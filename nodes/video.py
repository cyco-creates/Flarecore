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


class FlareTrack:
    CATEGORY = "flare"
    FUNCTION = "track"
    RETURN_TYPES = ("FLARE_LIGHTS", "IMAGE", "STRING")
    RETURN_NAMES = ("lights", "overlay", "report")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "detect_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.01}),
                "detect_max_lights": ("INT", {"default": 1, "min": 1, "max": 8}),
                "smoothing": ("FLOAT", {
                    "default": 0.65, "min": 0.0, "max": 0.98, "step": 0.01,
                    "tooltip": "position/brightness EMA: 0 = raw detections, higher = stiller",
                }),
                "max_jump": ("FLOAT", {
                    "default": 0.12, "min": 0.01, "max": 1.0, "step": 0.01,
                    "tooltip": "max per-frame travel (fraction of height) that still continues a track",
                }),
                "hold_frames": ("INT", {
                    "default": 3, "min": 0, "max": 30,
                    "tooltip": "frames a track survives without a detection before fading",
                }),
                "fade_frames": ("INT", {
                    "default": 4, "min": 1, "max": 30,
                    "tooltip": "frames over which a light fades in when found and out when lost",
                }),
            },
        }

    def track(self, image, detect_threshold, detect_max_lights, smoothing,
              max_jump, hold_frames, fade_frames):
        dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        rgb = image[..., :3].to(dtype)
        image_linear = srgb_to_linear(rgb)

        raw = detect_lights(image_linear, threshold=detect_threshold,
                            max_lights=detect_max_lights)
        tracked = track_lights(raw, smoothing=smoothing, max_jump=max_jump,
                               hold=hold_frames, fade=fade_frames)

        overlay = self._draw_overlay(rgb.clone(), tracked)

        frames = len(tracked)
        tids = {light["tid"] for frame in tracked for light in frame}
        total = sum(len(frame) for frame in tracked)
        report = (f"{frames} frames, {len(tids)} tracks, "
                  f"{total / max(frames, 1):.2f} lights/frame")
        return (tracked, overlay, report)

    def _draw_overlay(self, rgb, tracked):
        b, height, width, _ = rgb.shape
        arm = max(3, height // 72)
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
