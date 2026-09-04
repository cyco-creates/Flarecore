# SPDX-License-Identifier: Apache-2.0
"""FlareRender: render a procedural lens flare over an image."""

import logging

import torch

from ..flare.colorspace import srgb_to_linear, linear_to_srgb
from ..flare.detect import detect_lights, linear_luminance
from ..flare.engine import render_batch, composite
from ..flare.grid import uv_to_grid
from ..flare.occlude import occlusion_factor
from ..flare.schema import load_preset
from .library import resolve_preset_textures

DEFAULT_PRESET = '{"schema_version": 1, "elements": [{"type": "glow"}]}'

# Preview saving needs ComfyUI's temp dir; outside ComfyUI (tests, library
# use) the node runs headless. Probed once so a genuine save failure inside
# ComfyUI is logged instead of masquerading as "not in ComfyUI".
try:
    import folder_paths as _folder_paths
except ImportError:
    _folder_paths = None

_preview_error_logged = False


class FlareRender:
    CATEGORY = "flare"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK")
    RETURN_NAMES = ("image", "flare_pass", "flare_alpha")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "preset_json": ("STRING", {"multiline": True, "default": DEFAULT_PRESET}),
                "position_mode": (["manual", "detect", "detect_with_manual_offset"],),
                "light_x": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 1.0, "step": 0.001}),
                "light_y": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.001}),
                "flare_x": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.001}),
                "flare_y": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.001}),
                "detect_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.01}),
                "detect_max_lights": ("INT", {"default": 1, "min": 1, "max": 16}),
                "occlusion_radius": ("FLOAT", {"default": 0.02, "min": 0.001, "max": 0.5, "step": 0.001}),
                "light_depth": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
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
               depth=None, lights=None):
        preset = load_preset(preset_json)

        device = image.device
        dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        resolve_preset_textures(preset, device=device, dtype=dtype)

        rgb = image[..., :3].to(dtype)
        batch, height, width, _ = rgb.shape

        image_linear = srgb_to_linear(rgb)

        if lights is not None:
            lights_per_frame = self._lights_from_input(lights, batch)
        else:
            lights_per_frame = self._resolve_lights(
                image_linear, position_mode, light_x, light_y,
                detect_threshold, detect_max_lights,
            )

        if depth is not None:
            depth_maps = depth[..., :3].to(dtype).mean(dim=-1)  # (Bd, H, W)
            bd = depth_maps.shape[0]
            if 1 < bd < batch:
                raise ValueError(
                    f"depth batch ({bd}) is shorter than the image batch "
                    f"({batch}); pass one depth map to reuse it for every "
                    f"frame, or one per frame"
                )
            for i, frame_lights in enumerate(lights_per_frame):
                dmap = depth_maps[0 if bd == 1 else i]
                for light in frame_lights:
                    light["occlusion"] = occlusion_factor(
                        dmap, light["u"], light["v"],
                        radius=occlusion_radius, invert=invert_depth,
                        light_depth=light_depth,
                    )
            self._smooth_occlusion(lights_per_frame, occlusion_smooth)

        # The flare anchor (t = 1) is a second free point: element spacing
        # scales with the light-to-anchor distance. Lights supplied through
        # the FLARE_LIGHTS input may carry their own per-frame anchor.
        default_anchor = uv_to_grid(flare_x, flare_y, height, width)

        engine_lights = []
        for frame_lights in lights_per_frame:
            frame = []
            for light in frame_lights:
                x, y = uv_to_grid(light["u"], light["v"], height, width)
                if "au" in light and "av" in light:
                    ax, ay = uv_to_grid(light["au"], light["av"], height, width)
                else:
                    ax, ay = default_anchor
                frame.append({
                    "x": x, "y": y, "ax": ax, "ay": ay,
                    "brightness": light.get("brightness", 1.0),
                    "occlusion": light.get("occlusion", 0.0),
                })
            engine_lights.append(frame)

        flare_linear = render_batch(
            preset, engine_lights, height, width, device, dtype,
            extra_seed=seed, intensity=intensity, scale=scale,
        )

        out_linear = composite(image_linear, flare_linear, blend_mode)
        out = linear_to_srgb(out_linear)
        flare_pass = linear_to_srgb(flare_linear)
        if clamp_output:
            out = out.clamp_(0.0, 1.0)
            flare_pass = flare_pass.clamp_(0.0, 1.0)

        flare_alpha = linear_luminance(flare_pass).clamp(0.0, 1.0)

        result = (out, flare_pass, flare_alpha)
        if _folder_paths is None:
            return result
        return {"ui": _save_preview(out[0]), "result": result}

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

    def _resolve_lights(self, image_linear, position_mode, light_x, light_y,
                        detect_threshold, detect_max_lights):
        batch = image_linear.shape[0]
        if position_mode == "manual":
            return [[{"u": light_x, "v": light_y, "brightness": 1.0}]
                    for _ in range(batch)]

        detected = detect_lights(
            image_linear, threshold=detect_threshold, max_lights=detect_max_lights,
        )
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
