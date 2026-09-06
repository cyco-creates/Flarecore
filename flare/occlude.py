# SPDX-License-Identifier: Apache-2.0
"""Depth-based occlusion: how much of the light source is blocked.

Samples the depth map in a small disk around the light position and computes
the fraction of samples nearer to camera than the light itself, smoothed so
the flare fades rather than pops as an object crosses the light.

The light's own depth is given explicitly by `light_depth` rather than read
from the pixel under the light. Reading it from that pixel is wrong precisely
when it matters most: once an occluder covers the light, the sampled depth IS
the occluder, nothing is nearer than it, and the flare pops back to full
strength at the moment it should disappear.

Depth convention: internally, larger values are nearer to camera
(near-is-white, which is what Depth Anything, MiDaS and Zoe emit). If the
incoming map is near-is-black, set invert=True. The caller states the
convention explicitly; it is never guessed.

Depth values are expected in [0, 1] (the ComfyUI IMAGE range). `light_depth`
uses the same scale: 0.0 is infinitely far (a sun or sky light), 1.0 sits at
the camera. Use flare.depth.condition_depth to bring other sources into range.
"""

import math

import torch

from .elements import _smoothstep

# Depth margin by which a sample must beat the light to count as occluding.
# Guards against depth-map noise and soft edges around the light.
DEFAULT_MARGIN = 0.1

# Occlusion ramps over this range of blocked-sample fraction.
_FRACTION_LO = 0.15
_FRACTION_HI = 0.85


def _disk_offsets(n: int = 48) -> list[tuple[float, float]]:
    """Fixed golden-spiral sample offsets within the unit disk."""
    golden = math.pi * (3.0 - math.sqrt(5.0))
    pts = []
    for i in range(n):
        r = math.sqrt((i + 0.5) / n)
        a = i * golden
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


_OFFSETS = _disk_offsets()

# The offsets never change; upload them to each device once, not per call —
# on video batches that is one H2D copy per light per frame otherwise.
_OFFSETS_CACHE: dict = {}


def _offsets_on(device) -> torch.Tensor:
    key = str(device)
    hit = _OFFSETS_CACHE.get(key)
    if hit is None:
        hit = torch.tensor(_OFFSETS, device=device, dtype=torch.float32)
        _OFFSETS_CACHE[key] = hit
    return hit


def occlusion_factor(depth: torch.Tensor, u: float, v: float,
                     radius: float = 0.02, invert: bool = False,
                     light_depth: float = 0.0,
                     margin: float = DEFAULT_MARGIN) -> float:
    """Occlusion in [0, 1] for a light at UV (u, v) in [0, 1].

    depth: (H, W) depth map, values in [0, 1], larger = nearer to camera
        (unless invert=True).
    radius: sampling disk radius as a fraction of image height.
    light_depth: the light's own depth on the same scale; 0.0 = at infinity.
    margin: how much nearer than the light a sample must be to count.

    Returns 0 when fully visible, 1 when fully blocked.
    """
    if depth.dim() != 2:
        raise ValueError(f"expected (H, W) depth map, got shape {tuple(depth.shape)}")
    if invert:
        depth = 1.0 - depth

    height, width = depth.shape
    aspect = width / height

    # Gather every disk sample in one indexed read: keeps this to a single
    # device sync per light instead of one per sample.
    offs = _offsets_on(depth.device)
    su = u + offs[:, 0] * radius / aspect
    sv = v + offs[:, 1] * radius
    # A sample outside the map is unknown, not occluding. Clamping it into
    # the map judged a light above the frame by whatever lined the top edge
    # -- a canopy of branches blacked out an off-frame sun entirely.
    inside = ((su >= 0.0) & (su < 1.0) & (sv >= 0.0) & (sv < 1.0)).to(torch.float32)
    px = (su * width).long().clamp(0, width - 1)
    py = (sv * height).long().clamp(0, height - 1)
    samples = depth[py, px]

    nearer = (samples > (light_depth + margin)).to(torch.float32)
    fraction = (nearer * inside).sum() / inside.sum().clamp(min=1.0)
    return _smoothstep(_FRACTION_LO, _FRACTION_HI, fraction).item()
