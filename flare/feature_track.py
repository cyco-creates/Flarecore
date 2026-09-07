# SPDX-License-Identifier: Apache-2.0
"""Feature tracking: follow a patch of picture through a clip.

Light tracking (detect.py + track.py) follows the brightest thing in frame.
That is the right tool for a sun, and the wrong one the moment the flare has
to sit on something that is not the brightest thing -- a practical lamp in a
dim room, a reflection, a light that dims below its surroundings.

This follows PICTURE instead, the way a compositor's point tracker does.

The first version matched every frame against the patch from frame 0 by
normalized cross-correlation. On a driving shot a near feature changes scale
and lighting within a second, the correlation fell under the confidence
floor, and the tracker stopped: a tree trunk was lost at frame 12 and stayed
lost for 47 of 60 frames. One template cannot survive a feature that changes.

Now:
  * Frame to frame, pyramidal Lucas-Kanade. It compares each frame with the
    PREVIOUS one, so gradual change is fine, and the pyramid lets it follow
    motion far larger than its window. Sub-pixel by construction.
  * A forward-backward check gives an honest confidence: track the point
    back from where it landed, and if it does not return to where it
    started, the match was not real.
  * LK alone drifts, a fraction of a pixel per frame. When the frame-0
    template still correlates well near the tracked position, the position
    is pulled back onto it, so a feature that stays recognisable never walks
    off itself.
  * When LK fails -- an occlusion, a whip pan -- normalized cross-correlation
    against the original template searches the region around the predicted
    position to re-acquire the feature. Only when that fails too does the
    track coast, and it says so with a low confidence.

Two points give more than two positions: the vector between them carries the
rotation and the scale of whatever they are pinned to, which is what lets a
flare axis roll and grow with the shot.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F

# Below this the match is not trustworthy: the tracker coasts on its last
# velocity rather than snapping to whatever noise scored highest.
MIN_CONFIDENCE = 0.35
# Pyramid depth and refinement iterations for Lucas-Kanade. Three levels
# follow up to about 8x the window's worth of motion.
LK_LEVELS = 3
LK_ITERS = 12
# Forward-backward disagreement (pixels) beyond which an LK match is rejected.
FB_MAX_PX = 1.5
# Correlation with the frame-0 template needed before the position is
# pulled back onto it. Below this the feature has changed too much to trust
# the old template over the frame-to-frame result.
REANCHOR_MIN = 0.55
# Correlation needed to JUMP to a re-found feature after the track was lost.
# Higher than MIN_CONFIDENCE on purpose: continuing a track is a small claim,
# leaping to a new place is a large one, and a lost feature that has left
# the frame will always find some 0.4 correlation in the noise if allowed.
REACQUIRE_MIN = 0.6


def _luma(clip: torch.Tensor) -> torch.Tensor:
    """(B, H, W, C) -> (B, H, W) luminance."""
    if clip.dim() != 4:
        raise ValueError(f"expected (B, H, W, C) tensor, got {tuple(clip.shape)}")
    w = torch.tensor([0.2126, 0.7152, 0.0722], device=clip.device, dtype=clip.dtype)
    return (clip[..., :3] * w).sum(-1)


# --------------------------------------------------------------- NCC ------

def _ncc(region: torch.Tensor, template: torch.Tensor) -> torch.Tensor:
    """Normalized cross-correlation of `template` over `region`, (H, W) each.

    Three convolutions: the template is normalised once, and the region's
    local mean and variance come from box filters.
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
    # A placement with almost no contrast has a vanishing denominator, and
    # dividing by it manufactures enormous scores out of nothing. Floor it
    # relative to the patch size and clamp: correlation cannot exceed 1.
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


