# SPDX-License-Identifier: Apache-2.0
"""Normalized coordinate grid construction.

Coordinates are normalized by half the frame height, so units are
half-frame-heights and the aspect ratio is preserved:

    x in [-aspect, +aspect]   left -> right
    y in [-1, +1]             top -> bottom (image row 0 is the top)

Frame center is the origin.
"""

import torch


def make_grid(height: int, width: int, device, dtype) -> tuple[torch.Tensor, torch.Tensor]:
    """Return (x, y) coordinate tensors, each of shape (height, width).

    Pixel centers are sampled (a pixel spans +-half a step around its
    coordinate), so the grid is symmetric about the frame center.
    """
    aspect = width / height
    ys = ((torch.arange(height, device=device, dtype=dtype) + 0.5) / height) * 2.0 - 1.0
    xs = (((torch.arange(width, device=device, dtype=dtype) + 0.5) / width) * 2.0 - 1.0) * aspect
    y, x = torch.meshgrid(ys, xs, indexing="ij")
    return x, y


def uv_to_grid(u: float, v: float, height: int, width: int) -> tuple[float, float]:
    """Convert a UV position in [0,1] (origin top-left) to grid coordinates."""
    aspect = width / height
    return (u * 2.0 - 1.0) * aspect, v * 2.0 - 1.0
