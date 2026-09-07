# SPDX-License-Identifier: Apache-2.0
"""Scene motion: carry a light by how the picture moves."""
import math
import sys
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.scene_motion import estimate_motion, carry_point, good_features
from flare.feature_track import _luma


def panning_clip(frames=24, h=200, w=300, dx=2.0, dy=0.5, zoom=0.0, seed=0):
    """A textured plate panned by a known amount (and optionally zoomed
    about the centre). Returns clip and the true screen path of a point that
    started OFF-FRAME at (0.8, -0.1)."""
    g = torch.Generator().manual_seed(seed)
    base = F.avg_pool2d(torch.rand(1, 1, h * 3, w * 3, generator=g), 7, 1, 3)
    clip = torch.zeros(frames, h, w, 3)
    u0, v0 = 0.8, -0.1
    truth = []
    for i in range(frames):
        s = 1.0 + zoom * i
        tx, ty = dx * i, dy * i
        theta = torch.tensor([[1 / s, 0.0, -tx / (w / 2)], [0.0, 1 / s, -ty / (h / 2)]])
        grid = F.affine_grid(theta.unsqueeze(0), (1, 1, h, w), align_corners=False) / 3
        clip[i] = F.grid_sample(base, grid, align_corners=False)[0, 0].unsqueeze(-1).expand(-1, -1, 3)
        truth.append(((w / 2 + (u0 * w - w / 2 + tx) * s) / w,
                      (h / 2 + (v0 * h - h / 2 + ty) * s) / h))
    return clip, (u0, v0), truth


def _pan(base, xs, h, w):
    clip = torch.zeros(len(xs), h, w, 3)
    for i, tx in enumerate(xs):
        theta = torch.tensor([[1.0, 0.0, -tx / (w / 2)], [0.0, 1.0, 0.0]])
        grid = F.affine_grid(theta.unsqueeze(0), (1, 1, h, w), align_corners=False) / 3
        clip[i] = F.grid_sample(base, grid, align_corners=False)[0, 0].unsqueeze(-1).expand(-1, -1, 3)
    return clip


def cut_clip(before=12, after=12, h=200, w=300, dx_before=2.0, dx_after=-3.0):
    """A soft texture panned at dx_before, then a hard cut to an unrelated
    HARD-EDGED texture panned at dx_after. The edges matter: on smooth noise
    LK lands on a wrong-but-self-consistent match and the forward-backward
    check waves the cut through; on hard edges it fails, which is what real
    footage does. Frame `before` is the cut."""
    g = torch.Generator().manual_seed(0)
    soft = F.avg_pool2d(torch.rand(1, 1, h * 3, w * 3, generator=g), 7, 1, 3)
    g = torch.Generator().manual_seed(5)
    blobs = (F.avg_pool2d(torch.rand(1, 1, h * 3, w * 3, generator=g), 9, 1, 4) > 0.5).float()
    return torch.cat([_pan(soft, [dx_before * i for i in range(before)], h, w),
                      _pan(blobs, [dx_after * i for i in range(after)], h, w)])


class TestFeatures:
    def test_finds_corners_on_texture(self):
        clip, _, _ = panning_clip(frames=1)
        pts = good_features(_luma(clip)[0])
        assert len(pts) >= 20

    def test_finds_nothing_on_a_flat_frame(self):
        assert good_features(torch.full((120, 160), 0.5)) == []

    def test_keeps_clear_of_the_edges(self):
        clip, _, _ = panning_clip(frames=1)
        h, w = clip.shape[1], clip.shape[2]
        for x, y in good_features(_luma(clip)[0]):
            assert 0.03 * w < x < 0.97 * w and 0.03 * h < y < 0.97 * h


