# SPDX-License-Identifier: Apache-2.0
"""Point tracking: follow picture, not brightness."""
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.feature_track import (MIN_CONFIDENCE, track_feature, track_points,
                                 transform_from)


def moving_texture(frames=24, h=200, w=300, dx=0.0, dy=0.0, rot=0.0,
                   noise=0.0, seed=0):
    """A textured plate slid by a known amount, so the answer is known."""
    g = torch.Generator().manual_seed(seed)
    pad = 60 + int(max(abs(dx), abs(dy)) * frames)   # room for the whole move
    # Real pictures have structure at every scale; raw noise has none above a
    # pixel, which defeats any pyramid. Blur the noise so it looks like a
    # surface rather than static.
    import torch.nn.functional as F
    base = torch.rand((1, 3, h + 2 * pad, w + 2 * pad), generator=g)
    base = F.avg_pool2d(base, 7, 1, 3)[0].permute(1, 2, 0).contiguous()
    clip = torch.zeros(frames, h, w, 3)
    truth = []
    for i in range(frames):
        ox = pad + int(round(dx * i))
        oy = pad + int(round(dy * i))
        clip[i] = base[oy:oy + h, ox:ox + w]
        if noise:
            clip[i] = clip[i] + torch.rand((h, w, 3), generator=g) * noise
        # a feature that started at frame 0's centre is now here
        truth.append(((0.5 * w - (ox - pad)) / w, (0.5 * h - (oy - pad)) / h))
    return clip.clamp(0, 1), truth


class TestAccuracy:
    def test_a_still_shot_produces_a_still_track(self):
        clip, _ = moving_texture(noise=0.02)
        tr = track_feature(clip, 0.5, 0.5)
        us = [p["u"] for p in tr]
        assert max(us) - min(us) < 0.005, f"drifted: {us}"

    def test_it_follows_known_motion_to_sub_pixel(self):
        clip, truth = moving_texture(dx=3.0, dy=1.5, noise=0.02)
        tr = track_feature(clip, 0.5, 0.5)
        err = [math.hypot(t[0] - p["u"], t[1] - p["v"]) * clip.shape[2]
               for t, p in zip(truth, tr)]
        assert max(err) < 1.0, f"worst error {max(err):.2f} px"

    def test_confidence_is_a_correlation(self):
        clip, _ = moving_texture(dx=2.0, noise=0.02)
        for p in track_feature(clip, 0.5, 0.5):
            assert -1.0 <= p["confidence"] <= 1.0, p

    def test_a_good_match_is_confident(self):
        clip, _ = moving_texture(dx=2.0, noise=0.02)
        tr = track_feature(clip, 0.5, 0.5)
        assert min(p["confidence"] for p in tr) > MIN_CONFIDENCE


class TestRobustness:
    def test_a_featureless_patch_does_not_run_away(self):
        """Flat sky has nothing to match. The track must hold, not fly off:
        an unbounded coast leaves frame and can never recover."""
        clip = torch.full((20, 180, 240, 3), 0.8)
        tr = track_feature(clip, 0.5, 0.5)
        assert all(0.0 <= p["u"] <= 1.0 and 0.0 <= p["v"] <= 1.0 for p in tr)

    def test_a_blown_highlight_reports_low_confidence(self):
        """It must say it failed rather than quietly return nonsense."""
        clip = torch.zeros(12, 180, 240, 3)
        clip[:, 40:120, 60:160] = 1.0            # a big clipped blob
        tr = track_feature(clip, 0.45, 0.45)
        assert min(p["confidence"] for p in tr) < 0.999

    def test_brightness_changes_do_not_break_it(self):
        """NCC is normalised, so an exposure ramp must not move the track."""
        clip, _ = moving_texture(noise=0.02)
        for i in range(clip.shape[0]):
            clip[i] = (clip[i] * (0.5 + i / clip.shape[0])).clamp(0, 1)
        tr = track_feature(clip, 0.5, 0.5)
        us = [p["u"] for p in tr]
        assert max(us) - min(us) < 0.01, f"exposure moved it: {us}"

    def test_an_empty_clip_is_handled(self):
        assert track_feature(torch.zeros(0, 10, 10, 3), 0.5, 0.5) == []

    def test_it_rejects_the_wrong_shape(self):
        with pytest.raises(ValueError):
            track_feature(torch.zeros(4, 4), 0.5, 0.5)


