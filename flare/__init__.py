# SPDX-License-Identifier: Apache-2.0
"""flarecore rendering engine.

Standalone PyTorch library for procedural lens flares. This package must not
import anything from ComfyUI; the nodes/ package wraps it.
"""

from .colorspace import srgb_to_linear, linear_to_srgb, luminance
from .schema import validate_preset, load_preset, ELEMENT_TYPES, SCHEMA_VERSION
from .engine import render_stack, render_batch, composite
from .detect import detect_lights, linear_luminance
from .occlude import occlusion_factor
from .depth import (condition_depth, normalize_depth, blur_depth,
                    remap_levels, temporal_smooth_depth)
from .track import track_lights, interpolate_keyframes, parse_keyframes

__all__ = [
    "luminance",
    "temporal_smooth_depth",
    "track_lights",
    "interpolate_keyframes",
    "parse_keyframes",
    "srgb_to_linear",
    "linear_to_srgb",
    "validate_preset",
    "load_preset",
    "ELEMENT_TYPES",
    "SCHEMA_VERSION",
    "render_stack",
    "render_batch",
    "composite",
    "detect_lights",
    "linear_luminance",
    "occlusion_factor",
    "condition_depth",
    "normalize_depth",
    "blur_depth",
    "remap_levels",
]
