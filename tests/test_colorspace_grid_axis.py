# SPDX-License-Identifier: Apache-2.0
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.colorspace import srgb_to_linear, linear_to_srgb
from flare.grid import make_grid, uv_to_grid
from flare.axis import axis_angle, element_center


class TestColorspace:
    def test_roundtrip(self):
        x = torch.linspace(0.0, 1.0, 1024)
        back = linear_to_srgb(srgb_to_linear(x))
        assert torch.allclose(back, x, atol=1e-5)

    def test_known_values(self):
        # sRGB 0.5 decodes to ~0.2140 linear
        assert abs(srgb_to_linear(torch.tensor(0.5)).item() - 0.21404) < 1e-4
        assert srgb_to_linear(torch.tensor(0.0)).item() == 0.0
        assert abs(srgb_to_linear(torch.tensor(1.0)).item() - 1.0) < 1e-6

    def test_hdr_preserved(self):
        # values above 1 survive encode/decode without clamping
        x = torch.tensor([1.5, 4.0, 10.0])
        rt = srgb_to_linear(linear_to_srgb(x))
        assert torch.allclose(rt, x, rtol=1e-4)
        assert linear_to_srgb(torch.tensor(4.0)).item() > 1.0

    def test_no_nan_on_negatives(self):
        x = torch.tensor([-0.5, -0.001])
        assert torch.isfinite(srgb_to_linear(x)).all()
        assert torch.isfinite(linear_to_srgb(x)).all()

    def test_dtype_device_follow_input(self):
        x = torch.rand(8, dtype=torch.float32)
        assert srgb_to_linear(x).dtype == torch.float32


class TestGrid:
    def test_ranges(self):
        x, y = make_grid(100, 200, "cpu", torch.float32)
        assert x.shape == (100, 200)
        aspect = 2.0
        # pixel centers: extremes are half a step inside the bounds
        assert abs(x.min().item() + aspect * (1 - 1 / 200)) < 1e-5
        assert abs(y.max().item() - (1 - 1 / 100)) < 1e-5

    def test_center_symmetry(self):
        x, y = make_grid(64, 64, "cpu", torch.float32)
        assert abs(x.mean().item()) < 1e-6
        assert abs(y.mean().item()) < 1e-6

    def test_uv_to_grid(self):
        gx, gy = uv_to_grid(0.5, 0.5, 100, 200)
        assert gx == pytest.approx(0.0)
        assert gy == pytest.approx(0.0)
        gx, gy = uv_to_grid(0.0, 0.0, 100, 200)
        assert gx == pytest.approx(-2.0)
        assert gy == pytest.approx(-1.0)


class TestAxis:
    def test_element_center_endpoints(self):
        assert element_center(0.7, -0.3, 0.0) == (0.7, -0.3)
        assert element_center(0.7, -0.3, 1.0) == (0.0, 0.0)
        cx, cy = element_center(0.7, -0.3, 2.0)
        assert cx == pytest.approx(-0.7)
        assert cy == pytest.approx(0.3)

    def test_axis_angle(self):
        # light on the left -> axis points right (angle 0)
        assert axis_angle(-1.0, 0.0) == pytest.approx(0.0)
        # light at top -> axis points down (+y in image coords)
        assert axis_angle(0.0, -1.0) == pytest.approx(math.pi / 2)