def _ncc_search(frame: torch.Tensor, template: torch.Tensor, cx: float, cy: float,
                reach: int) -> tuple[float, float, float] | None:
    """Best placement of `template` within `reach` of (cx, cy). (x, y, score)."""
    height, width = frame.shape
    h, w = template.shape
    hy, hx = h // 2, w // 2
    sy0 = max(0, int(round(cy)) - hy - reach)
    sx0 = max(0, int(round(cx)) - hx - reach)
    sy1 = min(height, int(round(cy)) + hy + reach + (h % 2))
    sx1 = min(width, int(round(cx)) + hx + reach + (w % 2))
    region = frame[sy0:sy1, sx0:sx1]
    if region.shape[0] < h or region.shape[1] < w:
        return None
    surface = _ncc(region, template)
    flat = int(surface.argmax())
    sy, sx = flat // surface.shape[1], flat % surface.shape[1]
    score = float(surface[sy, sx])
    dy, dx = _subpixel(surface, sy, sx)
    return sx0 + sx + dx + hx, sy0 + sy + dy + hy, score


# -------------------------------------------------------- Lucas-Kanade ----

def _pyramid(img: torch.Tensor, levels: int) -> list[torch.Tensor]:
    out = [img]
    for _ in range(1, levels):
        cur = out[-1]
        if min(cur.shape) < 8:
            break
        out.append(F.avg_pool2d(cur.unsqueeze(0).unsqueeze(0), 2,
                                ceil_mode=True).squeeze(0).squeeze(0))
    return out


def _window(img: torch.Tensor, cx: float, cy: float, half: int) -> torch.Tensor:
    """Bilinear (2*half+1)^2 window centred on the float position (cx, cy)."""
    height, width = img.shape
    n = 2 * half + 1
    offs = torch.arange(-half, half + 1, device=img.device, dtype=img.dtype)
    xs = (cx + offs) / max(width - 1, 1) * 2.0 - 1.0
    ys = (cy + offs) / max(height - 1, 1) * 2.0 - 1.0
    grid = torch.stack(torch.meshgrid(ys, xs, indexing="ij"), dim=-1)[..., [1, 0]]
    return F.grid_sample(img.view(1, 1, height, width), grid.view(1, n, n, 2),
                         mode="bilinear", padding_mode="border",
                         align_corners=True).view(n, n)


def _lk_step(prev: torch.Tensor, cur: torch.Tensor, x: float, y: float,
             half: int, levels: int, iters: int) -> tuple[float, float, bool]:
    """Where the window at (x, y) in `prev` has moved to in `cur`.

    Coarse to fine: the motion found on a half-size image seeds the search
    on the full-size one, so a window of a dozen pixels can follow a move of
    many dozens. Returns (nx, ny, ok); ok is False when the window has no
    texture to solve with, or the solve ran away.
    """
    return _lk_step_pyr(_pyramid(prev, levels), _pyramid(cur, levels),
                        x, y, half, iters)


