# SPDX-License-Identifier: Apache-2.0
"""Depth-based occlusion: how much of the light source is blocked.

Samples the depth map in a small disk around the light position and computes
the fraction of samples nearer to camera than the light itself, smoothed so
the flare fades rather than pops as an object crosses the light.

Depth convention: internally, larger values are nearer to camera
(near-is-white). If the incoming map is near-is-black, set invert=True.
The caller states the convention explicitly; it is never guessed.
"""

import math

import torch


def _smoothstep(edge0: float, edge1: float, x: torch.Tensor) -> torch.Tensor:
    t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _disk_offsets(n: int = 24) -> list[tuple[float, float]]:
    """Fixed golden-spiral sample offsets within the unit disk."""
    golden = math.pi * (3.0 - math.sqrt(5.0))
    pts = []
    for i in range(n):
        r = math.sqrt((i + 0.5) / n)
        a = i * golden
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


_OFFSETS = _disk_offsets()


def occlusion_factor(depth: torch.Tensor, u: float, v: float,
                     radius: float = 0.02, invert: bool = False,
                     bias: float = 1e-3) -> float:
    """Occlusion in [0, 1] for a light at UV (u, v) in [0, 1].

    depth: (H, W) depth map. radius: sampling disk radius as a fraction of
    image height. invert: True if the map is near-is-black.
    Returns 0 when fully visible, 1 when fully blocked.
    """
    if depth.dim() != 2:
        raise ValueError(f"expected (H, W) depth map, got shape {tuple(depth.shape)}")
    if invert:
        depth = -depth  # order is all that matters; negation flips near/far

    height, width = depth.shape

    def sample(su: float, sv: float) -> float:
        px = min(max(int(su * width), 0), width - 1)
        py = min(max(int(sv * height), 0), height - 1)
        return depth[py, px].item()

    light_depth = sample(u, v)
    r_px = radius  # radius is in v units already (fraction of height)
    aspect = width / height
    nearer = 0
    for ox, oy in _OFFSETS:
        su = u + ox * r_px / aspect
        sv = v + oy * r_px
        if sample(su, sv) > light_depth + bias:
            nearer += 1
    fraction = nearer / len(_OFFSETS)
    return _smoothstep(0.15, 0.85, torch.tensor(fraction)).item()
