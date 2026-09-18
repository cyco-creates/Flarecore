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
import torch.nn.functional as F

# Depth margin by which a sample must beat the light to count as occluding.
# Guards against depth-map noise and soft edges around the light.
DEFAULT_MARGIN = 0.1

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
    # Dense regular coverage avoids missing a narrow branch between 48
    # sparse rays. Threshold before bilinear sampling: averaging depth first
    # can turn a thin foreground object into distant sky.
    samples_wide = min(129, max(9, 2 * math.ceil(radius * height) + 1))
    axis = torch.linspace(-1, 1, samples_wide, device=depth.device)
    yy, xx = torch.meshgrid(axis, axis, indexing="ij")
    disk = xx.square() + yy.square() <= 1.0
    offs = torch.stack((xx[disk], yy[disk]), -1)
    su = u + offs[:, 0] * radius / aspect
    sv = v + offs[:, 1] * radius
    # A sample outside the map is unknown, not occluding. Clamping it into
    # the map judged a light above the frame by whatever lined the top edge
    # -- a canopy of branches blacked out an off-frame sun entirely.
    inside = ((su >= 0.0) & (su < 1.0) & (sv >= 0.0) & (sv < 1.0)).to(torch.float32)
    grid = torch.stack((su*2-1, sv*2-1), -1)[None,None]
    foreground = (depth > light_depth + margin).float()[None,None]
    nearer = F.grid_sample(foreground, grid, align_corners=False,
                           padding_mode="border")[0,0,0]
    fraction = (nearer * inside).sum() / inside.sum().clamp(min=1.0)
    # Coverage is already an area fraction; a 15% dead zone erased fine
    # branches, and a second smoothstep distorted their actual coverage.
    return fraction.clamp(0,1).item()
