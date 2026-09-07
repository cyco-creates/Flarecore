# SPDX-License-Identifier: Apache-2.0
"""Render the starter element library with the engine itself.

Every shipped texture is produced by our own procedural math, so the library
starts populated without any licensing exposure. Regenerate with:

    python tests/make_starter_elements.py
"""

import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flare.colorspace import linear_to_srgb
from flare.engine import render_stack
from flare.schema import validate_preset

OUT = ROOT / "elements"
SIZE = 512

STARTERS = [
    ("glows/warm_soft", {"type": "glow", "scale": 0.7, "color": [1.0, 0.82, 0.55],
                         "params": {"softness": 0.35, "falloff": 1.3}}),
    ("glows/cool_core", {"type": "glow", "scale": 0.5, "intensity": 1.4,
                         "color": [0.7, 0.85, 1.0],
                         "params": {"softness": 0.18, "falloff": 2.0}}),
    ("rays/spark_12", {"type": "glint", "scale": 0.9, "color": [1.0, 0.95, 0.8],
                           "params": {"points": 12, "length": 0.8,
                                      "thickness": 0.01, "length_jitter": 0.35}}),
    ("rays/fine_burst", {"type": "glint", "scale": 0.95, "intensity": 0.8,
                             "color": [0.85, 0.9, 1.0],
                             "params": {"points": 24, "length": 0.9,
                                        "thickness": 0.005, "length_jitter": 0.5}}),
    ("ghosts/hex_soft", {"type": "iris", "scale": 0.6, "intensity": 0.85,
                              "color": [0.6, 0.8, 1.0],
                              "params": {"blades": 6, "edge_softness": 0.35}}),
    ("ghosts/octa_hollow", {"type": "iris", "scale": 0.62, "intensity": 0.9,
                                 "color": [1.0, 0.75, 0.5], "dispersion": 0.8,
                                 "params": {"blades": 8, "edge_softness": 0.2,
                                            "hollow": 0.72}}),
    ("streaks/anamorphic_blue", {"type": "streak", "scale": 0.95, "auto_rotate": False,
                                 "color": [0.4, 0.6, 1.0],
                                 "params": {"length": 0.55, "thickness": 0.02}}),
    ("rings/thin_white", {"type": "ring", "scale": 0.62, "color": [1.0, 0.98, 0.95],
                          "params": {"radius": 1.0, "thickness": 0.06}}),
    ("rings/rainbow_ring", {"type": "spectral", "scale": 0.58,
                               "dispersion": 2.2, "dispersion_samples": 9,
                               "params": {"shape": "ring", "radius": 1.0,
                                          "thickness": 0.07}}),
    ("ghosts/hex_cluster", {"type": "iris", "scale": 0.16, "intensity": 0.5,
                                "count": 5, "spread": 0.45, "count_falloff": 0.8,
                                "count_scale_step": 1.35, "offset": -0.9,
                                "auto_rotate": False, "rotation": 20.0,
                                "color": [0.9, 0.85, 0.7],
                                "params": {"blades": 6, "edge_softness": 0.5}}),
]


def render_texture(elem: dict) -> torch.Tensor:
    elem = dict(elem)
    elem.setdefault("offset", 0.0)
    elem.setdefault("auto_rotate", False)
    preset = validate_preset({"schema_version": 1, "elements": [elem]})
    # Light at frame centre; anchor pushed right so axis-dependent elements
    # (the bokeh chain) have a direction to follow.
    light = [{"x": 0.0, "y": 0.0, "ax": 0.9, "ay": 0.0, "brightness": 1.0}]
    return render_stack(preset, light, SIZE, SIZE, "cpu", torch.float32)


def main():
    for ref, elem in STARTERS:
        rgb = render_texture(elem)
        encoded = linear_to_srgb(rgb).clamp(0.0, 1.0)
        arr = (encoded.numpy() * 255).astype(np.uint8)
        path = OUT / f"{ref}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(path)
        print(f"wrote elements/{ref}.png  (peak {rgb.max().item():.2f})")


if __name__ == "__main__":
    main()
