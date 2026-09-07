# SPDX-License-Identifier: Apache-2.0
"""Luminance peak detection: find bright points to place flares on.

Pure torch. Deterministic ordering: peaks are sorted by brightness
descending, then by y, then by x, so batch frames stay consistent.

Two properties matter for video work and shape this implementation:

- Real bright lights clip. Inside a saturated disc every pixel equals the
  local max, so "peak" pixels arrive as a plateau, not a point. The reported
  position is therefore a mean-shift centroid of thresholded luminance, which
  converges to the disc centre instead of anchoring to whichever rim pixel a
  tie-break happens to pick — the difference between a stable flare origin
  and per-frame chatter.
- Candidate handling transfers to the CPU once per frame. Sorting GPU
  tensors element-by-element from Python would issue one device sync per
  candidate, and a blown-out sky has hundreds of thousands of candidates.
"""

import torch
import torch.nn.functional as F

from .colorspace import LUMA_WEIGHTS, luminance

# A frame with more raw peak candidates than this keeps only the brightest
# of them; beyond a few hundred the rest are plateau duplicates anyway.
_MAX_CANDIDATES = 512


def linear_luminance(image_linear: torch.Tensor) -> torch.Tensor:
    """Rec.709 luminance of a (..., 3) linear-light tensor."""
    return luminance(image_linear)


