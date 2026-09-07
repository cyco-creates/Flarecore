# SPDX-License-Identifier: Apache-2.0
"""FlareRender: render a procedural lens flare over an image."""

import logging

import torch

from ..flare.colorspace import srgb_to_linear, linear_to_srgb
from ..flare.depth import blur_depth
from ..flare.depth import condition_depth, temporal_smooth_depth
from ..flare.detect import detect_lights, linear_luminance

# Blur applied to the above-floor luminance before per-frame detection picks
# a light, as a fraction of frame height. Big enough to turn a clipped sky
# into one hill, small enough to keep two genuinely separate sources apart.
DETECT_REGION_SIGMA = 0.02
from ..flare.track import (track_lights, parse_path, sample_path,
                           solve_light_path, smooth_series)
from ..flare.feature_track import track_points as track_points_in
from ..flare.feature_track import transform_from
from ..flare.scene_motion import estimate_motion, carry_point, scene_luma
from ..flare.track import _blend_toward_fit, _fill_gaps
from ..flare.engine import render_batch, composite
from ..flare.grid import uv_to_grid
from ..flare.occlude import occlusion_factor
from ..flare.schema import load_preset
from .library import resolve_preset_textures

# The default preset is the cinematic base look — the first render should
# already read as a real lens, not a placeholder dot.
from pathlib import Path as _Path

_FALLBACK_PRESET = '{"schema_version": 1, "elements": [{"type": "glow"}]}'
try:
    DEFAULT_PRESET = (_Path(__file__).resolve().parents[1] / "presets"
                      / "cine_blue.json").read_text(encoding="utf-8")
except OSError:
    DEFAULT_PRESET = _FALLBACK_PRESET

# Preview saving needs ComfyUI's temp dir; outside ComfyUI (tests, library
# use) the node runs headless. Probed once so a genuine save failure inside
# ComfyUI is logged instead of masquerading as "not in ComfyUI".
try:
    import folder_paths as _folder_paths
except ImportError:
    _folder_paths = None

_preview_error_logged = False

def _compute_device(home):
    """ComfyUI's own compute device (CUDA/MPS when present); outside ComfyUI,
    or when the device cannot be initialised, the renderer simply follows
    the input tensor. Imported lazily: importing model_management touches
    the CUDA runtime, which must not happen at module load."""
    try:
        from comfy import model_management as mm
        return mm.get_torch_device()
    except Exception:
        return home


def _engine_version() -> str:
    """Newest mtime across the rendering code, as a cache key.

    ComfyUI caches a node's result against its INPUTS. Change the engine and
    leave the graph alone -- exactly what happens while a look is being
    developed -- and the inputs are identical, so pressing Run replays the
    old render in 0.00s and the fix appears not to have worked. Folding the
    code's own timestamp into IS_CHANGED makes an edit invalidate the cache
    the same way turning a knob does, and costs a handful of stat calls.
    """
    root = _Path(__file__).resolve().parents[1]
    newest = 0.0
    for sub in ("flare", "nodes"):
        for f in (root / sub).glob("*.py"):
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                pass
    return f"{newest:.3f}"


def restrict_to_region(detections, cu, cv, radius, aspect):
    """Drop candidates outside a circle around (cu, cv).

    u spans the width and v the height, so the distance is measured in
    units of frame height (the same convention as max_jump) by scaling the
    horizontal offset by the aspect.
    """
    out = []
    for frame in detections:
        out.append([d for d in frame
                    if ((d["u"] - cu) * aspect) ** 2 + (d["v"] - cv) ** 2
                    <= radius ** 2])
    return out


# How far (fraction of frame height) a detection may pull the light away
# from the camera-carried path at full scene lock; the limit opens up as
# the lock is released, until at 0 the anchoring is not applied at all.
ANCHOR_MAX_DEVIATION = 0.03


