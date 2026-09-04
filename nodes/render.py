# SPDX-License-Identifier: Apache-2.0
"""FlareRender: render a procedural lens flare over an image."""

import torch

from ..flare.colorspace import srgb_to_linear, linear_to_srgb
from ..flare.detect import detect_lights, linear_luminance
from ..flare.engine import render_batch, composite
from ..flare.grid import uv_to_grid
from ..flare.occlude import occlusion_factor
from ..flare.schema import load_preset
from .library import resolve_preset_textures

DEFAULT_PRESET = '{"schema_version": 1, "elements": [{"type": "glow"}]}'


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
            },
            "optional": {
                "depth": ("IMAGE",),
            },
        }

    def render(self, image, preset_json, position_mode, light_x, light_y,
               flare_x, flare_y, detect_threshold, detect_max_lights,
               occlusion_radius, light_depth, invert_depth, intensity, scale,
               blend_mode, clamp_output, seed, depth=None):
        preset = load_preset(preset_json)
        resolve_preset_textures(preset)

        device = image.device
        dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        rgb = image[..., :3].to(dtype)
        batch, height, width, _ = rgb.shape

        image_linear = srgb_to_linear(rgb)

        lights_per_frame = self._resolve_lights(
            image_linear, position_mode, light_x, light_y,
            detect_threshold, detect_max_lights,
        )

        if depth is not None:
            depth_maps = depth[..., :3].to(dtype).mean(dim=-1)  # (Bd, H, W)
            for i, lights in enumerate(lights_per_frame):
                dmap = depth_maps[min(i, depth_maps.shape[0] - 1)]
                for light in lights:
                    light["occlusion"] = occlusion_factor(
                        dmap, light["u"], light["v"],
                        radius=occlusion_radius, invert=invert_depth,
                        light_depth=light_depth,
                    )

        # The flare anchor (t = 1) is a second free point: element spacing
        # scales with the light-to-anchor distance.
        ax, ay = uv_to_grid(flare_x, flare_y, height, width)

        engine_lights = []
        for lights in lights_per_frame:
            frame = []
            for light in lights:
                x, y = uv_to_grid(light["u"], light["v"], height, width)
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
            out = out.clamp(0.0, 1.0)
            flare_pass = flare_pass.clamp(0.0, 1.0)

        flare_alpha = linear_luminance(flare_pass).clamp(0.0, 1.0)

        result = (out, flare_pass, flare_alpha)
        ui = _save_preview(out[0])
        if ui is not None:
            return {"ui": ui, "result": result}
        return result

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
    so the point-picker widget has a backdrop. Outside ComfyUI (tests,
    library use) this quietly does nothing."""
    try:
        import random
        import numpy as np
        from PIL import Image
        import folder_paths

        h, w = frame.shape[:2]
        max_w = 768
        if w > max_w:
            frame = torch.nn.functional.interpolate(
                frame.permute(2, 0, 1).unsqueeze(0).float(),
                size=(int(h * max_w / w), max_w), mode="bilinear",
                align_corners=False,
            )[0].permute(1, 2, 0)
        arr = (frame.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)

        temp = folder_paths.get_temp_directory()
        name = f"flarecore_{random.getrandbits(48):012x}.png"
        import os
        os.makedirs(temp, exist_ok=True)
        Image.fromarray(arr).save(os.path.join(temp, name), compress_level=1)
        return {"images": [{"filename": name, "subfolder": "", "type": "temp"}]}
    except Exception:
        return None
