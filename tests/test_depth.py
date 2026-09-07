# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.depth import (
    normalize_depth,
    remap_levels,
    blur_depth,
    condition_depth,
    NORMALIZE_MODES,
)
from flare.occlude import occlusion_factor


class TestNormalize:
    def test_per_batch_shares_one_range(self):
        d = torch.stack([
            torch.full((8, 8), 0.0),
            torch.full((8, 8), 1.0),
        ])
        out = normalize_depth(d, "per_batch")
        assert out[0].max() == 0.0
        assert out[1].min() == 1.0

    def test_per_frame_rescales_each_frame(self):
        d = torch.stack([
            torch.linspace(0.0, 0.2, 64).reshape(8, 8),
            torch.linspace(0.0, 1.0, 64).reshape(8, 8),
        ])
        out = normalize_depth(d, "per_frame")
        # both frames now span the full range independently
        assert out[0].max() == pytest.approx(1.0)
        assert out[1].max() == pytest.approx(1.0)

    def test_per_batch_is_temporally_stable(self):
        # A static object's depth must not drift when other content changes.
        # per_frame normalization moves it; per_batch holds it still.
        base = torch.linspace(0.0, 1.0, 64).reshape(8, 8)
        f0 = base.clone()
        f0[4, 4] = 0.5  # the static object
        f1 = f0.clone()
        f1[0, 0] = 2.0  # a nearer object enters the shot, extending the range
        d = torch.stack([f0, f1])

        per_frame = normalize_depth(d, "per_frame")
        per_batch = normalize_depth(d, "per_batch")
        # the static object keeps its value only under per_batch
        assert per_frame[0, 4, 4].item() != pytest.approx(per_frame[1, 4, 4].item())
        assert per_batch[0, 4, 4].item() == pytest.approx(per_batch[1, 4, 4].item())

    def test_none_passes_through(self):
        d = torch.rand(2, 8, 8) * 3.0
        assert torch.equal(normalize_depth(d, "none"), d)

    def test_constant_map_does_not_divide_by_zero(self):
        d = torch.full((1, 8, 8), 0.7)
        out = normalize_depth(d, "per_batch")
        assert torch.isfinite(out).all()

    def test_unknown_mode_lists_valid(self):
        with pytest.raises(ValueError) as exc:
            normalize_depth(torch.zeros(1, 4, 4), "bogus")
        for m in NORMALIZE_MODES:
            assert m in str(exc.value)


class TestRemap:
    def test_band_isolation(self):
        d = torch.tensor([0.0, 0.25, 0.5, 0.75, 1.0])
        out = remap_levels(d, 0.25, 0.75)
        assert out[0] == 0.0
        assert out[2] == pytest.approx(0.5)
        assert out[4] == 1.0

    def test_invalid_range(self):
        with pytest.raises(ValueError, match="white_point"):
            remap_levels(torch.zeros(4), 0.8, 0.2)


class TestBlur:
    def test_zero_amount_is_identity(self):
        d = torch.rand(1, 16, 16)
        assert torch.equal(blur_depth(d, 0.0), d)

    def test_softens_a_hard_edge(self):
        d = torch.zeros(1, 32, 32)
        d[:, :, 16:] = 1.0
        out = blur_depth(d, 0.06)
        # the step becomes a ramp: values strictly between 0 and 1 appear
        mid = out[0, 16, 12:20]
        assert ((mid > 0.01) & (mid < 0.99)).any()

    def test_preserves_shape_and_range(self):
        d = torch.rand(3, 24, 32)
        out = blur_depth(d, 0.05)
        assert out.shape == d.shape
        assert out.min() >= -1e-5 and out.max() <= 1.0 + 1e-5

    def test_flat_field_unchanged(self):
        # reflect padding must not darken the edges
        d = torch.full((1, 16, 16), 0.6)
        out = blur_depth(d, 0.08)
        assert torch.allclose(out, d, atol=1e-5)

    def test_resolution_independent(self):
        # same fractional blur on two resolutions gives a similar profile
        small = torch.zeros(1, 32, 32)
        small[:, :, 16:] = 1.0
        big = torch.zeros(1, 64, 64)
        big[:, :, 32:] = 1.0
        a = blur_depth(small, 0.1)[0, 16]
        b = blur_depth(big, 0.1)[0, 32]
        a_r = torch.nn.functional.interpolate(
            a.view(1, 1, -1), size=b.numel(), mode="linear", align_corners=False
        ).view(-1)
        assert torch.allclose(a_r, b, atol=0.08)


class TestConditionPipeline:
    def test_invert_then_remap_uses_final_convention(self):
        d = torch.tensor([[[0.0, 1.0]]])  # (1,1,2)
        out = condition_depth(d, normalize="none", invert=True)
        assert out[0, 0, 0] == pytest.approx(1.0)
        assert out[0, 0, 1] == pytest.approx(0.0)

    def test_full_pipeline_shape_and_range(self):
        d = torch.rand(4, 32, 48) * 5.0 - 1.0
        out = condition_depth(d, normalize="per_batch", invert=True,
                              blur=0.03, black_point=0.1, white_point=0.9)
        assert out.shape == d.shape
        assert out.min() >= 0.0 and out.max() <= 1.0

    def test_conditioning_makes_occlusion_work(self):
        # A near-is-black source with an odd range, conditioned, must drive
        # occlusion correctly end to end.
        raw = torch.full((1, 64, 64), 900.0)  # far background
        raw[:, :, :] = 900.0
        raw[0, :, :] = 100.0  # near occluder everywhere (near-is-black)
        cond = condition_depth(raw, normalize="per_batch", invert=True)
        occ = occlusion_factor(cond[0], 0.5, 0.5, radius=0.2)
        assert occ == 1.0
