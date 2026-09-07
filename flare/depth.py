# SPDX-License-Identifier: Apache-2.0
"""Depth map conditioning.

Pure tensor math to bring a third-party depth map into the range and
convention flare.occlude expects: values in [0, 1] with larger meaning nearer
to camera. No models and no inference here — depth estimation belongs to
whatever node produced the map (Depth Anything, MiDaS, Zoe, a rendered Z-pass).

Normalization mode matters for video. Per-frame normalization rescales every
frame independently, so the depth of a static object drifts as other content
enters and leaves the shot, and the occlusion breathes. Per-batch
normalization uses one range across the whole clip and holds still.
"""

import math

import torch
import torch.nn.functional as F

NORMALIZE_MODES = ("none", "per_frame", "per_batch")


def normalize_depth(depth: torch.Tensor, mode: str = "per_batch",
                    eps: float = 1e-6) -> torch.Tensor:
    """Min-max normalize a (B, H, W) depth stack into [0, 1].

    'per_frame' rescales each frame on its own range; 'per_batch' shares one
    range across the batch, which is the temporally stable choice for video.
    """
    if mode not in NORMALIZE_MODES:
        raise ValueError(
            f"unknown normalize mode {mode!r}; valid modes are: "
            f"{', '.join(NORMALIZE_MODES)}"
        )
    if mode == "none":
        return depth
    if mode == "per_batch":
        lo = depth.amin()
        hi = depth.amax()
    else:
        lo = depth.amin(dim=(-2, -1), keepdim=True)
        hi = depth.amax(dim=(-2, -1), keepdim=True)
    return ((depth - lo) / (hi - lo).clamp(min=eps)).clamp(0.0, 1.0)


def remap_levels(depth: torch.Tensor, black_point: float = 0.0,
                 white_point: float = 1.0, eps: float = 1e-6) -> torch.Tensor:
    """Linearly remap [black_point, white_point] onto [0, 1] and clamp.

    Useful for isolating the depth band an occluder actually occupies.
    """
    if white_point <= black_point:
        raise ValueError(
            f"white_point ({white_point}) must be greater than "
            f"black_point ({black_point})"
        )
    span = max(white_point - black_point, eps)
    return ((depth - black_point) / span).clamp(0.0, 1.0)


def _gaussian_kernel1d(sigma_px: float, device, dtype) -> torch.Tensor:
    radius = max(int(math.ceil(sigma_px * 3.0)), 1)
    x = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    k = torch.exp(-(x ** 2) / (2.0 * sigma_px * sigma_px))
    return k / k.sum()


def blur_depth(depth: torch.Tensor, amount: float) -> torch.Tensor:
    """Separable gaussian blur of a (B, H, W) stack.

    amount is the blur sigma as a fraction of image height, so the softening
    is resolution independent. Softening depth edges makes the occlusion ramp
    gently across an occluder's boundary instead of switching per-pixel.
    """
    if amount <= 0.0:
        return depth
    height, width = depth.shape[-2], depth.shape[-1]
    # Reflect padding requires pad < dim; cap sigma so the 3-sigma kernel
    # radius stays inside the narrower axis (portrait maps at high blur
    # otherwise raise inside F.pad).
    sigma_px = min(amount * height, (min(height, width) - 1) / 3.5)
    if sigma_px < 1e-3:
        return depth

    k = _gaussian_kernel1d(sigma_px, depth.device, depth.dtype)
    pad = (k.numel() - 1) // 2
    x = depth.unsqueeze(1)  # (B, 1, H, W)
    # Reflect padding keeps edges from darkening toward zero.
    x = F.pad(x, (pad, pad, 0, 0), mode="reflect")
    x = F.conv2d(x, k.view(1, 1, 1, -1))
    x = F.pad(x, (0, 0, pad, pad), mode="reflect")
    x = F.conv2d(x, k.view(1, 1, -1, 1))
    return x.squeeze(1)


def temporal_smooth_depth(depth: torch.Tensor, amount: float) -> torch.Tensor:
    """Zero-phase temporal smoothing of a (B, H, W) stack along the batch.

    amount in [0, 1]: 0 is off, 1 is heavy. A forward and a backward
    exponential pass are averaged so the smoothing does not lag the motion.
    Per-frame depth-model noise is what makes occlusion shimmer on video;
    this is the knob that stills it.
    """
    b = depth.shape[0]
    if amount <= 0.0 or b < 2:
        return depth
    k = 1.0 - 0.9 * min(amount, 1.0)  # blend factor toward the new frame

    fwd = depth.clone()
    for t in range(1, b):
        fwd[t] = fwd[t - 1] * (1.0 - k) + depth[t] * k
    bwd = depth.clone()
    for t in range(b - 2, -1, -1):
        bwd[t] = bwd[t + 1] * (1.0 - k) + depth[t] * k
    return (fwd + bwd) * 0.5


def condition_depth(depth: torch.Tensor, normalize: str = "per_batch",
                    invert: bool = False, blur: float = 0.0,
                    black_point: float = 0.0,
                    white_point: float = 1.0) -> torch.Tensor:
    """Full conditioning pipeline for a (B, H, W) depth stack.

    Order: normalize, invert, level remap, blur. Inverting before the remap
    means black_point/white_point always refer to the final convention
    (0 = far, 1 = near), whichever way the source map was oriented.
    """
    out = normalize_depth(depth, normalize)
    if invert:
        out = 1.0 - out
    out = remap_levels(out, black_point, white_point)
    return blur_depth(out, blur)