class TestCarry:
    def test_an_off_frame_light_follows_a_pan_to_a_pixel(self):
        clip, (u0, v0), truth = panning_clip()
        mo = estimate_motion(clip, anchor_uv=(u0, v0))
        path = carry_point(u0, v0, mo, clip.shape[1], clip.shape[2])
        err = [math.hypot(p[0] - t[0], p[1] - t[1]) * clip.shape[2]
               for p, t in zip(path, truth)]
        assert max(err) < 1.5, f"worst error {max(err):.2f} px"

    def test_a_still_shot_carries_the_light_nowhere(self):
        clip, (u0, v0), _ = panning_clip(dx=0.0, dy=0.0)
        mo = estimate_motion(clip, anchor_uv=(u0, v0))
        path = carry_point(u0, v0, mo, clip.shape[1], clip.shape[2])
        assert max(math.hypot(p[0] - u0, p[1] - v0) for p in path) < 0.003

    def _dolly(self, frames=24, h=200, w=300, grow=0.012, seed=1):
        """Driving forward: the far background (sky, treeline) does not move,
        the near foreground (road, trunks) expands away from the vanishing
        point. A pure zoom would move the sun too; a dolly does not."""
        g = torch.Generator().manual_seed(seed)
        far = F.avg_pool2d(torch.rand(1, 1, h, w, generator=g), 7, 1, 3)[0, 0]
        near = F.avg_pool2d(torch.rand(1, 1, h * 3, w * 3, generator=g), 5, 1, 2)
        clip = torch.zeros(frames, h, w, 3)
        for i in range(frames):
            s_ = 1.0 + grow * i
            theta = torch.tensor([[1 / s_, 0.0, 0.0], [0.0, 1 / s_, 0.0]])
            grid = F.affine_grid(theta.unsqueeze(0), (1, 1, h, w), align_corners=False) / 3
            fg = F.grid_sample(near, grid, align_corners=False)[0, 0]
            img = far.clone()
            img[h // 2:, :] = fg[h // 2:, :]          # foreground fills the lower half
            clip[i] = img.unsqueeze(-1).expand(-1, -1, 3)
        return clip

    def test_forward_travel_does_not_push_the_light_out(self):
        """Driving forward, every near feature expands from the vanishing
        point. A free scale term reads that as a zoom and pushes an off-frame
        light further out every frame; the rigid fit, weighted toward the
        far side of frame where the light is, must keep it put."""
        clip = self._dolly()
        u0, v0 = 0.8, -0.1
        rigid = carry_point(u0, v0, estimate_motion(clip, (u0, v0), model="rigid"),
                            clip.shape[1], clip.shape[2])
        loose = carry_point(u0, v0, estimate_motion(clip, (u0, v0), model="similarity"),
                            clip.shape[1], clip.shape[2])
        drift_rigid = math.hypot(rigid[-1][0] - u0, rigid[-1][1] - v0)
        drift_loose = math.hypot(loose[-1][0] - u0, loose[-1][1] - v0)
        assert drift_rigid < 0.03, f"rigid drifted {drift_rigid:.3f}"
        assert drift_rigid < drift_loose, (drift_rigid, drift_loose)

    def test_carries_backwards_from_a_later_start_frame(self):
        clip, (u0, v0), truth = panning_clip()
        mo = estimate_motion(clip, anchor_uv=(u0, v0))
        start = 10
        path = carry_point(truth[start][0], truth[start][1], mo,
                           clip.shape[1], clip.shape[2], start=start)
        err = [math.hypot(p[0] - t[0], p[1] - t[1]) * clip.shape[2]
               for p, t in zip(path, truth)]
        assert max(err) < 2.0, f"worst error {max(err):.2f} px"

    def test_single_frame_and_empty(self):
        clip, (u0, v0), _ = panning_clip(frames=1)
        assert carry_point(u0, v0, estimate_motion(clip), 200, 300) == [(u0, v0)]
        assert estimate_motion(torch.zeros(0, 10, 10, 3)) == [(1.0, 0.0, 0.0, 0.0)]

    def test_recovers_after_a_cut(self):
        """One frame LK cannot bridge -- a cut, a whip -- must not freeze
        the solve. Reseeding features on the new frame is only half of it:
        the reference pyramid has to move on too, or every later frame is
        matched against the pre-cut frame, fails again, and replays the last
        good motion for the rest of the shot."""
        clip = cut_clip(before=12, after=12, dx_before=2.0, dx_after=-3.0)
        stats = {}
        mo = estimate_motion(clip, anchor_uv=(0.5, 0.5), stats=stats)
        # only the cut itself is unsolvable
        assert stats["solved_frames"] >= clip.shape[0] - 2, stats
        # and after it the motion is the new pan, not a replay of the old
        tx_after = [round(m[2], 2) for m in mo[13:]]
        assert all(t < -1.5 for t in tx_after), tx_after