def _median5(values):
    """Five-tap running median; the ends use what is available."""
    n = len(values)
    out = []
    for i in range(n):
        win = sorted(values[max(0, i - 2):min(n, i + 3)])
        out.append(win[len(win) // 2])
    return out


def _anchor_to_scene(lights_per_frame, luma, amount, lock=1.0):
    """Hold every detected light to the way the picture moves.

    A detector says where the light is in each frame on its own, and on
    real footage that answer wobbles: the visible part of a sun changes as
    branches cross it, a clipped sky has no centre, a rival source wins a
    frame. The camera's motion, read from the whole picture, says where the
    light MUST have gone between frames. So each light is carried from its
    most confident frame by the scene's motion, and only the residual -- how
    far the detector disagrees with that -- is smoothed. At `amount` 0 the
    detections stand; at 1 the residual is a fitted curve and the light
    rides the camera. Frames that had no light get none: hold and fade are
    the tracker's business, this only moves lights that exist.
    """
    frames = len(lights_per_frame)
    if frames < 3:
        return lights_per_frame
    by_key: dict = {}
    for i, frame in enumerate(lights_per_frame):
        for slot, light in enumerate(frame):
            by_key.setdefault(light.get("tid", slot), []).append((i, slot, light))
    height, width = int(luma.shape[1]), int(luma.shape[2])
    out = [[dict(l) for l in frame] for frame in lights_per_frame]
    for key, entries in by_key.items():
        if len(entries) < 3:
            continue
        # the reference: the frame this light was seen most confidently
        ref_i, _, ref = max(entries, key=lambda e: (e[2].get("brightness", 1.0)
                                                    * (1.0 - e[2].get("occlusion", 0.0))))
        stats: dict = {}
        motions = estimate_motion(anchor_uv=(ref["u"], ref["v"]), model="rigid",
                                  luma=luma, stats=stats)
        # A matte, a black plate, a featureless sky: nothing to anchor to.
        # The light's own motion is then all there is, and must stand.
        if stats.get("solved_frames", 0) < 0.5 * max(frames - 1, 1):
            continue
        carried = carry_point(ref["u"], ref["v"], motions, height, width, start=ref_i)
        res_u = [None] * frames
        res_v = [None] * frames
        for i, slot, l in entries:
            res_u[i] = l["u"] - carried[i][0]
            res_v[i] = l["v"] - carried[i][1]
        # gaps take the nearest known residual, then the residual is
        # smoothed and pulled toward a fitted curve by `amount`
        path = [(res_u[i], res_v[i], 0.0) if res_u[i] is not None else None
                for i in range(frames)]
        _fill_gaps(path)
        # A detector that hops to a rival for a frame or two leaves a spike
        # in the residual; a low-pass spreads a spike, a median removes it.
        ru = _median5([p[0] for p in path])
        rv = _median5([p[1] for p in path])
        # The camera says where the light went; the detector may only
        # disagree by so much. A hop to a rival source is a disagreement of
        # a third of the frame, so however long it lasts it is clipped to a
        # few percent and cannot drag the light. Genuine slow drift -- the
        # visible part of a sun changing as branches cross it -- is small
        # and passes through.
        limit = ANCHOR_MAX_DEVIATION + (1.0 - lock) * 0.6
        mu = sorted(ru)[len(ru) // 2]; mv = sorted(rv)[len(rv) // 2]
        ru = [mu + max(-limit, min(limit, r - mu)) for r in ru]
        rv = [mv + max(-limit, min(limit, r - mv)) for r in rv]
        ru = _blend_toward_fit(smooth_series(ru, min(amount, 0.6)), amount * lock)
        rv = _blend_toward_fit(smooth_series(rv, min(amount, 0.6)), amount * lock)
        for i, slot, l in entries:
            moved = out[i][slot]
            moved["u"] = carried[i][0] + ru[i]
            moved["v"] = carried[i][1] + rv[i]
    return out


def _damp_travel(lights_per_frame, amount):
    """Scale each light's excursion about its own average position.

    1 leaves the path alone; 0 pins every light to the mean of its own path,
    so the flare holds still for the whole clip. Each track is damped about
    ITS OWN centre -- with several dots on a matte, pulling them all toward
    one shared point would collapse them together.
    """
    if amount >= 1.0 or not lights_per_frame:
        return lights_per_frame
    k = min(max(amount, 0.0), 1.0)
    # A light keeps its identity through "tid" when tracking assigned one,
    # and otherwise by its slot in the frame's list.
    # The anchor is damped about ITS OWN mean, not the light's: pulled
    # toward the light's centre, travel 0 would put both points in one
    # place and collapse the flare axis to nothing.
    sums: dict = {}
    for frame in lights_per_frame:
        for slot, light in enumerate(frame):
            key = light.get("tid", slot)
            u, v, au, av, n, na = sums.get(key, (0.0, 0.0, 0.0, 0.0, 0, 0))
            u += light["u"]; v += light["v"]; n += 1
            if "au" in light and "av" in light:
                au += light["au"]; av += light["av"]; na += 1
            sums[key] = (u, v, au, av, n, na)
    centre = {key: ((u / n, v / n), ((au / na, av / na) if na else None))
              for key, (u, v, au, av, n, na) in sums.items() if n}
    out = []
    for frame in lights_per_frame:
        damped = []
        for slot, light in enumerate(frame):
            (cu, cv), anchor_c = centre.get(
                light.get("tid", slot), ((light["u"], light["v"]), None))
            moved = dict(light)
            moved["u"] = cu + (light["u"] - cu) * k
            moved["v"] = cv + (light["v"] - cv) * k
            if "au" in light and "av" in light and anchor_c is not None:
                acu, acv = anchor_c
                moved["au"] = acu + (light["au"] - acu) * k
                moved["av"] = acv + (light["av"] - acv) * k
            damped.append(moved)
        out.append(damped)
    return out


class FlareRender:
    CATEGORY = "flare"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK")
    RETURN_NAMES = ("image", "flare_pass", "flare_alpha")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return _engine_version()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "preset_json": ("STRING", {"multiline": True, "default": DEFAULT_PRESET}),
                "position_mode": ([
                    "manual", "detect", "detect_with_manual_offset",
                    "track", "track_dots", "path", "lock", "point_track",
                    "follow",
                ], {
                    "tooltip": "manual: the picker's light point. detect: the "
                               "brightest spot, per frame. track: the same but "
                               "followed through the clip. track_dots: every "
                               "bright dot on a dark matte gets its own flare. "
                               "path: follow the path drawn on the picker. "
                               "lock: solve the whole clip at once so the "
                               "light cannot teleport between rival sources. "
                               "point_track: follow one or two features you "
                               "place on the picker, the way a compositor's "
                               "point tracker does -- with two, the flare "
                               "axis takes their rotation and scale too. "
                               "follow: place the light where the source "
                               "really is -- outside the frame if it is -- "
                               "and it is carried by the camera's motion, "
                               "read from the whole picture. Nothing is "
                               "detected, so nothing can be lost.",
                }),
                "light_x": ("FLOAT", {"default": 0.25, "min": -1.0, "max": 2.0, "step": 0.001}),
                "light_y": ("FLOAT", {"default": 0.3, "min": -1.0, "max": 2.0, "step": 0.001}),
                "flare_x": ("FLOAT", {"default": 0.5, "min": -1.0, "max": 2.0, "step": 0.001}),
                "flare_y": ("FLOAT", {"default": 0.5, "min": -1.0, "max": 2.0, "step": 0.001}),
                "detect_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.01}),
                "detect_max_lights": ("INT", {"default": 1, "min": 1, "max": 16}),
                "occlusion_radius": ("FLOAT", {"default": 0.02, "min": 0.001, "max": 0.5, "step": 0.001}),
                "light_depth": ("FLOAT", {
                    "default": 0.1, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "the light's own depth on the map's scale; 0 "
                               "means at infinity. An occluder counts only if "
                               "it reads at least 0.1 nearer than this, so on "
                               "a normalised map whose sky is not exactly 0 "
                               "the sun starts occluding ITSELF. Measured on "
                               "a sun-through-trees shot: at 0 the flare went "
                               "fully dark in 7 frames of 60, at 0.1 in 1. "
                               "Raise it until the flare stops blinking in "
                               "clear sky.",
                }),
                "invert_depth": ("BOOLEAN", {"default": False}),
                "intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "scale": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 10.0, "step": 0.01}),
                "blend_mode": (["add", "screen"],),
                "clamp_output": ("BOOLEAN", {"default": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                # appended last so widgets_values in saved workflows stay aligned
                "occlusion_smooth": ("FLOAT", {
                    "default": 0.4, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "temporal smoothing of occlusion per tracked "
                               "light across the batch; turns one-frame "
                               "occlusion cuts into fades (video)",
                }),
                "scene_color": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "tint each light's flare by the plate colour "
                               "at the light (0 = preset colours only, 1 = "
                               "fully takes the source's colour)",
                }),
                "track_smoothing": ("FLOAT", {
                    "default": 0.6, "min": 0.0, "max": 0.98, "step": 0.01,
                    "tooltip": "track/track_dots: position smoothing.",
                }),
                "track_max_jump": ("FLOAT", {
                    "default": 0.10, "min": 0.01, "max": 1.0, "step": 0.01,
                    "tooltip": "track/track_dots: how far a light may travel "
                               "between frames (fraction of height). Also the "
                               "gate that stops a flare hopping onto a rival "
                               "light — lower it if the flare wanders.",
                }),
                "depth_normalize": (["as_is", "per_batch", "per_frame"], {
                    "tooltip": "condition a RAW depth map here instead of "
                               "wiring a Flare Depth Adapter. Leave as_is for "
                               "a map that is already conditioned: "
                               "normalising rescales the map, and light_depth "
                               "is measured on its scale. per_batch is the "
                               "one to use for video.",
                }),
                "depth_blur": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 0.2, "step": 0.001,
                    "tooltip": "softens the connected depth map's edges so "
                               "occlusion fades instead of stepping.",
                }),
                "depth_temporal_smooth": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 0.95, "step": 0.01,
                    "tooltip": "stills per-frame depth-model shimmer across "
                               "the batch (video).",
                }),
                "light_path": ("STRING", {
                    "default": "",
                    "tooltip": "path mode: 'u,v; u,v; ...' — drawn with the "
                               "picker's path tool, not typed.",
                }),
                "mask_falloff": ("FLOAT", {
                    "default": 0.35, "min": 0.02, "max": 2.0, "step": 0.01,
                    "tooltip": "how far a light's glow reaches when it lights "
                               "elements that use light_mask (lens dirt, "
                               "bloom). Fraction of frame height; smaller "
                               "means the dirt only shows in a tight pool "
                               "around the source.",
                }),
                "colorspace": (["srgb", "linear"], {
                    "tooltip": "what the incoming pixels are. srgb: ordinary "
                               "images and video (decoded to linear inside, "
                               "re-encoded on output). linear: footage that "
                               "is already scene-linear — EXR plates, render "
                               "passes — passed through untouched, so a "
                               "Nuke/Resolve round trip stays correct.",
                }),
                "chunk_frames": ("INT", {
                    "default": 0, "min": 0, "max": 512,
                    "tooltip": "frames rendered per GPU slice. 0 sizes the "
                               "slice from free VRAM, so a long clip streams "
                               "through instead of loading whole onto the "
                               "card — results are identical either way.",
                }),
                "light_travel": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "how much the light is allowed to move. 1 "
                               "follows the detected or tracked path exactly; "
                               "0 pins it to one spot for the whole clip and "
                               "the flare stops moving altogether. In between "
                               "it keeps the same path with the excursion "
                               "scaled down, so a source that should barely "
                               "drift can be calmed without losing its shape.",
                }),
                "track_points": ("STRING", {
                    "default": "",
                    "tooltip": "point_track: 'u,v' for one point or "
                               "'u,v; u,v' for two, placed on the picker "
                               "rather than typed. The first drives the "
                               "light; a second drives the flare anchor.",
                }),
                "track_feature": ("INT", {
                    "default": 32, "min": 8, "max": 128, "step": 2,
                    "tooltip": "size of the feature region in pixels — the "
                               "patch being matched. Big enough to contain "
                               "something distinctive, small enough that it "
                               "does not change shape as the shot moves.",
                }),
                "track_search": ("INT", {
                    "default": 64, "min": 8, "max": 256, "step": 2,
                    "tooltip": "how far from the predicted position to look, "
                               "in pixels. This is the tracker's speed "
                               "limit: raise it for fast motion, lower it to "
                               "stop it finding lookalikes further away.",
                }),
                "track_hold": ("INT", {
                    "default": 3, "min": 0, "max": 60,
                    "tooltip": "frames a lost light keeps its flare at full "
                               "strength before fading -- a thin occluder "
                               "becomes a flicker-free pass instead of a cut.",
                }),
                "track_fade": ("INT", {
                    "default": 4, "min": 1, "max": 60,
                    "tooltip": "frames a light takes to fade in when found "
                               "and out when lost.",
                }),
                "search_radius": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "track/lock: only look for the light within "
                               "this distance of the picker's light point "
                               "(fraction of frame height). 0 searches the "
                               "whole frame. Set it when a rival source "
                               "elsewhere keeps stealing the flare.",
                }),
                "scene_lock": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "detect/track/lock: how much the light is held "
                               "to the way the picture moves. A sun is at "
                               "infinity and moves only with the camera, so "
                               "at 1 the detector may only nudge it a little "
                               "and a hop to a rival source cannot drag it. "
                               "Set 0 for a light that moves on its own -- "
                               "headlights crossing frame, a torch -- so the "
                               "detector's motion is kept in full. A matte "
                               "with no scene in it is left alone either way.",
                }),
                "anchor_path": ("STRING", {
                    "default": "",
                    "tooltip": "path mode: the flare anchor's own 'u,v; u,v' "
                               "path. Bake to path writes it from a "
                               "two-tracker solve so the axis keeps the "
                               "pair's rotation and scale. Empty: the anchor "
                               "stays at flare_x/flare_y.",
                }),
            },
            "optional": {
                "depth": ("IMAGE",),
                "lights": ("FLARE_LIGHTS", {
                    "tooltip": "Per-frame lights from Flare Track or Flare "
                               "Keyframes; overrides position_mode when connected.",
                }),
            },
        }

    def render(self, image, preset_json, position_mode, light_x, light_y,
               flare_x, flare_y, detect_threshold, detect_max_lights,
               occlusion_radius, light_depth, invert_depth, intensity, scale,
               blend_mode, clamp_output, seed, occlusion_smooth=0.4,
               scene_color=0.0, track_smoothing=0.6, track_max_jump=0.10,
               depth_normalize="as_is", depth_blur=0.0,
               depth_temporal_smooth=0.0, light_path="", mask_falloff=0.35,
               colorspace="srgb", chunk_frames=0, light_travel=1.0, track_points="", track_feature=32,
               track_search=48, track_hold=3, track_fade=4,
               search_radius=0.0, scene_lock=1.0, anchor_path="",
               depth=None, lights=None):
        preset = load_preset(preset_json)

        # ComfyUI passes IMAGE tensors on the CPU regardless of where they
        # were made, so "follow the input" would pin the whole renderer to
        # the CPU (about 30x slower at 1080p). Render on the compute device
        # ComfyUI itself uses and hand the results back on the input's.
        home = image.device
        device = _compute_device(home)
        output_dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        # Half precision quantizes the coordinate grid and faint HDR tails.
        # Keep float64 callers intact; promote half/bfloat16 for all math,
        # including texture sampling, then cast only into output buffers.
        dtype = torch.float64 if output_dtype == torch.float64 else torch.float32
        resolve_preset_textures(preset, device=device, dtype=dtype)

        batch, height, width = image.shape[0], image.shape[1], image.shape[2]

        # The engine always works in linear light; `colorspace` says whether
        # the pixels arrive encoded. Linear plates (EXR, render passes) skip
        # both the decode here and the encode at the end.
        is_linear = colorspace == "linear"

        # A whole clip does not fit on the card: the working set peaked at
        # 24.5 GB for 96 frames of 1080p when the batch flowed through in
        # one piece, which kills 300-frame clips on ANY card. Frames stream
        # through in slices instead; everything that needs full-clip context
        # (tracking, occlusion smoothing, temporal depth) works on small
        # per-frame values that live on the CPU.
        chunk = self._chunk_size(chunk_frames, batch, height, width, device)

        def linear_chunk(start, stop):
            rgb = image[start:stop, ..., :3].to(device=device, dtype=dtype)
            return rgb if is_linear else srgb_to_linear(rgb)

        if lights is not None:
            light_source = "lights input"
            lights_per_frame = self._lights_from_input(lights, batch)
        else:
            light_source = position_mode
            lights_per_frame = self._resolve_lights(
                linear_chunk, batch, chunk, position_mode, light_x, light_y,
                detect_threshold, detect_max_lights,
                smoothing=track_smoothing, max_jump=track_max_jump,
                light_path=light_path, track_points=track_points,
                track_feature=track_feature, track_search=track_search,
                hold=track_hold, fade=track_fade, search_radius=search_radius,
                anchor_path=anchor_path,
            )

        if (lights is None and batch > 2 and scene_lock > 0.0
                and position_mode in ("detect", "detect_with_manual_offset",
                                      "track", "track_dots", "lock")):
            luma = torch.cat([scene_luma(linear_chunk(s, min(s + chunk, batch)))
                              for s in range(0, batch, chunk)], dim=0)
            lights_per_frame = _anchor_to_scene(lights_per_frame, luma,
                                                track_smoothing, scene_lock)
            del luma
        lights_per_frame = _damp_travel(lights_per_frame, light_travel)

        if depth is not None:
            bd = depth.shape[0]
            if 1 < bd < batch:
                raise ValueError(
                    f"depth batch ({bd}) is shorter than the image batch "
                    f"({batch}); pass one depth map to reuse it for every "
                    f"frame, or one per frame"
                )
            # Conditioned per slice on the device, then parked on the CPU:
            # occlusion only samples a handful of points per light, and the
            # temporal smooth is a cheap EMA, so neither needs the GPU. The
            # per-batch range is gathered first so slicing cannot change
            # what "the batch's min and max" means.
            norm = "none" if depth_normalize == "as_is" else depth_normalize
            lo = hi = None
            if norm == "per_batch":
                lo = torch.tensor(float("inf"))
                hi = torch.tensor(float("-inf"))
                for s in range(0, bd, chunk):
                    dm = depth[s:s + chunk, ..., :3].to(device=device,
                                                        dtype=dtype).mean(dim=-1)
                    lo = torch.minimum(lo, dm.amin().cpu())
                    hi = torch.maximum(hi, dm.amax().cpu())
            # Shaped from the DEPTH, not the image: depth models return
            # their own resolution (Depth Anything gives 512x910 for a
            # 720x1280 clip) and occlusion samples the map in normalised
            # u,v, so the two never have to agree.
            depth_cpu = torch.empty(bd, int(depth.shape[1]), int(depth.shape[2]),
                                    dtype=dtype)
            for s in range(0, bd, chunk):
                dm = depth[s:s + chunk, ..., :3].to(device=device,
                                                    dtype=dtype).mean(dim=-1)
                if norm == "per_batch":
                    span = (hi - lo).clamp(min=1e-6).to(dm.device, dm.dtype)
                    dm = ((dm - lo.to(dm.device, dm.dtype)) / span).clamp(0.0, 1.0)
                dm = condition_depth(
                    dm, normalize="per_frame" if norm == "per_frame" else "none",
                    invert=invert_depth, blur=depth_blur)
                depth_cpu[s:s + chunk] = dm.to("cpu")
            if depth_temporal_smooth > 0.0 and bd > 1:
                depth_cpu = temporal_smooth_depth(depth_cpu, depth_temporal_smooth)
            for i, frame_lights in enumerate(lights_per_frame):
                dmap = depth_cpu[0 if bd == 1 else i]
                for light in frame_lights:
                    light["occlusion"] = occlusion_factor(
                        dmap, light["u"], light["v"],
                        radius=occlusion_radius, invert=False,
                        light_depth=light_depth,
                    )
            self._smooth_occlusion(lights_per_frame, occlusion_smooth)

        # The flare anchor (t = 1) is a second free point: element spacing
        # scales with the light-to-anchor distance. Lights supplied through
        # the FLARE_LIGHTS input may carry their own per-frame anchor.
        default_anchor = uv_to_grid(flare_x, flare_y, height, width)

        engine_lights = []
        for fi, frame_lights in enumerate(lights_per_frame):
            frame = []
            for k, light in enumerate(frame_lights):
                x, y = uv_to_grid(light["u"], light["v"], height, width)
                if "au" in light and "av" in light:
                    ax, ay = uv_to_grid(light["au"], light["av"], height, width)
                else:
                    ax, ay = default_anchor
                entry = {
                    "x": x, "y": y, "ax": ax, "ay": ay,
                    "u": light["u"], "v": light["v"],
                    "brightness": light.get("brightness", 1.0),
                    "occlusion": light.get("occlusion", 0.0),
                    # tracked lights keep their id so flicker stays per-lamp
                    "index": int(light.get("tid", k)),
                }
                frame.append(entry)
            engine_lights.append(frame)

        needs_mask = any(e.get("light_mask", 0.0) > 0.0
                         for e in preset["elements"])

        # Results accumulate where the input lives (CPU for ComfyUI), so the
        # card only ever holds one slice of the clip.
        out_full = torch.empty(batch, height, width, 3, dtype=output_dtype, device=home)
        pass_full = torch.empty_like(out_full)
        alpha_full = torch.empty(batch, height, width, dtype=output_dtype, device=home)

        for s in range(0, batch, chunk):
            e = min(s + chunk, batch)
            chunk_linear = linear_chunk(s, e)
            chunk_lights = engine_lights[s:e]

            if scene_color > 0.0:
                for fi, frame_lights in enumerate(chunk_lights):
                    for light in frame_lights:
                        light["color"] = _scene_light_color(
                            chunk_linear[fi], light["u"], light["v"],
                            scene_color)

            # Elements with light_mask appear where the light is: scene
            # luminance combined with a radial falloff around each light
            # (dirt on a lens is lit by the source itself, even over a
            # black plate). Only built when the preset uses it.
            scene_masks = None
            glow_masks = None
            if needs_mask:
                mask = linear_luminance(chunk_linear).clamp(0.0, 1.0)
                # The light's own pool, kept apart from the scene's
                # luminance: an element may want to be revealed by the
                # source alone. Scene luminance changes every frame, so a
                # plate masked by it appears to crawl even though the plate
                # itself never moves.
                glow_only = torch.zeros_like(mask)
                yy = torch.linspace(0.0, 1.0, height, device=device, dtype=dtype)
                xx = torch.linspace(0.0, 1.0, width, device=device, dtype=dtype)
                gy, gx = torch.meshgrid(yy, xx, indexing="ij")
                aspect_px = width / height
                falloff_r = max(mask_falloff, 1e-3)  # of frame height
                for i, frame_lights in enumerate(chunk_lights):
                    for light in frame_lights:
                        w = light.get("brightness", 1.0) * \
                            (1.0 - light.get("occlusion", 0.0))
                        if w <= 0.0:
                            continue
                        d2 = ((gx - light["u"]) * aspect_px) ** 2 + \
                             (gy - light["v"]) ** 2
                        glowm = torch.exp(-d2 / (2.0 * falloff_r ** 2)) * min(w, 1.0)
                        mask[i] = torch.maximum(mask[i], glowm)
                        glow_only[i] = torch.maximum(glow_only[i], glowm)
                scene_masks = blur_depth(mask, 0.02)
                glow_masks = blur_depth(glow_only, 0.02)

            flare_linear = render_batch(
                preset, chunk_lights, height, width, device, dtype,
                extra_seed=seed, intensity=intensity, scale=scale,
                scene_masks=scene_masks, glow_masks=glow_masks, frame_offset=s,
            )

            out_linear = composite(chunk_linear, flare_linear, blend_mode)
            out_c = out_linear if is_linear else linear_to_srgb(out_linear)
            pass_c = flare_linear if is_linear else linear_to_srgb(flare_linear)
            if clamp_output:
                out_c = out_c.clamp_(0.0, 1.0)
                pass_c = pass_c.clamp_(0.0, 1.0)
            out_full[s:e] = out_c.to(home)
            pass_full[s:e] = pass_c.to(home)
            alpha_full[s:e] = linear_luminance(pass_c).clamp(0.0, 1.0).to(home)
            del chunk_linear, flare_linear, out_linear, out_c, pass_c, scene_masks
            del glow_masks

        result = (out_full, pass_full, alpha_full)
        out = out_full
        if _folder_paths is None:
            return result
        # The picker backdrop is the COMPOSITE — the point of the panel is
        # previewing the flare while positioning it. It rides a custom ui
        # key so ComfyUI does not also paint a preview image under the node.
        # fc_light_src tells the editor which control actually placed the
        # light this run: a connected lights input silently overrides
        # light_x/light_y, and without this the picker looks broken.
        # The path the light actually took, so the picker can show it and
        # the owner can bake it into an editable motion path. Primary light
        # only; the anchor comes along when a mode produced one.
        fc_track = []
        for frame in lights_per_frame:
            if frame:
                l = frame[0]
                fc_track.append([round(float(l["u"]), 4), round(float(l["v"]), 4),
                                 round(float(l["au"]), 4) if "au" in l else None,
                                 round(float(l["av"]), 4) if "av" in l else None])
            else:
                fc_track.append(None)
        return {"ui": {"fc_preview": _save_preview(out[0])["images"],
                       "fc_light_src": [light_source],
                       "fc_track": [fc_track]},
                "result": result}

    def _chunk_size(self, requested, batch, height, width, device):
        """Frames per GPU slice. Explicit request wins; 0 sizes the slice so
        the working set (measured near 10x the frames in flight, blur
        temporaries included) stays a modest share of free VRAM."""
        if requested and int(requested) > 0:
            return max(1, min(int(requested), batch))
        frame_bytes = height * width * 3 * 4
        if getattr(device, "type", str(device)) == "cuda":
            try:
                free, _ = torch.cuda.mem_get_info(device)
                budget = min(free * 0.5, 6e9)
            except Exception:
                budget = 4e9
        else:
            budget = 8e9
        return max(1, min(batch, int(budget // (frame_bytes * 10)) or 1))

    def _smooth_occlusion(self, lights_per_frame, amount):
        """Low-pass each tracked light's occlusion series along the batch.

        A detector can pin to a halo sliver beside a thin occluder and then
        snap across it, which turns the occlusion into a one-frame cut; the
        stable track ids from FlareTrack let the cut be spread into a fade.
        Lights without a tid (manual, detect) are left untouched.
        """
        if amount <= 0.0 or len(lights_per_frame) < 2:
            return
        from ..flare.track import smooth_series
        series: dict = {}
        for i, frame_lights in enumerate(lights_per_frame):
            for light in frame_lights:
                tid = light.get("tid")
                if tid is not None:
                    series.setdefault(tid, []).append((i, light))
        for entries in series.values():
            if len(entries) < 2:
                continue
            smoothed = smooth_series([l["occlusion"] for _, l in entries], amount)
            for (_, light), occ in zip(entries, smoothed):
                light["occlusion"] = occ

    def _lights_from_input(self, lights, batch):
        """Adapt a FLARE_LIGHTS object (list per frame of light dicts) to
        this batch: a single frame of lights broadcasts, otherwise the
        sequence must cover the batch."""
        if not isinstance(lights, list) or (lights and not isinstance(lights[0], list)):
            raise ValueError("lights input must be per-frame lists (FLARE_LIGHTS)")
        n = len(lights)
        if n == 0:
            return [[] for _ in range(batch)]
        if 1 < n < batch:
            raise ValueError(
                f"lights input covers {n} frames but the image batch has "
                f"{batch}; produce one entry per frame (or a single frame "
                f"to broadcast)"
            )
        return [[dict(light) for light in lights[0 if n == 1 else i]]
                for i in range(batch)]

    def _resolve_lights(self, linear_chunk, batch, chunk, position_mode,
                        light_x, light_y, detect_threshold, detect_max_lights,
                        smoothing=0.6, max_jump=0.10, light_path="",
                        track_points="", track_feature=32, track_search=48,
                        hold=3, fade=4, search_radius=0.0, anchor_path=""):
        """linear_chunk(start, stop) hands back that slice of the clip in
        linear light on the compute device — pixels are only touched a slice
        at a time, matching the streamed render pass."""
        if position_mode == "manual":
            return [[{"u": light_x, "v": light_y, "brightness": 1.0}]
                    for _ in range(batch)]

        if position_mode == "path":
            points = parse_path(light_path)
            if not points:
                raise ValueError(
                    "position_mode is 'path' but no path is drawn; use the "
                    "picker's path tool, or switch to manual"
                )
            frames = [[{"u": u, "v": v, "brightness": 1.0}]
                      for u, v in sample_path(points, batch)]
            # a baked two-tracker solve carries the anchor on its own path,
            # so the axis keeps the pair's rotation and scale
            anchors = parse_path(anchor_path)
            if anchors:
                for frame, (au, av) in zip(frames, sample_path(anchors, batch)):
                    frame[0]["au"] = au
                    frame[0]["av"] = av
            return frames

        probe = linear_chunk(0, 1)
        frame_aspect = probe.shape[2] / max(probe.shape[1], 1)
        del probe

        def detect_chunked(threshold, pool, region_sigma=0.0):
            dets = []
            for s in range(0, batch, chunk):
                dets += detect_lights(linear_chunk(s, min(s + chunk, batch)),
                                      threshold=threshold, max_lights=pool,
                                      region_sigma=region_sigma)
            # A search region, centred on the picker's light point: the one
            # place in the UI that already means "the light is about here".
            # Candidates outside it never reach the tracker, so a rival
            # source across frame cannot steal the flare however bright.
            if search_radius > 0.0:
                dets = restrict_to_region(dets, light_x, light_y,
                                          search_radius, frame_aspect)
            return dets

        if position_mode == "follow":
            # The light is where the picker says, in frame 0, and it moves
            # the way the picture moves. Detection never enters into it, so
            # an off-frame sun, a clipped sky and a canopy full of branches
            # cannot touch it; a rigid fit keeps forward travel from reading
            # as a zoom that would push an off-frame light further out.
            luma = torch.cat([scene_luma(linear_chunk(s, min(s + chunk, batch)))
                              for s in range(0, batch, chunk)], dim=0)
            motions = estimate_motion(anchor_uv=(light_x, light_y),
                                      model="rigid", luma=luma)
            path = carry_point(light_x, light_y, motions,
                               luma.shape[1], luma.shape[2])
            us = smooth_series([p[0] for p in path], min(smoothing, 0.6))
            vs = smooth_series([p[1] for p in path], min(smoothing, 0.6))
            return [[{"u": u, "v": v, "brightness": 1.0, "tid": 0}]
                    for u, v in zip(us, vs)]

        if position_mode == "point_track":
            # Feature tracking needs whole frames, not the light's position,
            # so the clip is pulled in slices exactly like the detectors.
            pts = parse_path(track_points)
            if not pts:
                raise ValueError(
                    "point_track needs at least one point: click the picker "
                    "to place a tracker on the feature to follow"
                )
            clip = torch.cat([linear_chunk(s, min(s + chunk, batch))
                              for s in range(0, batch, chunk)], dim=0)
            tracked = track_points_in(clip, pts[:2], feature=track_feature,
                                      search=track_search)
            # sub-pixel tracks carry sub-pixel jitter, and a flare axis
            # amplifies it: the same zero-phase smooth the light tracker uses
            for tr in tracked:
                us = smooth_series([q["u"] for q in tr], smoothing)
                vs = smooth_series([q["v"] for q in tr], smoothing)
                for q, uu, vv in zip(tr, us, vs):
                    q["u"], q["v"] = uu, vv
            if len(tracked) == 1:
                return [[{"u": p["u"], "v": p["v"], "brightness": 1.0,
                          "tid": 0}] for p in tracked[0]]
            # two points: the second becomes the flare's anchor, so the axis
            # inherits the pair's rotation and scale without any extra maths
            return [[{"u": f["u"], "v": f["v"], "au": f["au"], "av": f["av"],
                      "brightness": 1.0, "tid": 0}]
                    for f in transform_from(tracked[0], tracked[1])]

        if position_mode == "lock":
            # Solve the clip as one problem: the trajectory that explains
            # every frame with the least total travel. A single bad frame
            # cannot hand the light to a rival across the frame, which is
            # what per-frame detection does on a shot with two bright
            # regions. max_jump keeps its meaning -- how far the light may
            # travel between frames -- and sets how dearly travel is paid
            # for, so there is no new dial to learn.
            pool = max(detect_max_lights * 8, 12)
            raw = detect_chunked(detect_threshold, pool)
            cost = 4.0 / max(max_jump, 1e-3) ** 2
            return solve_light_path(raw, max_tracks=detect_max_lights,
                                    motion_cost=cost, smoothing=smoothing)

        if position_mode in ("track", "track_dots"):
            threshold = detect_threshold
            if position_mode == "track_dots":
                # A matte's dots are whatever white the render happened to
                # produce (this one peaks at 0.92 sRGB, not 1.0) over a
                # not-quite-black ground, so an absolute threshold is the
                # wrong tool: take it relative to the brightest thing in the
                # CLIP — gathered across slices first, so slicing cannot
                # change what "the brightest" means.
                peak = 0.0
                for s in range(0, batch, chunk):
                    peak = max(peak, float(linear_chunk(s, min(s + chunk, batch)).amax()))
                threshold = max(peak * max(detect_threshold, 0.05), 1e-4)
            # more candidates than flares: association picks the nearest, so
            # spares keep a followed light fed through a busy frame
            pool = max(detect_max_lights * 8, 12)
            raw = detect_chunked(threshold, pool)
            return track_lights(raw, smoothing=smoothing, max_jump=max_jump,
                                hold=hold, fade=fade,
                                max_tracks=detect_max_lights)

        # Per-frame detection picks by REGION, not by brightest pixel: a
        # blown-out sky is a plateau where the brightest pixel is an
        # arbitrary tie-break that moves every frame. Tracking deliberately
        # does not do this -- it wants the fine-grained pool above.
        detected = detect_chunked(detect_threshold, detect_max_lights,
                                  region_sigma=DETECT_REGION_SIGMA)
        if position_mode == "detect":
            return detected
        if position_mode == "detect_with_manual_offset":
            du, dv = light_x - 0.5, light_y - 0.5
            return [
                [{**light, "u": light["u"] + du, "v": light["v"] + dv}
                 for light in lights]
                for lights in detected
            ]
        raise ValueError(f"unknown position_mode {position_mode!r}")


def _scene_light_color(plate_linear, u, v, strength, radius=0.03):
    """Chromaticity of the plate around the light, as an (r, g, b) multiplier
    with unit luminance, blended toward neutral by 1 - strength. A pure
    grey source returns (1, 1, 1); an orange sunset sun returns a warm tint
    that the whole flare then takes on."""
    h, w = plate_linear.shape[:2]
    r = max(int(radius * h), 1)
    cy, cx = int(v * (h - 1)), int(u * (w - 1))
    # Both ends clamped: a light above the frame has a negative cy, and a
    # negative slice END wraps to nearly the whole plate.
    y0, y1 = max(cy - r, 0), min(cy + r + 1, h)
    x0, x1 = max(cx - r, 0), min(cx + r + 1, w)
    if y1 <= y0 or x1 <= x0:
        return [1.0, 1.0, 1.0]
    patch = plate_linear[y0:y1, x0:x1, :3]
    lum = linear_luminance(patch)
    # weight by brightness so the source dominates over its surroundings
    wsum = lum.sum()
    if wsum <= 1e-6:
        return [1.0, 1.0, 1.0]
    mean = (patch * lum.unsqueeze(-1)).sum(dim=(0, 1)) / wsum
    mean_lum = float(linear_luminance(mean.view(1, 1, 3))[0, 0])
    if mean_lum <= 1e-6:
        return [1.0, 1.0, 1.0]
    chroma = (mean / mean_lum).clamp(0.2, 3.0).tolist()
    return [1.0 + strength * (c - 1.0) for c in chroma]


def _save_preview(frame):
    """Save a downscaled preview of frame (H, W, 3) into ComfyUI's temp dir
    so the point-picker widget has a backdrop. Returns the ui images dict;
    on failure logs once and returns an empty list so the node's return
    shape never changes."""
    global _preview_error_logged
    try:
        import os
        import random
        import numpy as np
        from PIL import Image

        h, w = frame.shape[:2]
        max_w = 768
        if w > max_w:
            frame = torch.nn.functional.interpolate(
                frame.permute(2, 0, 1).unsqueeze(0).float(),
                size=(int(h * max_w / w), max_w), mode="bilinear",
                align_corners=False,
            )[0].permute(1, 2, 0)
        arr = (frame.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)

        temp = _folder_paths.get_temp_directory()
        name = f"flarecore_{random.getrandbits(48):012x}.png"
        os.makedirs(temp, exist_ok=True)
        Image.fromarray(arr).save(os.path.join(temp, name), compress_level=1)
        return {"images": [{"filename": name, "subfolder": "", "type": "temp"}]}
    except Exception as e:
        if not _preview_error_logged:
            _preview_error_logged = True
            logging.warning("flarecore: preview save failed (%s); the point "
                            "picker will have no backdrop", e)
        return {"images": []}
