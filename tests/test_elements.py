# SPDX-License-Identifier: Apache-2.0
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.grid import make_grid
from flare.schema import PARAM_DEFAULTS
from flare.elements import ELEMENT_FUNCTIONS, _smoothstep


def local_grid(n=256, extent=1.5):
    xs = torch.linspace(-extent, extent, n)
    v, u = torch.meshgrid(xs, xs, indexing="ij")
    return u, v


def params_for(etype, **overrides):
    p = dict(PARAM_DEFAULTS[etype])
    p.update(overrides)
    return p


def test_all_types_registered():
    assert set(ELEMENT_FUNCTIONS) == {
        "glow", "iris", "streak", "ring", "hoop", "glint", "spectral", "texture",
        "orbs",
    }


def test_all_elements_finite_nonnegative_shape():
    u, v = local_grid(128)
    for etype, fn in ELEMENT_FUNCTIONS.items():
        p = params_for(etype)
        if etype == "glint":
            p["seed"] = 7
        if etype == "texture":
            p["_texture"] = torch.rand(32, 32)
        field = fn(u, v, p)
        assert field.shape[:2] == u.shape, etype
        assert torch.isfinite(field).all(), etype
        assert (field >= 0).all(), etype


def test_smoothstep_reversed_edges():
    x = torch.tensor([0.0, 0.85, 0.925, 1.0, 1.2])
    y = _smoothstep(1.0, 0.85, x)
    assert y[0] == 1.0
    assert y[1] == 1.0
    assert 0.4 < y[2] < 0.6
    assert y[3] == 0.0
    assert y[4] == 0.0


class TestGlow:
    def test_peak_at_center_and_monotonic(self):
        u, v = local_grid(101)
        field = ELEMENT_FUNCTIONS["glow"](u, v, params_for("glow"))
        assert field.max() == field[50, 50] == pytest.approx(1.0)
        row = field[50, 50:]
        assert (row[1:] <= row[:-1]).all()

    def test_falloff_controls_tail(self):
        u, v = local_grid(101)
        soft = ELEMENT_FUNCTIONS["glow"](u, v, params_for("glow", falloff=0.6))
        hard = ELEMENT_FUNCTIONS["glow"](u, v, params_for("glow", falloff=3.0))
        assert soft[50, 95] > hard[50, 95]


class TestIris:
    def test_inside_one_outside_zero(self):
        u, v = local_grid(256)
        field = ELEMENT_FUNCTIONS["iris"](u, v, params_for("iris", edge_softness=0.05))
        assert field[128, 128] == pytest.approx(1.0)
        assert field[0, 0] == 0.0

    def test_high_blade_count_approximates_circle(self):
        u, v = local_grid(256)
        field = ELEMENT_FUNCTIONS["iris"](u, v, params_for("iris", blades=64, edge_softness=0.05))
        r = torch.sqrt(u * u + v * v)
        inside = r < 0.9
        outside = r > 1.05
        assert field[inside].min() > 0.99
        assert field[outside].max() < 1e-6

    def test_hollow_punches_center(self):
        u, v = local_grid(256)
        field = ELEMENT_FUNCTIONS["iris"](u, v, params_for("iris", hollow=0.5))
        assert field[128, 128] == 0.0  # center punched out
        # band between hollow and outer edge still lit
        assert field[128, 128 + int(0.75 / 3.0 * 256)] > 0.9

    def test_rotational_symmetry(self):
        u, v = local_grid(257)
        p = params_for("iris", blades=6, edge_softness=0.1)
        field = ELEMENT_FUNCTIONS["iris"](u, v, p)
        # rotating the coordinates by one sector leaves the field unchanged
        a = 2 * math.pi / 6
        ur = u * math.cos(a) + v * math.sin(a)
        vr = -u * math.sin(a) + v * math.cos(a)
        rotated = ELEMENT_FUNCTIONS["iris"](ur, vr, p)
        assert torch.allclose(field, rotated, atol=1e-5)


class TestStreak:
    def test_anisotropy(self):
        u, v = local_grid(101)
        field = ELEMENT_FUNCTIONS["streak"](u, v, params_for("streak"))
        # far along u still bright, same distance along v is dark
        assert field[50, 90] > 0.2
        assert field[90, 50] < 1e-4

    def test_count_adds_rotated_copies(self):
        u, v = local_grid(101)
        cross = ELEMENT_FUNCTIONS["streak"](u, v, params_for("streak", count=2))
        # vertical arm now bright too
        assert cross[90, 50] > 0.2


class TestRing:
    def test_peak_at_radius(self):
        u, v = local_grid(401, extent=1.0)
        field = ELEMENT_FUNCTIONS["ring"](u, v, params_for("ring", radius=0.5, thickness=0.05))
        r = torch.sqrt(u * u + v * v)
        peak_r = r.flatten()[field.flatten().argmax()]
        assert abs(peak_r.item() - 0.5) < 0.01
        assert field[200, 200] < 1e-6  # center dark


class TestHoop:
    def test_fades_along_axis(self):
        u, v = local_grid(401, extent=1.0)
        p = params_for("hoop", radius=0.6, thickness=0.15, angular_falloff=1.0)
        field = ELEMENT_FUNCTIONS["hoop"](u, v, p)
        # on the local +u axis (the flare axis) the hoop vanishes
        on_axis = field[200, 200 + int(0.6 / 2.0 * 401)]
        perp = field[200 + int(0.6 / 2.0 * 401), 200]
        assert on_axis < 1e-4
        assert perp > 0.9


class TestGlint:
    def test_deterministic_for_seed(self):
        u, v = local_grid(101)
        p = params_for("glint", seed=42)
        p["seed"] = 42
        a = ELEMENT_FUNCTIONS["glint"](u, v, p)
        b = ELEMENT_FUNCTIONS["glint"](u, v, p)
        assert torch.equal(a, b)

    def test_different_seed_differs(self):
        u, v = local_grid(101)
        pa = params_for("glint")
        pa["seed"] = 1
        pb = params_for("glint")
        pb["seed"] = 2
        a = ELEMENT_FUNCTIONS["glint"](u, v, pa)
        b = ELEMENT_FUNCTIONS["glint"](u, v, pb)
        assert not torch.equal(a, b)

    def test_ray_count_visible(self):
        u, v = local_grid(361)
        p = params_for("glint", points=4, length_jitter=0.0)
        p["seed"] = 0
        field = ELEMENT_FUNCTIONS["glint"](u, v, p)
        # rays at 0/90/180/270: at u=1.0 with length 0.5 a ray reads
        # exp(-2) ~ 0.135 on axis, and essentially zero at 45 degrees
        assert field[180, 300] > 0.12
        assert field[300, 180] > 0.12
        assert field[280, 280] < 0.02


class TestSpectral:
    def test_ring_and_iris_shapes(self):
        u, v = local_grid(128)
        ring_field = ELEMENT_FUNCTIONS["spectral"](u, v, params_for("spectral"))
        iris_field = ELEMENT_FUNCTIONS["spectral"](u, v, params_for("spectral", shape="iris"))
        assert not torch.allclose(ring_field, iris_field)
