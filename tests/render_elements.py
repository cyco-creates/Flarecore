# SPDX-License-Identifier: Apache-2.0
"""Render each element type in isolation to PNGs for visual inspection.

Usage: python tests/render_elements.py
Writes tests/out/elements/*.png (grayscale intensity, sRGB-encoded).
"""

import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.colorspace import linear_to_srgb
from flare.schema import PARAM_DEFAULTS
from flare.elements import ELEMENT_FUNCTIONS

OUT = Path(__file__).parent / "out" / "elements"

VARIANTS = [
    ("glow", {}),
    ("iris", {}),
    ("iris_hollow", {"_type": "iris", "hollow": 0.6, "edge_softness": 0.08}),
    ("iris_12_blades", {"_type": "iris", "blades": 12, "edge_softness": 0.05}),
    ("streak", {}),
    ("streak_cross", {"_type": "streak", "count": 2}),
    ("ring", {}),
    ("hoop", {}),
    ("glint", {}),
    ("glint_16pt", {"_type": "glint", "points": 16, "length": 0.9}),
    ("spectral_ring_shape", {"_type": "spectral"}),
    ("spectral_iris_shape", {"_type": "spectral", "shape": "iris"}),
]


def render_one(name: str, overrides: dict, n: int = 512, extent: float = 1.6):
    etype = overrides.pop("_type", name)
    params = dict(PARAM_DEFAULTS[etype])
    params.update(overrides)
    if etype == "glint":
        params.setdefault("seed", 0)

    xs = torch.linspace(-extent, extent, n)
    v, u = torch.meshgrid(xs, xs, indexing="ij")
    field = ELEMENT_FUNCTIONS[etype](u, v, params)

    encoded = linear_to_srgb(field).clamp(0.0, 1.0)
    img = (encoded.numpy() * 255).astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, mode="L").save(OUT / f"{name}.png")
    print(f"wrote {name}.png  (peak {field.max().item():.3f})")


def main():
    for name, overrides in VARIANTS:
        render_one(name, dict(overrides))


if __name__ == "__main__":
    main()
