# SPDX-License-Identifier: Apache-2.0
"""The element texture library: elements/<category>/<name>.png.

Presets reference textures by relative path; this module resolves those
references into linear-light tensors for the engine, with an mtime-aware
cache so edited files reload without a restart. Loading lives at the node
layer because the flare/ engine stays free of image-decoding dependencies.
"""

from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ..flare.colorspace import srgb_to_linear

ELEMENTS_DIR = Path(__file__).resolve().parents[1] / "elements"

_cache: dict[tuple, torch.Tensor] = {}

# A colour texture whose channels never differ by more than this is treated
# as an intensity field under channel='auto'.
_AUTO_GREY_TOLERANCE = 0.02


def list_elements() -> list[str]:
    """Relative paths of every texture in the library, sorted."""
    if not ELEMENTS_DIR.is_dir():
        return []
    return sorted(
        p.relative_to(ELEMENTS_DIR).as_posix()
        for p in ELEMENTS_DIR.rglob("*.png")
    )


def _resolve_path(ref: str) -> Path:
    norm = ref.replace("\\", "/")
    if norm.startswith("/") or ".." in norm.split("/") or (len(norm) > 1 and norm[1] == ":"):
        raise ValueError(f"texture reference {ref!r} must stay inside the element library")
    path = ELEMENTS_DIR / norm
    if not path.is_file():
        available = list_elements()
        listing = ", ".join(available[:20]) if available else "(library is empty)"
        raise ValueError(
            f"texture {ref!r} not found in the element library at "
            f"{ELEMENTS_DIR}; available: {listing}"
        )
    return path


def load_texture(ref: str, channel: str = "auto") -> torch.Tensor:
    """Load a library texture as linear light: (H, W) grey or (H, W, 3)."""
    path = _resolve_path(ref)
    key = (str(path), path.stat().st_mtime_ns, channel)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    arr = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255.0
    rgb = srgb_to_linear(torch.from_numpy(arr))

    if channel == "luminance":
        out = (rgb * torch.tensor([0.2126, 0.7152, 0.0722])).sum(-1)
    elif channel == "rgb":
        out = rgb
    else:  # auto
        spread = (rgb.amax(dim=-1) - rgb.amin(dim=-1)).max()
        out = rgb.mean(-1) if spread <= _AUTO_GREY_TOLERANCE else rgb

    _cache.clear() if len(_cache) > 64 else None
    _cache[key] = out
    return out


def resolve_preset_textures(preset: dict) -> None:
    """Inject '_texture' tensors into every texture element of a validated
    preset, in place. Raises with the library listing when a file is missing."""
    for elem in preset["elements"]:
        if elem["type"] == "texture" and elem["enabled"]:
            ref = elem["params"].get("file", "")
            if not ref:
                raise ValueError(
                    "a texture element has no params.file set; pick one of: "
                    + (", ".join(list_elements()[:20]) or "(library is empty)")
                )
            elem["params"]["_texture"] = load_texture(
                ref, elem["params"].get("channel", "auto")
            )