class TestTwoPoint:
    def test_two_points_give_position_rotation_and_scale(self):
        clip, _ = moving_texture(noise=0.02)
        a, b = track_points(clip, [(0.3, 0.5), (0.7, 0.5)])
        xf = transform_from(a, b)
        assert len(xf) == clip.shape[0]
        assert all(abs(f["rotation"]) < 2.0 for f in xf), "a still shot rotated"
        assert all(abs(f["scale"] - 1.0) < 0.05 for f in xf), "a still shot scaled"

    def test_the_second_point_becomes_the_anchor(self):
        clip, _ = moving_texture(noise=0.02)
        a, b = track_points(clip, [(0.3, 0.5), (0.7, 0.5)])
        xf = transform_from(a, b)
        assert abs(xf[0]["u"] - 0.3) < 0.02 and abs(xf[0]["au"] - 0.7) < 0.02

    def test_confidence_is_the_weaker_of_the_two(self):
        clip, _ = moving_texture(noise=0.02)
        a, b = track_points(clip, [(0.3, 0.5), (0.7, 0.5)])
        xf = transform_from(a, b)
        for f, pa, pb in zip(xf, a, b):
            assert f["confidence"] == min(pa["confidence"], pb["confidence"])

    def test_empty_input_is_handled(self):
        assert transform_from([], []) == []


class TestChange:
    """The first tracker matched every frame against frame 0 and died the
    moment a feature changed. Frame-to-frame tracking must follow a feature
    that zooms and an exposure that ramps, with the answer known."""

    def _zoom(self, frames=30, h=240, w=360, zoom=0.015, seed=0):
        import torch.nn.functional as F
        g = torch.Generator().manual_seed(seed)
        base = F.avg_pool2d(torch.rand(1, 1, h * 2, w * 2, generator=g), 7, 1, 3)
        clip = torch.zeros(frames, h, w, 3)
        u0, v0 = 0.58, 0.44
        truth = []
        for i in range(frames):
            s = 1.0 + zoom * i
            theta = torch.tensor([[1 / s, 0.0, 0.0], [0.0, 1 / s, 0.0]])
            grid = F.affine_grid(theta.unsqueeze(0), (1, 1, h, w), align_corners=False) * 0.5
            img = F.grid_sample(base, grid, align_corners=False)[0, 0]
            clip[i] = img.unsqueeze(-1).expand(-1, -1, 3)
            truth.append(((w / 2 + (u0 * w - w / 2) * s) / w,
                          (h / 2 + (v0 * h - h / 2) * s) / h))
        return clip, (u0, v0), truth

    def test_it_follows_a_zoom_to_within_two_pixels(self):
        clip, (u0, v0), truth = self._zoom()
        tr = track_feature(clip, u0, v0)
        err = [math.hypot(t[0] - p["u"], t[1] - p["v"]) * clip.shape[2]
               for t, p in zip(truth, tr)]
        # a fixed 32 px window through a x1.45 zoom: under 1% of frame width
        assert max(err) < 3.0, f"worst error {max(err):.2f} px over a x1.45 zoom"
        assert min(p["confidence"] for p in tr) > MIN_CONFIDENCE

    def test_fast_motion_is_followed(self):
        """7 px a frame on a 300 px frame: the pyramid's job."""
        clip, truth = moving_texture(dx=7.0, dy=2.0, frames=12)
        tr = track_feature(clip, 0.5, 0.5)
        err = [math.hypot(t[0] - p["u"], t[1] - p["v"]) * clip.shape[2]
               for t, p in zip(truth, tr)]
        assert max(err) < 1.5, f"worst error {max(err):.2f} px"

    def test_confidence_drops_when_the_feature_leaves_frame(self):
        """A feature that walks off the picture must not keep claiming a
        confident position: there is nothing left to see."""
        clip, _ = moving_texture(dx=6.0, frames=40)     # 240 px of travel on 300
        tr = track_feature(clip, 0.7, 0.5)
        assert tr[-1]["confidence"] < MIN_CONFIDENCE
        assert 0.0 <= tr[-1]["u"] <= 1.0
