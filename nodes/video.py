# SPDX-License-Identifier: Apache-2.0
"""Video nodes: temporally stable light tracks and keyframed light motion.

Both emit FLARE_LIGHTS — a list with one entry per frame, each entry a list
of {"u", "v", "brightness", optional "au"/"av" anchor, optional "tid"} —
which FlareRender consumes in place of its position_mode."""

import torch

from ..flare.colorspace import srgb_to_linear
from ..flare.detect import detect_lights
from ..flare.track import track_lights, interpolate_keyframes

_TRACK_COLORS = [
    (1.0, 0.71, 0.28),  # orange
    (0.37, 0.84, 1.0),  # cyan
    (0.62, 1.0, 0.47),  # green
    (1.0, 0.47, 0.62),  # pink
    (0.86, 0.67, 1.0),  # violet
    (1.0, 0.95, 0.45),  # yellow
]


# Spare candidates per frame for the tracker to choose from. Cheap (the
# detector already caps its search) and the whole point of the pool is that
# a busy frame still contains a candidate near the light we are following.
CANDIDATE_POOL_MIN = 12


# Tracking as a plain function. It used to be the Flare Track node; the
# render node does the same job in its track/lock modes now and reports the
# solved path back to the picker, so the separate node had nothing left to
# add. The pipeline stays here as a function for the tests that exercise it
# and for anyone scripting outside ComfyUI.
def track_clip(image, detect_threshold, detect_max_lights, smoothing,
          max_jump, hold_frames, fade_frames, search_radius=0.0,
          search_u=0.5, search_v=0.5):
    dtype = image.dtype if image.dtype.is_floating_point else torch.float32
    rgb = image[..., :3].to(dtype)
    image_linear = srgb_to_linear(rgb)

    # Detect a POOL of candidates, not just the number of flares wanted:
    # association picks the nearest candidate to each track, so having
    # spares is what keeps a light being followed through a frame where
    # some rival blob is momentarily the brightest thing on screen.
    pool = max(detect_max_lights * 8, CANDIDATE_POOL_MIN)
    raw = detect_lights(image_linear, threshold=detect_threshold,
                        max_lights=pool)
    dropped = 0
    if search_radius > 0.0:
        # u spans the width and v the height, so compare in units of
        # frame height (same convention as max_jump)
        aspect = image.shape[2] / max(image.shape[1], 1)
        kept = []
        for frame in raw:
            near = [d for d in frame
                    if ((d["u"] - search_u) * aspect) ** 2
                    + (d["v"] - search_v) ** 2 <= search_radius ** 2]
            dropped += len(frame) - len(near)
            kept.append(near)
        raw = kept
    tracked = track_lights(raw, smoothing=smoothing, max_jump=max_jump,
                           hold=hold_frames, fade=fade_frames,
                           max_tracks=detect_max_lights)

    overlay = _draw_overlay(
        rgb.clone(), tracked,
        roi=(search_u, search_v, search_radius) if search_radius > 0 else None)

    frames = len(tracked)
    tids = {light["tid"] for frame in tracked for light in frame}
    total = sum(len(frame) for frame in tracked)
    report = (f"{frames} frames, {len(tids)} tracks, "
              f"{total / max(frames, 1):.2f} lights/frame")
    if search_radius > 0.0:
        report += f"; search region rejected {dropped} candidates"
    if len(tids) > detect_max_lights:
        report += (f"; {len(tids)} different lights were followed over the "
                   f"clip — raise detect_threshold or set a search region "
                   f"if the flare moves between sources")
    return (tracked, overlay, report)

def _draw_overlay(rgb, tracked, roi=None):
    b, height, width, _ = rgb.shape
    arm = max(3, height // 72)
    if roi is not None:
        # dotted ring showing where the tracker is allowed to look
        cu, cv, rad = roi
        aspect = width / max(height, 1)
        ys = torch.linspace(0.0, 1.0, height, device=rgb.device, dtype=rgb.dtype)
        xs = torch.linspace(0.0, 1.0, width, device=rgb.device, dtype=rgb.dtype)
        gy, gx = torch.meshgrid(ys, xs, indexing="ij")
        d = torch.sqrt(((gx - cu) * aspect) ** 2 + (gy - cv) ** 2)
        ring = (d - rad).abs() < (1.5 / max(height, 1))
        dash = ((gx * width).long() + (gy * height).long()) % 12 < 6
        rgb[:, ring & dash] = torch.tensor(
            [0.25, 0.9, 1.0], device=rgb.device, dtype=rgb.dtype)
    for i in range(min(b, len(tracked))):
        for light in tracked[i]:
            color = torch.tensor(
                _TRACK_COLORS[light["tid"] % len(_TRACK_COLORS)],
                device=rgb.device, dtype=rgb.dtype)
            px = min(max(int(light["u"] * width), arm), width - 1 - arm)
            py = min(max(int(light["v"] * height), arm), height - 1 - arm)
            rgb[i, py - arm:py + arm + 1, px - 1:px + 2] = color
            rgb[i, py - 1:py + 2, px - arm:px + arm + 1] = color
    return rgb




class FlareKeyframes:
    CATEGORY = "flare"
    FUNCTION = "make"
    RETURN_TYPES = ("FLARE_LIGHTS", "INT")
    RETURN_NAMES = ("lights", "frame_count")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "frame_count": ("INT", {"default": 48, "min": 1, "max": 4096}),
                "light_keys": ("STRING", {
                    "multiline": True, "default": "0: 0.2, 0.3; 47: 0.8, 0.4",
                    "tooltip": "frame: u,v pairs separated by ';' — the light's path",
                }),
                "anchor_keys": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "optional flare-anchor path in the same format; empty keeps the node's flare_x/flare_y",
                }),
                "easing": (["smooth", "linear"],),
                "brightness": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "connect to take frame_count from the batch"}),
            },
        }

    def make(self, frame_count, light_keys, anchor_keys, easing, brightness,
             image=None):
        if image is not None:
            frame_count = image.shape[0]
        path = interpolate_keyframes(light_keys, frame_count, easing)
        anchors = None
        if anchor_keys.strip():
            anchors = interpolate_keyframes(anchor_keys, frame_count, easing)

        lights = []
        for f in range(frame_count):
            light = {"u": path[f][0], "v": path[f][1],
                     "brightness": brightness, "tid": 0}
            if anchors is not None:
                light["au"], light["av"] = anchors[f]
            lights.append([light])
        return (lights, frame_count)
