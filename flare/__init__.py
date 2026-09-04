# SPDX-License-Identifier: Apache-2.0
"""flarecore rendering engine.

Standalone PyTorch library for procedural lens flares. This package must not
import anything from ComfyUI; the nodes/ package wraps it.
"""

from .colorspace import srgb_to_linear, linear_to_srgb
from .schema import validate_preset, load_preset, ELEMENT_TYPES, SCHEMA_VERSION

__all__ = [
    "srgb_to_linear",
    "linear_to_srgb",
    "validate_preset",
    "load_preset",
    "ELEMENT_TYPES",
    "SCHEMA_VERSION",
]