def detect_lights(image_linear: torch.Tensor, threshold: float = 0.8,
                  max_lights: int = 1, min_separation: float = 0.1,
                  window: int = 5,
                  region_sigma: float = 0.0) -> list[list[dict]]:
    """Find bright peaks in a (B, H, W, 3) linear image.

    threshold: minimum linear luminance for a peak.
    min_separation: minimum distance between kept peaks, as a fraction of
        image height.
    window: local-maximum window size in pixels (odd).
    region_sigma: when > 0, choose lights by REGION rather than by peak
        pixel -- blur the above-floor luminance by this fraction of image
        height and take the maxima of that field. A blown-out sky is a
        plateau thousands of pixels wide where the brightest pixel is an
        arbitrary tie-break that moves with compression noise; measured on a
        driving shot, the per-frame light hopped a mean of 0.26 of frame
        height, and 0.076 with this on. Only the per-frame `detect` mode
        wants it: tracking is fed a POOL of fine-grained candidates and
        associates them itself, and it tracks measurably worse when the
        pool is smoothed into regions first.

    Returns a list of length B; each entry is a list of dicts
    {"u", "v", "brightness", "energy"} with u/v in [0, 1], subpixel, ordered
    brightest first. `energy` is the above-floor luminance summed over the
    peak's neighbourhood — a stand-in for how big the source is, and the
    only thing that still tells a sun from a small gap in the leaves once
    both have clipped to pure white.
    """
    if image_linear.dim() != 4:
        raise ValueError(f"expected (B, H, W, C) tensor, got shape {tuple(image_linear.shape)}")
    b, height, width, _ = image_linear.shape
    lum = linear_luminance(image_linear)  # (B, H, W)

    above = None
    if region_sigma > 0.0:
        from .depth import blur_depth
        above = (lum - threshold).clamp(min=0.0)
        field = blur_depth(above, region_sigma)
        win = max(3, (int(min_separation * height) | 1))
        pooled = F.max_pool2d(field.unsqueeze(1), kernel_size=win, stride=1,
                              padding=win // 2).squeeze(1)
        is_peak = (field == pooled) & (field > 0.0)
        rank = field
    else:
        pad = window // 2
        pooled = F.max_pool2d(lum.unsqueeze(1), kernel_size=window, stride=1, padding=pad)
        pooled = pooled.squeeze(1)
        is_peak = (lum >= threshold) & (lum == pooled)
        rank = lum

    min_sep_px = min_separation * height
    results = []
    for i in range(b):
        mask = is_peak[i]
        flat = torch.nonzero(mask, as_tuple=False)  # (N, 2) as (y, x)
        if flat.shape[0] == 0:
            results.append([])
            continue

        values = rank[i, flat[:, 0], flat[:, 1]]
        if flat.shape[0] > _MAX_CANDIDATES:
            top = torch.topk(values, _MAX_CANDIDATES).indices
            flat = flat[top]
            values = values[top]

        # one transfer per frame; everything below is plain Python
        ys = flat[:, 0].tolist()
        xs = flat[:, 1].tolist()
        vals = values.float().tolist()
        order = sorted(range(len(vals)), key=lambda j: (-vals[j], ys[j], xs[j]))

        kept = []
        for j in order:
            py, px = ys[j], xs[j]
            if any((py - ky) ** 2 + (px - kx) ** 2 < min_sep_px ** 2 for ky, kx, _ in kept):
                continue
            kept.append((py, px, vals[j]))
            if len(kept) >= max_lights:
                break

        radius = max(2, min(int(min_sep_px / 2), 15))
        lights = []
        for py, px, ranked in kept:
            brightness = float(lum[i, py, px])
            if above is not None:
                cy, cx, energy = _region_centroid(
                    above[i], py, px, REGION_CENTROID_SIGMA * height)
            else:
                cy, cx, energy = _meanshift_centroid(lum[i], py, px,
                                                     threshold, radius)
            lights.append({
                "u": (cx + 0.5) / width,
                "v": (cy + 0.5) / height,
                "brightness": brightness,
                "energy": energy,
            })
        results.append(lights)
    return results


# Half-width of the window the region centroid measures over, as a fraction
# of image height. A uniform plateau has no interior structure at all, so the
# window has to REACH ITS EDGES to find the middle: measured on a synthetic
# disk, 0.08H put the answer 0.05 off centre and 0.25H put it within 0.008.
# Gaussian-weighted rather than a hard disk so a rival source just outside
# barely tugs the answer -- a small rival 0.28H away moved it by 0.004.
REGION_CENTROID_SIGMA = 0.25


def _region_centroid(above: torch.Tensor, py: int, px: int,
                     sigma_px: float) -> tuple[float, float, float]:
    """Gaussian-weighted centroid of the above-floor mass around (py, px).

    Returns (cy, cx, energy). Separable weights, so this costs two vectors
    and one image multiply per light rather than a windowed gather.
    """
    height, width = above.shape
    ys = torch.arange(height, device=above.device, dtype=above.dtype)
    xs = torch.arange(width, device=above.device, dtype=above.dtype)
    s2 = 2.0 * max(sigma_px, 1.0) ** 2
    wy = torch.exp(-((ys - float(py)) ** 2) / s2)
    wx = torch.exp(-((xs - float(px)) ** 2) / s2)
    mass = above * wy.unsqueeze(1) * wx.unsqueeze(0)
    total = float(mass.sum())
    if total <= 0.0:
        return float(py), float(px), 0.0
    cy = float((mass.sum(1) * ys).sum()) / total
    cx = float((mass.sum(0) * xs).sum()) / total
    return cy, cx, total


def _meanshift_centroid(lum: torch.Tensor, py: int, px: int,
                        threshold: float, radius: int,
                        iterations: int = 3) -> tuple[float, float, float]:
    """Above-threshold-weighted centroid, re-centred a few times.

    The seed pixel of a saturated plateau is its top-left rim; iterating the
    windowed centroid walks the estimate into the blob's centre. Also returns
    the window's above-floor luminance sum as the peak's energy.
    """
    height, width = lum.shape
    floor = threshold * 0.5
    cy, cx = float(py), float(px)
    for _ in range(iterations):
        iy, ix = int(round(cy)), int(round(cx))
        y0, y1 = max(iy - radius, 0), min(iy + radius + 1, height)
        x0, x1 = max(ix - radius, 0), min(ix + radius + 1, width)
        patch = (lum[y0:y1, x0:x1] - floor).clamp(min=0.0)
        total = patch.sum()
        if total <= 0:
            return float(py), float(px), 0.0
        energy = float(total)
        ys = torch.arange(y0, y1, device=lum.device, dtype=patch.dtype)
        xs = torch.arange(x0, x1, device=lum.device, dtype=patch.dtype)
        ny = ((patch.sum(dim=1) * ys).sum() / total).item()
        nx = ((patch.sum(dim=0) * xs).sum() / total).item()
        if abs(ny - cy) < 0.05 and abs(nx - cx) < 0.05:
            cy, cx = ny, nx
            break
        cy, cx = ny, nx
    return cy, cx, energy
