# SPDX-License-Identifier: Apache-2.0
"""Luminance peak detection: find bright points to place flares on.

Pure torch. Deterministic ordering: peaks are sorted by brightness
descending, then by y, then by x, so batch frames stay consistent.
"""

import torch
import torch.nn.functional as F

_LUMA = (0.2126, 0.7152, 0.0722)


def linear_luminance(image_linear: torch.Tensor) -> torch.Tensor:
    """Rec.709 luminance of a (..., 3) linear-light tensor."""
    w = torch.tensor(_LUMA, device=image_linear.device, dtype=image_linear.dtype)
    return (image_linear * w).sum(dim=-1)


def detect_lights(image_linear: torch.Tensor, threshold: float = 0.8,
                  max_lights: int = 1, min_separation: float = 0.1,
                  window: int = 5) -> list[list[dict]]:
    """Find bright peaks in a (B, H, W, 3) linear image.

    threshold: minimum linear luminance for a peak.
    min_separation: minimum distance between kept peaks, as a fraction of
        image height.
    window: local-maximum window size in pixels (odd).

    Returns a list of length B; each entry is a list of dicts
    {"u", "v", "brightness"} with u/v in [0, 1], subpixel, ordered
    brightest first.
    """
    if image_linear.dim() != 4:
        raise ValueError(f"expected (B, H, W, C) tensor, got shape {tuple(image_linear.shape)}")
    b, height, width, _ = image_linear.shape
    lum = linear_luminance(image_linear)  # (B, H, W)

    pad = window // 2
    pooled = F.max_pool2d(lum.unsqueeze(1), kernel_size=window, stride=1, padding=pad)
    pooled = pooled.squeeze(1)
    is_peak = (lum >= threshold) & (lum == pooled)

    min_sep_px = min_separation * height
    results = []
    for i in range(b):
        ys, xs = torch.nonzero(is_peak[i], as_tuple=True)
        if ys.numel() == 0:
            results.append([])
            continue
        values = lum[i, ys, xs]
        # sort by brightness desc, then y, then x (stable lexicographic)
        order = torch.arange(ys.numel())
        key = torch.stack([-values, ys.to(values.dtype), xs.to(values.dtype)], dim=1)
        order = sorted(order.tolist(), key=lambda j: tuple(key[j].tolist()))

        kept = []
        for j in order:
            py, px = ys[j].item(), xs[j].item()
            if any((py - ky) ** 2 + (px - kx) ** 2 < min_sep_px ** 2 for ky, kx, _ in kept):
                continue
            kept.append((py, px, values[j].item()))
            if len(kept) >= max_lights:
                break

        lights = []
        for py, px, brightness in kept:
            cy, cx = _subpixel_centroid(lum[i], py, px)
            lights.append({
                "u": (cx + 0.5) / width,
                "v": (cy + 0.5) / height,
                "brightness": brightness,
            })
        results.append(lights)
    return results


def _subpixel_centroid(lum: torch.Tensor, py: int, px: int, radius: int = 2):
    """Weighted-mean centroid of the neighbourhood around a peak pixel."""
    height, width = lum.shape
    y0, y1 = max(py - radius, 0), min(py + radius + 1, height)
    x0, x1 = max(px - radius, 0), min(px + radius + 1, width)
    patch = lum[y0:y1, x0:x1]
    total = patch.sum()
    if total <= 0:
        return float(py), float(px)
    ys = torch.arange(y0, y1, device=lum.device, dtype=lum.dtype)
    xs = torch.arange(x0, x1, device=lum.device, dtype=lum.dtype)
    cy = (patch.sum(dim=1) * ys).sum() / total
    cx = (patch.sum(dim=0) * xs).sum() / total
    return cy.item(), cx.item()
