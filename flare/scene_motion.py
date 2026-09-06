# SPDX-License-Identifier: Apache-2.0
"""Scene motion: how the PICTURE moves, so a light can be carried by it.

The brightness detectors find the brightest thing in frame. Three things
break that on real footage, and the owner's driving shot has all three: the
sun is above the top edge, so what gets detected is the visible sky patch
below it; the sky is clipped, so the patch has no interior structure; and
branches keep cutting the patch, so its centre wanders. No estimator can
find a light that is not in the picture.

What IS in the picture is how everything moves. A sun is at infinity, so its
screen position changes only with camera rotation -- and camera rotation is
exactly what the far features in the frame reveal. So: pick corners the
picture actually has, follow them frame to frame with Lucas-Kanade, fit a
similarity transform (shift, rotation, scale) to how they moved, and carry
the light -- wherever it was placed, including outside the frame -- by that
transform. Occlusion cannot touch it because the light is never looked for.

Near features move with parallax the sun does not share. Two things keep
that from dragging the light: the fit is robust (iteratively reweighted, so
the many outliers of a nearby tree trunk sweeping past are shed), and
features are weighted by how far they are from the light in the frame,
which on a landscape shot favours the sky and the far treeline.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F

from .feature_track import _luma, _lk_batch, _pyramid, LK_ITERS

# How many corners to follow, and how close two may sit.
FEATURE_COUNT = 72
FEATURE_MIN_DIST = 0.06          # fraction of frame height
FEATURE_BORDER = 0.04            # keep corners this far from the edges
FEATURE_WIN = 10                 # LK half-window in pixels
FB_MAX = 1.0                     # forward-backward disagreement allowed, px
# Robust fit: passes of reweighting, and the residual (px) past which a
# feature stops counting.
IRLS_PASSES = 4
IRLS_CUTOFF_PX = 3.0
# Below this many surviving features the frame's motion is not trusted and
# the previous frame's motion is repeated instead of fitting noise.
MIN_INLIERS = 8


def good_features(luma: torch.Tensor, count: int = FEATURE_COUNT,
                  min_dist: float = FEATURE_MIN_DIST,
                  border: float = FEATURE_BORDER) -> list[tuple[float, float]]:
    """Corners worth following: the Shi-Tomasi minimum eigenvalue of the
    structure tensor, kept apart by non-maximum suppression. (x, y) in px."""
    height, width = luma.shape
    img = luma.view(1, 1, height, width)
    kx = torch.tensor([[-1.0, 0.0, 1.0]], device=luma.device, dtype=luma.dtype).view(1, 1, 1, 3)
    ix = F.conv2d(F.pad(img, (1, 1, 0, 0), mode="replicate"), kx)
    iy = F.conv2d(F.pad(img, (0, 0, 1, 1), mode="replicate"), kx.view(1, 1, 3, 1))
    w = 7
    box = torch.ones(1, 1, w, w, device=luma.device, dtype=luma.dtype) / (w * w)
    pad = w // 2
    sxx = F.conv2d(F.pad(ix * ix, (pad,) * 4, mode="replicate"), box)
    syy = F.conv2d(F.pad(iy * iy, (pad,) * 4, mode="replicate"), box)
    sxy = F.conv2d(F.pad(ix * iy, (pad,) * 4, mode="replicate"), box)
    tr = sxx + syy
    det = sxx * syy - sxy * sxy
    lam = (tr / 2 - ((tr / 2) ** 2 - det).clamp(min=0).sqrt()).view(height, width)
    # keep clear of the edges: a window there sees replicated pixels
    by, bx = int(border * height), int(border * width)
    lam[:by, :] = 0; lam[-by - 1:, :] = 0; lam[:, :bx] = 0; lam[:, -bx - 1:] = 0
    # non-maximum suppression at the separation distance
    sep = max(3, int(min_dist * height)) | 1
    peak = F.max_pool2d(lam.view(1, 1, height, width), sep, 1, sep // 2).view(height, width)
    cand = (lam == peak) & (lam > 1e-6)
    idx = torch.nonzero(cand, as_tuple=False)
    if idx.shape[0] == 0:
        return []
    vals = lam[idx[:, 0], idx[:, 1]]
    # Take the best corners PER CELL of a grid, not the best overall. Sharp
    # near-field texture -- trunks, road, dashboard -- otherwise wins every
    # slot and the far field that a sun actually moves with is never
    # followed at all: measured on a two-layer dolly, all 48 corners landed
    # in the expanding foreground and none in the static background.
    cols, rows = 6, 4
    per_cell = max(1, count // (cols * rows))
    ys_, xs_ = idx[:, 0].float(), idx[:, 1].float()
    cell = (ys_ * rows / height).long().clamp(0, rows - 1) * cols         + (xs_ * cols / width).long().clamp(0, cols - 1)
    order = torch.argsort(vals, descending=True)
    taken: dict = {}
    keep = []
    for i in order.tolist():
        c = int(cell[i])
        if taken.get(c, 0) >= per_cell:
            continue
        taken[c] = taken.get(c, 0) + 1
        keep.append(i)
        if len(keep) >= count:
            break
    # cells with no texture leave slots free: hand them to the next best
    if len(keep) < count:
        have = set(keep)
        for i in order.tolist():
            if i not in have:
                keep.append(i)
                if len(keep) >= count:
                    break
    return [(float(idx[i, 1]), float(idx[i, 0])) for i in keep]


def _fit_similarity(src, dst, weights, model="rigid"):
    """Weighted least-squares fit of src -> dst as (a, b, tx, ty), meaning
    x' = a*x - b*y + tx and y' = b*x + a*y + ty.

    model "similarity" solves all four (scale, rotation, shift); "rigid"
    fixes the scale at 1 (rotation + shift); "translation" fixes rotation
    too. A sun at infinity moves only with camera rotation. On a shot that
    travels forward every near feature expands away from the vanishing
    point, a free scale term reads that as a zoom, and a zoom pushes an
    off-frame light further out every frame. Fixing the scale is what keeps
    the sun where the sun is.
    """
    n = len(src)
    if n < 2:
        return None
    if model == "translation":
        tw = sum(weights) or 1.0
        tx = sum(w * (d[0] - s[0]) for s, d, w in zip(src, dst, weights)) / tw
        ty = sum(w * (d[1] - s[1]) for s, d, w in zip(src, dst, weights)) / tw
        return 1.0, 0.0, tx, ty
    if model == "rigid":
        # weighted Procrustes: centre both clouds, rotation from the
        # weighted cross-covariance, scale normalised away
        tw = sum(weights) or 1.0
        cx = sum(w * s[0] for s, w in zip(src, weights)) / tw
        cy = sum(w * s[1] for s, w in zip(src, weights)) / tw
        ex = sum(w * d[0] for d, w in zip(dst, weights)) / tw
        ey = sum(w * d[1] for d, w in zip(dst, weights)) / tw
        sa = sb = 0.0
        for s_, d, w in zip(src, dst, weights):
            px, py = s_[0] - cx, s_[1] - cy
            qx, qy = d[0] - ex, d[1] - ey
            sa += w * (px * qx + py * qy)
            sb += w * (px * qy - py * qx)
        norm = math.hypot(sa, sb)
        if norm < 1e-9:
            return None
        a, b = sa / norm, sb / norm
        return a, b, ex - (a * cx - b * cy), ey - (b * cx + a * cy)
    if n < 3:
        return None
    A = torch.zeros(2 * n, 4, dtype=torch.float64)
    rhs = torch.zeros(2 * n, dtype=torch.float64)
    wts = torch.zeros(2 * n, dtype=torch.float64)
    for i, ((x, y), (xp, yp), wt) in enumerate(zip(src, dst, weights)):
        A[2 * i] = torch.tensor([x, -y, 1.0, 0.0]); rhs[2 * i] = xp
        A[2 * i + 1] = torch.tensor([y, x, 0.0, 1.0]); rhs[2 * i + 1] = yp
        wts[2 * i] = wts[2 * i + 1] = wt
    sw = wts.sqrt().unsqueeze(1)
    try:
        sol = torch.linalg.lstsq(A * sw, (rhs * sw.squeeze(1)).unsqueeze(1)).solution.squeeze(1)
    except Exception:
        return None
    a, b, tx, ty = (float(v) for v in sol)
    if not all(math.isfinite(v) for v in (a, b, tx, ty)) or math.hypot(a, b) < 0.5:
        return None
    return a, b, tx, ty


def _apply(sim, x, y):
    a, b, tx, ty = sim
    return a * x - b * y + tx, b * x + a * y + ty


def scene_luma(clip: torch.Tensor, max_width: int = 640) -> torch.Tensor:
    """(B, H, W, C) -> (B, h, w) luminance, downsampled so w <= max_width.

    Motion estimation does not need every pixel: corners survive a 2-3x
    reduction, the solve gets 4-9x cheaper, and a whole clip's luma fits on
    the card where the full clip would not. Positions come back normalised
    to [0, 1], so the resolution used here never leaks out.
    """
    luma = _luma(clip).float()
    b, h, w = luma.shape
    if w > max_width:
        f = max_width / w
        luma = F.interpolate(luma.unsqueeze(1), size=(max(8, int(round(h * f))), max_width),
                             mode="area").squeeze(1)
    return luma


def estimate_motion(clip: torch.Tensor | None = None,
                    anchor_uv: tuple[float, float] | None = None,
                    feature_count: int = FEATURE_COUNT, model: str = "rigid",
                    weight_reach: float = 0.35,
                    luma: torch.Tensor | None = None,
                    stats: dict | None = None) -> list[tuple]:
    """Per-frame similarity transforms mapping frame i-1 -> frame i, in
    pixels. Entry 0 is the identity. Features are re-seeded whenever too few
    survive, so a long shot never runs out of things to follow.

    anchor_uv: where the light is; features near it count for more, so a
        far sky feature outweighs a near trunk when the light is in the sky.
    """
    if luma is None:
        luma = scene_luma(clip)
    luma = luma.float()
    frames, height, width = luma.shape
    ident = (1.0, 0.0, 0.0, 0.0)
    motions = [ident]
    if frames < 2:
        return motions
    pts = good_features(luma[0], feature_count)
    last = ident
    solved = 0                     # frames whose motion was actually measured
    pyr_prev = _pyramid(luma[0], 3)
    for i in range(1, frames):
        prev, cur = luma[i - 1], luma[i]
        pyr_cur = _pyramid(cur, 3)
        if len(pts) < MIN_INLIERS:
            pts = good_features(prev, feature_count)
        if not pts:                      # a featureless frame: nothing to solve
            motions.append(last)
            pyr_prev = pyr_cur
            continue
        xs = torch.tensor([p[0] for p in pts], dtype=torch.float32, device=luma.device)
        ys = torch.tensor([p[1] for p in pts], dtype=torch.float32, device=luma.device)
        nx, ny, ok = _lk_batch(pyr_prev, pyr_cur, xs, ys, FEATURE_WIN, LK_ITERS)
        bx, by, ok2 = _lk_batch(pyr_cur, pyr_prev, nx, ny, FEATURE_WIN, LK_ITERS)
        fb = ((bx - xs) ** 2 + (by - ys) ** 2).sqrt()
        keep = ok & ok2 & (fb <= FB_MAX) & (nx >= 0) & (nx < width) & (ny >= 0) & (ny < height)
        src = [(float(a), float(b)) for a, b, k in zip(xs, ys, keep) if k]
        dst = [(float(a), float(b)) for a, b, k in zip(nx, ny, keep) if k]
        if len(src) < MIN_INLIERS:
            motions.append(last)
            pts = good_features(cur, feature_count)
            # the reseeded features live on `cur`, so the reference pyramid
            # must move on with them: left behind, every later frame is
            # matched against the pre-cut frame, fails, and replays `last`
            pyr_prev = pyr_cur
            continue
        # distance weighting toward the light, then robust reweighting
        if anchor_uv is not None:
            # features near the light count for more: on a landscape that is
            # the sky and the far treeline, which move like the sun does
            ax, ay = anchor_uv[0] * width, anchor_uv[1] * height
            reach = max(weight_reach, 0.05) * math.hypot(width, height)
            base = [max(0.02, math.exp(-math.hypot(x - ax, y - ay) / reach)) for (x, y) in src]
        else:
            base = [1.0] * len(src)
        wts = list(base)
        sim = None
        for _ in range(IRLS_PASSES):
            sim = _fit_similarity(src, dst, wts, model)
            if sim is None:
                break
            res = [math.hypot(*(lambda p, q: (p[0] - q[0], p[1] - q[1]))(_apply(sim, *s), d))
                   for s, d in zip(src, dst)]
            # The cutoff adapts to how tightly the trusted features agree.
            # Far features on a landscape agree to a fraction of a pixel;
            # near ones on a forward-travelling shot disagree by only a
            # pixel or two, which a fixed cutoff would wave through and
            # which is exactly the parallax that drags the sun. Scale from
            # the weighted median residual, so the crowd that agrees sets
            # the bar and the crowd that does not is shed.
            order = sorted(range(len(res)), key=lambda k: res[k])
            total = sum(wts) or 1.0
            acc, med = 0.0, res[order[-1]]
            for k in order:
                acc += wts[k]
                if acc >= 0.5 * total:
                    med = res[k]
                    break
            cutoff = min(IRLS_CUTOFF_PX, max(0.35, 2.5 * med))
            wts = [b * (1.0 if r < cutoff else cutoff / r) ** 2
                   for b, r in zip(base, res)]
        if sim is None:
            motions.append(last)
        else:
            motions.append(sim)
            last = sim
            solved += 1
        # the surviving features move on; drop the ones that drifted
        pts = [d for s, d in zip(src, dst)
               if math.hypot(*(lambda p, q: (p[0] - q[0], p[1] - q[1]))(_apply(motions[-1], *s), d)) < IRLS_CUTOFF_PX]
        pyr_prev = pyr_cur
    if stats is not None:
        stats["solved_frames"] = solved
        stats["frames"] = frames
    return motions


def carry_point(u: float, v: float, motions: list[tuple], height: int, width: int,
                start: int = 0) -> list[tuple[float, float]]:
    """Where a point placed at (u, v) in frame `start` is in every frame,
    carried by the scene's motion. u, v may lie outside [0, 1]."""
    x, y = u * width, v * height
    frames = len(motions)
    out = [None] * frames
    out[start] = (x, y)
    cx, cy = x, y
    for i in range(start + 1, frames):
        cx, cy = _apply(motions[i], cx, cy)
        out[i] = (cx, cy)
    cx, cy = x, y
    for i in range(start - 1, -1, -1):
        # invert the motion that took frame i to frame i+1
        a, b, tx, ty = motions[i + 1]
        d = a * a + b * b
        px, py = cx - tx, cy - ty
        cx, cy = (a * px + b * py) / d, (-b * px + a * py) / d
        out[i] = (cx, cy)
    return [(px / width, py / height) for (px, py) in out]