def _lk_step_pyr(pp: list, pc: list, x: float, y: float,
                 half: int, iters: int) -> tuple:
    """_lk_step on pyramids built once and shared across many points."""
    dx = dy = 0.0
    for lvl in range(len(pp) - 1, -1, -1):
        s = 2.0 ** lvl
        px, py = x / s, y / s
        h = max(2, min(half, int(min(pp[lvl].shape) // 4)))
        P = _window(pp[lvl], px, py, h)
        # gradients of the reference window
        Ix = torch.zeros_like(P); Iy = torch.zeros_like(P)
        Ix[:, 1:-1] = (P[:, 2:] - P[:, :-2]) * 0.5
        Iy[1:-1, :] = (P[2:, :] - P[:-2, :]) * 0.5
        gxx = float((Ix * Ix).sum()); gyy = float((Iy * Iy).sum())
        gxy = float((Ix * Iy).sum())
        det = gxx * gyy - gxy * gxy
        if det < 1e-6 or (gxx + gyy) < 1e-4:
            return x, y, False                # featureless: cannot solve
        inv = ((gyy / det, -gxy / det), (-gxy / det, gxx / det))
        for _ in range(iters):
            C = _window(pc[lvl], px + dx, py + dy, h)
            It = C - P
            bx = -float((Ix * It).sum()); by = -float((Iy * It).sum())
            ex = inv[0][0] * bx + inv[0][1] * by
            ey = inv[1][0] * bx + inv[1][1] * by
            dx += ex; dy += ey
            if abs(ex) + abs(ey) < 0.02:
                break
            if abs(dx) > 4 * (h + 1) or abs(dy) > 4 * (h + 1):
                return x, y, False            # ran away: no match here
        if lvl > 0:
            dx *= 2.0; dy *= 2.0
    return x + dx, y + dy, True


def _windows(img: torch.Tensor, xs: torch.Tensor, ys: torch.Tensor,
             half: int) -> torch.Tensor:
    """(N, n, n) bilinear windows centred on N float positions, one call."""
    height, width = img.shape
    n = 2 * half + 1
    offs = torch.arange(-half, half + 1, device=img.device, dtype=img.dtype)
    gx = (xs.view(-1, 1, 1) + offs.view(1, 1, n)) / max(width - 1, 1) * 2.0 - 1.0
    gy = (ys.view(-1, 1, 1) + offs.view(1, n, 1)) / max(height - 1, 1) * 2.0 - 1.0
    grid = torch.stack([gx.expand(-1, n, n), gy.expand(-1, n, n)], dim=-1)
    src = img.view(1, 1, height, width).expand(xs.shape[0], 1, height, width)
    return F.grid_sample(src, grid, mode="bilinear", padding_mode="border",
                         align_corners=True).view(-1, n, n)


def _lk_batch(pp: list, pc: list, xs: torch.Tensor, ys: torch.Tensor,
              half: int, iters: int):
    """Pyramidal Lucas-Kanade for N points at once.

    Returns (nx, ny, ok) tensors. The same solve as _lk_step_pyr, but every
    window of every point is sampled in one grid_sample per iteration
    instead of one call per point, which is what makes tracking fifty scene
    features per frame affordable.
    """
    dev, dt = xs.device, xs.dtype
    n = xs.shape[0]
    if n == 0:
        return xs, ys, torch.zeros(0, dtype=torch.bool, device=dev)
    dx = torch.zeros(n, device=dev, dtype=dt)
    dy = torch.zeros(n, device=dev, dtype=dt)
    ok = torch.ones(n, dtype=torch.bool, device=dev)
    for lvl in range(len(pp) - 1, -1, -1):
        s = 2.0 ** lvl
        px, py = xs / s, ys / s
        h = max(2, min(half, int(min(pp[lvl].shape) // 4)))
        P = _windows(pp[lvl], px, py, h)
        Ix = torch.zeros_like(P); Iy = torch.zeros_like(P)
        Ix[:, :, 1:-1] = (P[:, :, 2:] - P[:, :, :-2]) * 0.5
        Iy[:, 1:-1, :] = (P[:, 2:, :] - P[:, :-2, :]) * 0.5
        gxx = (Ix * Ix).sum((1, 2)); gyy = (Iy * Iy).sum((1, 2))
        gxy = (Ix * Iy).sum((1, 2))
        det = gxx * gyy - gxy * gxy
        ok &= (det >= 1e-6) & ((gxx + gyy) >= 1e-4)
        det = det.clamp(min=1e-12)
        i00, i01, i11 = gyy / det, -gxy / det, gxx / det
        for _ in range(iters):
            C = _windows(pc[lvl], px + dx, py + dy, h)
            It = C - P
            bx = -(Ix * It).sum((1, 2)); by = -(Iy * It).sum((1, 2))
            ex = i00 * bx + i01 * by
            ey = i01 * bx + i11 * by
            dx = dx + ex; dy = dy + ey
            if float((ex.abs() + ey.abs()).max()) < 0.02:
                break
        ok &= (dx.abs() <= 4 * (h + 1)) & (dy.abs() <= 4 * (h + 1))
        if lvl > 0:
            dx = dx * 2.0; dy = dy * 2.0
    return xs + dx, ys + dy, ok


def _coast(px: float, py: float, vx: float, vy: float,
           width: int, height: int) -> tuple[float, float, float, float]:
    """Carry a lost feature on its last velocity, decaying and inside frame."""
    vx *= 0.7
    vy *= 0.7
    px = min(max(px + vx, 0.0), float(width - 1))
    py = min(max(py + vy, 0.0), float(height - 1))
    return px, py, vx, vy


# ----------------------------------------------------------- public -------

def track_feature(clip: torch.Tensor, u: float, v: float,
                  feature: int = 32, search: int = 48,
                  adapt: float = 0.0) -> list[dict]:
    """Follow one point through the clip from its position in frame 0.

    clip: (B, H, W, C) in any range -- only structure matters.
    u, v: where the feature sits in frame 0, in [0, 1].
    feature: side of the feature window, in pixels.
    search: how far from the predicted position re-acquisition may look, in
        pixels, when the frame-to-frame step has failed.
    adapt: kept for compatibility; the frame-to-frame step already follows
        gradual change, and the frame-0 template is what stops drift.

    Returns one dict per frame: {"u", "v", "confidence"}. Confidence is the
    better of the forward-backward agreement and the correlation with the
    original template, in [0, 1]. A lost frame keeps moving on the last
    known velocity and reports what it could get, never a made-up 1.
    """
    if clip.shape[0] == 0:
        return []
    luma = _luma(clip.float()).nan_to_num()
    frames, height, width = luma.shape
    half = max(1, min(max(2, int(feature)) // 2, (min(height, width)-1)//2))
    reach = max(2, int(search))

    px = float(u) * width
    py = float(v) * height
    px = min(max(px, 0.0), float(width - 1))
    py = min(max(py, 0.0), float(height - 1))
    template = _window(luma[0], px, py, half).clone()

    out = [{"u": px / width, "v": py / height, "confidence": 1.0}]
    vx = vy = 0.0
    lost = False
    for i in range(1, frames):
        prev, cur = luma[i - 1], luma[i]
        # Once lost, the previous frame contains the occluder, not our
        # feature. Running LK on it would confidently start tracking a tree.
        nx, ny, ok = (px, py, False) if lost else _lk_step(prev, cur, px, py, half, LK_LEVELS, LK_ITERS)
        fb = math.inf
        if ok:
            bx, by, ok2 = _lk_step(cur, prev, nx, ny, half, LK_LEVELS, LK_ITERS)
            fb = math.hypot(bx - px, by - py) if ok2 else math.inf
        if ok and fb <= FB_MAX_PX and 0.0 <= nx < width and 0.0 <= ny < height:
            fb_conf = max(0.0, 1.0 - fb / FB_MAX_PX) * 0.9
            # drift control: if the original template still sits within a
            # few pixels, pull the position back onto it
            found = _ncc_search(cur, template, nx, ny, 3)
            ncc = found[2] if found else 0.0
            if found and ncc >= REANCHOR_MIN:
                nx = 0.5 * (nx + found[0]); ny = 0.5 * (ny + found[1])
            vx, vy = nx - px, ny - py
            px, py = nx, ny
            lost = False
            conf = max(fb_conf, max(ncc, 0.0))
            # A window that overhangs the frame edge is matching replicated
            # border pixels, not picture. Say so: a feature walking out of
            # frame must not keep reporting a confident position.
            edge = min(px, py, width - 1 - px, height - 1 - py)
            if edge < half:
                conf *= max(edge, 0.0) / half
            out.append({"u": px / width, "v": py / height, "confidence": conf})
            continue

        # the frame-to-frame step failed: look for the original feature
        # around where it should be by now
        found = _ncc_search(cur, template, px + vx, py + vy, reach)
        if found and found[2] >= REACQUIRE_MIN:
            nx, ny, score = found
            vx, vy = nx - px, ny - py
            px, py = nx, ny
            lost = False
            out.append({"u": px / width, "v": py / height, "confidence": score})
            continue

        # Coasting: nothing matched well enough to jump to. Report the best
        # correlation seen, scaled well under the trust floor -- a 0.4 peak
        # in the noise must never read as a 0.4-confident position.
        px, py, vx, vy = _coast(px, py, vx, vy, width, height)
        lost = True
        seen = max(found[2], 0.0) if found else 0.0
        out.append({"u": px / width, "v": py / height,
                    "confidence": min(0.5 * seen, MIN_CONFIDENCE - 0.05)})
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
