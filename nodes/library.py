# SPDX-License-Identifier: Apache-2.0
"""The element texture library: elements/<category>/<name>.png.

Presets reference textures by relative path; this module resolves those
references into linear-light tensors for the engine, with an mtime-aware
LRU cache so edited files reload without a restart. Device copies are cached
too: at video batch sizes, re-uploading a texture per element evaluation is
gigabytes of pointless PCIe traffic. Loading lives at the node layer because
the flare/ engine stays free of image-decoding dependencies.
"""

from collections import OrderedDict
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ..flare.colorspace import srgb_to_linear, luminance
from ..flare.schema import normalize_texture_ref

ELEMENTS_DIR = Path(__file__).resolve().parents[1] / "elements"

_CACHE_LIMIT = 64
_cpu_cache: OrderedDict = OrderedDict()     # (path, mtime, channel) -> tensor
_device_cache: OrderedDict = OrderedDict()  # (cpu_key, device, dtype) -> tensor

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


# Pre-consolidation category folders, remapped so old presets keep loading.
_LEGACY_CATEGORY = {
    "fog": "glows", "discs": "ghosts", "iris_ghosts": "ghosts",
    "lens_orbs": "ghosts", "dirt_bokeh": "ghosts", "spike_balls": "rays",
    "shimmers": "rays", "sparkles": "rays", "stripes": "streaks",
    "spectral": "rings",
}


def _resolve_path(ref: str) -> Path:
    norm = normalize_texture_ref(ref, "texture reference")
    path = ELEMENTS_DIR / norm
    if not path.is_file() and "/" in norm:
        cat, _, rest = norm.partition("/")
        if cat in _LEGACY_CATEGORY:
            remapped = ELEMENTS_DIR / _LEGACY_CATEGORY[cat] / rest
            if remapped.is_file():
                return remapped
    if not path.is_file():
        available = list_elements()
        listing = ", ".join(available[:20]) if available else "(library is empty)"
        raise ValueError(
            f"texture {ref!r} not found in the element library at "
            f"{ELEMENTS_DIR}; available: {listing}"
        )
    return path


def _lru_put(cache: OrderedDict, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def load_texture(ref: str, channel: str = "auto",
                 device=None, dtype=None) -> torch.Tensor:
    """Load a library texture as linear light: (H, W) grey or (H, W, 3).

    With device/dtype given, the returned tensor is cached resident on that
    device — repeated renders reuse it instead of re-uploading.
    """
    path = _resolve_path(ref)
    cpu_key = (str(path), path.stat().st_mtime_ns, channel)
    out = _cpu_cache.get(cpu_key)
    if out is None:
        arr = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255.0
        rgb = srgb_to_linear(torch.from_numpy(arr))
        if channel == "luminance":
            out = luminance(rgb)
        elif channel == "rgb":
            out = rgb
        else:  # auto
            spread = (rgb.amax(dim=-1) - rgb.amin(dim=-1)).max()
            out = rgb.mean(-1) if spread <= _AUTO_GREY_TOLERANCE else rgb
        _lru_put(_cpu_cache, cpu_key, out)
    else:
        _cpu_cache.move_to_end(cpu_key)

    if device is None and dtype is None:
        return out
    dev_key = (cpu_key, str(device), str(dtype))
    moved = _device_cache.get(dev_key)
    if moved is None:
        moved = out.to(device=device, dtype=dtype)
        _lru_put(_device_cache, dev_key, moved)
    else:
        _device_cache.move_to_end(dev_key)
    return moved


def resolve_preset_textures(preset: dict, device=None, dtype=None) -> None:
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
                ref, elem["params"].get("channel", "auto"),
                device=device, dtype=dtype,
            )
