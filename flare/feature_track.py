# SPDX-License-Identifier: Apache-2.0
"""Feature tracking: follow a patch of picture through a clip.

Light tracking (detect.py + track.py) follows the brightest thing in frame.
That is the right tool for a sun, and the wrong one the moment the flare has
to sit on something that is not the brightest thing -- a practical lamp in a
dim room, a reflection, a light that dims below its surroundings.

This follows PICTURE instead: a small feature region is matched into the next
frame by normalized cross-correlation, the way a compositor's point tracker
does. NCC is used rather than plain difference because it is invariant to
brightness and contrast changes, so a feature survives exposure shifts and a
light blowing out.

Two points give more than two positions: the vector between them carries the
rotation and the scale of whatever they are pinned to, which is what lets a
flare axis roll and grow with the shot.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F

# Below this correlation the match is not trustworthy: the tracker coasts on
# its last velocity rather than snapping to whatever noise scored highest.
MIN_CONFIDENCE = 0.35


def _luma(clip: torch.Tensor) -> torch.Tensor:
    """(B, H, W, C) -> (B, H, W) luminance."""
    if clip.dim() != 4:
        raise ValueError(f"expected (B, H, W, C) tensor, got {tuple(clip.shape)}")
    w = torch.tensor([0.2126, 0.7152, 0.0722], device=clip.device, dtype=clip.dtype)
    return (clip[..., :3] * w).sum(-1)


def _ncc(region: torch.Tensor, template: torch.Tensor) -> torch.Tensor:
    """Normalized cross-correlation of `template` over `region`.

    Both (H, W). Returns the correlation surface, one value per placement.
    The template is normalised once; the region's local mean and variance
    come from box filters, so the whole surface is three convolutions.
    """
    h, w = template.shape
    t = template - template.mean()
    norm = t.norm()
    if float(norm) < 1e-8:                     # a flat patch matches nothing
        return torch.zeros(region.shape[0] - h + 1, region.shape[1] - w + 1,
                           device=region.device, dtype=region.dtype)
    tn = (t / norm).view(1, 1, h, w)
    x = region.view(1, 1, *region.shape)
    ones = torch.ones(1, 1, h, w, device=region.device, dtype=region.dtype)
    n = float(h * w)
    s1 = F.conv2d(x, ones)
    s2 = F.conv2d(x * x, ones)
    var = (s2 - s1 * s1 / n).clamp(min=0.0)
    corr = F.conv2d(x, tn) - (s1 / n) * float(tn.sum())
    # A placement with almost no contrast -- blown sky, a black bar -- has a
    # vanishing denominator, and dividing by it manufactures enormous scores
    # out of nothing. Floor the denominator relative to the patch size and
    # clamp: correlation cannot exceed 1, so anything that does is numerical
    # noise, not a match.
    floor = 1e-4 * n
    surface = corr / var.clamp(min=floor).sqrt()
    return surface.clamp(-1.0, 1.0).squeeze(0).squeeze(0)


def _subpixel(surface: torch.Tensor, y: int, x: int) -> tuple[float, float]:
    """Parabola through the correlation peak and its two neighbours."""
    def offset(a: float, b: float, c: float) -> float:
        d = a - 2.0 * b + c
        return 0.0 if abs(d) < 1e-9 else max(-1.0, min(1.0, 0.5 * (a - c) / d))

    peak = float(surface[y, x])
    dy = dx = 0.0
    if 0 < y < surface.shape[0] - 1:
        dy = offset(float(surface[y - 1, x]), peak, float(surface[y + 1, x]))
    if 0 < x < surface.shape[1] - 1:
        dx = offset(float(surface[y, x - 1]), peak, float(surface[y, x + 1]))
    return dy, dx


def _coast(px: float, py: float, vx: float, vy: float,
           width: int, height: int) -> tuple[float, float, float, float]:
    """Carry a lost feature on its last velocity, decaying and inside frame.

    Undamped, a lost track accelerates off the picture and never comes back,
    because once it is outside the frame there is nothing left to match.
    """
    vx *= 0.7
    vy *= 0.7
    px = min(max(px + vx, 0.0), float(width - 1))
    py = min(max(py + vy, 0.0), float(height - 1))
    return px, py, vx, vy


def track_feature(clip: torch.Tensor, u: float, v: float,
                  feature: int = 32, search: int = 48,
                  adapt: float = 0.0) -> list[dict]:
    """Follow one point through the clip from its position in frame 0.

    clip: (B, H, W, C) in any range -- only structure matters to NCC.
    u, v: where the feature sits in frame 0, in [0, 1].
    feature: side of the square feature region, in pixels.
    search: how far from the predicted position to look, in pixels. This is
        the tracker's speed limit: a feature that moves further than this
        between frames is lost.
    adapt: 0 keeps the original template for the whole clip, which cannot
        drift but cannot survive a feature that changes either. Above 0 the
        template is blended toward what was just matched, which follows
        change at the cost of slowly walking off the feature.

    Returns one dict per frame: {"u", "v", "confidence"}. A frame that
    matched below MIN_CONFIDENCE keeps moving on the last known velocity
    instead of jumping to the best-scoring noise, and says so with a low
    confidence -- an unreliable track should be visible, not hidden.
    """
    if clip.shape[0] == 0:
        return []
    luma = _luma(clip)
    frames, height, width = luma.shape
    half = max(2, int(feature)) // 2
    reach = max(2, int(search))

    px = float(u) * width
    py = float(v) * height
    y0 = max(0, min(height - 2 * half, int(round(py)) - half))
    x0 = max(0, min(width - 2 * half, int(round(px)) - half))
    template = luma[0, y0:y0 + 2 * half, x0:x0 + 2 * half].clone()

    out = [{"u": px / width, "v": py / height, "confidence": 1.0}]
    vx = vy = 0.0
    for i in range(1, frames):
        # look around where the feature is HEADING, not where it was: a
        # steady pan then needs no more search radius than a static shot
        cx, cy = px + vx, py + vy
        sy0 = max(0, int(round(cy)) - half - reach)
        sx0 = max(0, int(round(cx)) - half - reach)
        sy1 = min(height, int(round(cy)) + half + reach)
        sx1 = min(width, int(round(cx)) + half + reach)
        region = luma[i, sy0:sy1, sx0:sx1]
        if region.shape[0] < 2 * half or region.shape[1] < 2 * half:
            px, py, vx, vy = _coast(px, py, vx, vy, width, height)
            out.append({"u": px / width, "v": py / height, "confidence": 0.0})
            continue

        surface = _ncc(region, template)
        flat = int(surface.argmax())
        sy, sx = flat // surface.shape[1], flat % surface.shape[1]
        score = float(surface[sy, sx])
        if score < MIN_CONFIDENCE:
            px, py, vx, vy = _coast(px, py, vx, vy, width, height)
            out.append({"u": px / width, "v": py / height,
                        "confidence": max(score, 0.0)})
            continue

        dy, dx = _subpixel(surface, sy, sx)
        nx = sx0 + sx + dx + half
        ny = sy0 + sy + dy + half
        vx, vy = nx - px, ny - py
        px, py = nx, ny
        out.append({"u": px / width, "v": py / height, "confidence": score})

        if adapt > 0.0:
            ty = max(0, min(height - 2 * half, int(round(py)) - half))
            tx = max(0, min(width - 2 * half, int(round(px)) - half))
            fresh = luma[i, ty:ty + 2 * half, tx:tx + 2 * half]
            if fresh.shape == template.shape:
                template = template * (1.0 - adapt) + fresh * adapt
    return out


def track_points(clip: torch.Tensor, points: list[tuple[float, float]],
                 feature: int = 32, search: int = 48,
                 adapt: float = 0.0) -> list[list[dict]]:
    """Track each point independently. Returns one list per point."""
    return [track_feature(clip, u, v, feature, search, adapt) for u, v in points]


def transform_from(a: list[dict], b: list[dict]) -> list[dict]:
    """Turn a two-point track into position, rotation and scale per frame.

    Everything is relative to frame 0, the way a compositor's two-point
    track is: rotation is how far the line between the points has turned
    since then, scale how much longer it has become.
    """
    if not a or not b:
        return []
    ax, ay = a[0]["u"], a[0]["v"]
    bx, by = b[0]["u"], b[0]["v"]
    base_ang = math.atan2(by - ay, bx - ax)
    base_len = math.hypot(bx - ax, by - ay)
    out = []
    for pa, pb in zip(a, b):
        dx, dy = pb["u"] - pa["u"], pb["v"] - pa["v"]
        length = math.hypot(dx, dy)
        out.append({
            "u": pa["u"], "v": pa["v"],
            "au": pb["u"], "av": pb["v"],
            "rotation": math.degrees(math.atan2(dy, dx) - base_ang),
            "scale": (length / base_len) if base_len > 1e-6 else 1.0,
            "confidence": min(pa["confidence"], pb["confidence"]),
        })
    return out
