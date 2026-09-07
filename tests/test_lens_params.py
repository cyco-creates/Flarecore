# SPDX-License-Identifier: Apache-2.0
"""Element controls drawn from a survey of real cine lens flare tests.

Each one exists because a shape kept recurring across lenses and could not
be expressed: a companion ghost's own streak sitting below the source, a
hotspot welded to the frame edge, the bow in an anamorphic line, a dashed
streak, a ghost lit only on the edge facing the source, the crescent a
barrel cuts out of a ghost, and the ring of dusty spectral specks around a
front-element reflection.
"""

import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.engine import render_stack
from flare.elements import ELEMENT_FUNCTIONS
from flare.grid import make_grid
from flare.schema import PARAM_DEFAULTS, validate_preset

H, W = 128, 128


def render(elements, lights=None, glob=None):
    preset = {"schema_version": 1, "elements": elements}
    if glob:
        preset["global"] = glob
    lights = lights or [{"x": 0.0, "y": 0.0, "brightness": 1.0}]
    return render_stack(validate_preset(preset), lights, H, W, "cpu",
                        torch.float32)


def peak_xy(field):
    """Brightest point of an (H, W[, 3]) render, in grid coordinates."""
    lum = field.sum(-1) if field.dim() == 3 else field
    i = int(lum.argmax())
    py, px = i // lum.shape[1], i % lum.shape[1]
    x, y = make_grid(lum.shape[0], lum.shape[1], "cpu", torch.float32)
    return float(x[py, px]), float(y[py, px])


def field_of(etype, params=None, extent=2.0, n=256):
    """Evaluate one element function over local coordinates in [-extent, extent]."""
    p = dict(PARAM_DEFAULTS[etype])
    p.update(params or {})
    p.setdefault("seed", 7)
    p.setdefault("irregular", 0.0)
    p.setdefault("_px", 0.0)
    t = torch.linspace(-extent, extent, n)
    v, u = torch.meshgrid(t, t, indexing="ij")
    return ELEMENT_FUNCTIONS[etype](u, v, p), u, v


class TestShift:
    """A companion ghost carries its own anamorphic streak a fixed distance
    below the source -- seen on three Nanomorph focal lengths, on the Atlas
    and on the Viltrox. The offset is in SCREEN space: it must not swing
    with the flare axis the way `offset` does."""

    def _dot(self, **extra):
        return dict({"type": "glow", "offset": 0.0, "scale": 0.05,
                     "auto_rotate": False,
                     "params": {"softness": 0.2, "falloff": 3.0}}, **extra)

    def test_moves_the_element_down_the_screen(self):
        base = render([self._dot()])
        moved = render([self._dot(shift=[0.0, 0.6])])
        bx, by = peak_xy(base)
        mx, my = peak_xy(moved)
        assert mx == pytest.approx(bx, abs=0.03)
        assert my - by == pytest.approx(0.6, abs=0.03)

    def test_is_screen_space_not_axis_space(self):
        """With the light on the far side the axis reverses; a shift must
        still go down the screen, unlike `offset`."""
        left = render([self._dot(shift=[0.0, 0.5])],
                      lights=[{"x": -0.8, "y": 0.0, "brightness": 1.0}])
        right = render([self._dot(shift=[0.0, 0.5])],
                       lights=[{"x": 0.8, "y": 0.0, "brightness": 1.0}])
        assert peak_xy(left)[1] == pytest.approx(0.5, abs=0.05)
        assert peak_xy(right)[1] == pytest.approx(0.5, abs=0.05)

    def test_defaults_to_no_shift(self):
        assert validate_preset({"schema_version": 1,
                                "elements": [{"type": "glow"}]}
                               )["elements"][0]["shift"] == [0.0, 0.0]


class TestPin:
    """The hotspot welded to the top frame edge that tracks the light's x --
    on the Atlas, every Nanomorph and every Proteus. One coordinate is
    fixed to the frame, the other keeps following the light."""

    def _dot(self, **extra):
        return dict({"type": "glow", "offset": 0.0, "scale": 0.05,
                     "auto_rotate": False,
                     "params": {"softness": 0.2, "falloff": 3.0}}, **extra)

    def test_pins_y_to_the_top_edge_while_x_follows_the_light(self):
        for lx in (-0.6, 0.0, 0.7):
            out = render([self._dot(pin=[None, -1.0])],
                         lights=[{"x": lx, "y": 0.4, "brightness": 1.0}])
            px, py = peak_xy(out)
            assert py == pytest.approx(-1.0, abs=0.05), lx
            assert px == pytest.approx(lx, abs=0.05), lx

    def test_pinning_x_leaves_y_free(self):
        out = render([self._dot(pin=[0.5, None])],
                     lights=[{"x": -0.7, "y": 0.6, "brightness": 1.0}])
        px, py = peak_xy(out)
        assert px == pytest.approx(0.5, abs=0.05)
        assert py == pytest.approx(0.6, abs=0.05)

    def test_defaults_to_unpinned(self):
        assert validate_preset({"schema_version": 1,
                                "elements": [{"type": "glow"}]}
                               )["elements"][0]["pin"] == [None, None]


class TestShade:
    """The Zeiss Radiance ghost is bright only on the edge facing the
    source, which reads as a comet and as a cusped crescent once the frame
    cuts it. A linear ramp across the element's own axis."""

    def _iris(self, **extra):
        return dict({"type": "iris", "offset": 0.0, "scale": 0.5,
                     "auto_rotate": False,
                     "params": {"blades": 64, "edge_softness": 0.2}}, **extra)

    def test_puts_the_light_on_one_side(self):
        out = render([self._iris(shade=1.0)])
        x, _ = make_grid(H, W, "cpu", torch.float32)
        lum = out.sum(-1)
        centroid = float((lum * x).sum() / lum.sum())
        assert centroid > 0.1, centroid

    def test_negative_shade_flips_the_lit_edge(self):
        x, _ = make_grid(H, W, "cpu", torch.float32)
        def centroid(s):
            lum = render([self._iris(shade=s)]).sum(-1)
            return float((lum * x).sum() / lum.sum())
        assert centroid(-1.0) == pytest.approx(-centroid(1.0), abs=0.02)

    def test_zero_is_the_unshaded_element(self):
        plain = render([self._iris()])
        shaded = render([self._iris(shade=0.0)])
        assert torch.allclose(plain, shaded)


class TestStreakCurve:
    """Every Laowa line sags: the streak is a shallow arc, not a rule."""

    def _profile(self, curve):
        """v of the streak's brightest point at each u."""
        f, u, v = field_of("streak", {"length": 3.0, "thickness": 0.05,
                                      "curve": curve}, extent=1.5)
        return [float(v[int(f[:, c].argmax()), c]) for c in range(f.shape[1])]

    def test_straight_by_default(self):
        assert max(abs(t) for t in self._profile(0.0)) < 0.02

    def test_bows_away_from_the_axis_at_the_ends(self):
        p = self._profile(1.0)
        mid = p[len(p) // 2]
        assert abs(mid) < 0.02
        assert p[0] > 0.05 and p[-1] > 0.05

    def test_sign_flips_the_bow(self):
        assert self._profile(-1.0)[0] == pytest.approx(-self._profile(1.0)[0],
                                                       abs=0.02)


class TestStreakDash:
    """The Ultra Panatar's line is broken into segments with gaps."""

    def _along(self, dash):
        f, u, v = field_of("streak", {"length": 2.0, "thickness": 0.06,
                                      "dash": dash}, extent=1.5)
        return f[f.shape[0] // 2]

    def test_solid_by_default(self):
        line = self._along(0.0)
        assert float(line.min() / line.max()) > 0.2

    def test_gaps_appear(self):
        line = self._along(0.6)
        assert float(line.min() / line.max()) < 0.02

    def test_energy_falls_as_gaps_widen(self):
        assert float(self._along(0.8).sum()) < float(self._along(0.2).sum())


class TestCrescent:
    """A ghost the barrel cuts into a crescent: the Xelmus's giant arc, the
    Hawk's cat-eye disc, the edge crescents on the Kowa and the Baltar."""

    def test_ring_keeps_one_side_only(self):
        f, u, v = field_of("ring", {"radius": 1.0, "thickness": 0.08,
                                    "crescent": 0.5})
        left = float(f[:, u[0] < -0.8].sum())
        right = float(f[:, u[0] > 0.8].sum())
        assert right > 10 * left, (left, right)

    def test_zero_leaves_the_ring_whole(self):
        whole, _, _ = field_of("ring", {"radius": 1.0, "thickness": 0.08})
        same, _, _ = field_of("ring", {"radius": 1.0, "thickness": 0.08,
                                       "crescent": 0.0})
        assert torch.allclose(whole, same)

    def test_more_crescent_removes_more(self):
        e = [float(field_of("ring", {"radius": 1.0, "thickness": 0.08,
                                     "crescent": c})[0].sum())
             for c in (0.2, 0.5, 0.8)]
        assert e[0] > e[1] > e[2]

    def test_iris_takes_a_crescent_too(self):
        f, u, v = field_of("iris", {"blades": 64, "crescent": 0.5})
        assert float(f[:, u[0] > 0.5].sum()) > 10 * float(f[:, u[0] < -0.5].sum())


class TestOrbRing:
    """The Hawk's front-element disc is rimmed with dusty specks, each
    diffracting its own colour."""

    def test_orbs_gather_on_the_rim(self):
        f, u, v = field_of("orbs", {"count": 60, "size": 0.05, "spread": 1.0,
                                    "size_jitter": 0.0, "ring": 1.0,
                                    "illumination": 100.0}, extent=1.4)
        r = torch.sqrt(u * u + v * v)
        lit = f > f.max() * 0.5
        assert float(r[lit].mean()) == pytest.approx(1.0, abs=0.15)
        assert float((r[lit] < 0.5).float().mean()) < 0.02

    def test_disc_fill_is_the_default(self):
        f, u, v = field_of("orbs", {"count": 60, "size": 0.05, "spread": 1.0,
                                    "size_jitter": 0.0,
                                    "illumination": 100.0}, extent=1.4)
        r = torch.sqrt(u * u + v * v)
        lit = f > f.max() * 0.5
        assert float((r[lit] < 0.5).float().mean()) > 0.05

    def test_spectral_orbs_carry_colour(self):
        f, _, _ = field_of("orbs", {"count": 40, "size": 0.06, "spread": 1.0,
                                    "spectral": 1.0, "illumination": 100.0})
        assert f.dim() == 3 and f.shape[-1] == 3
        lit = f.sum(-1) > f.sum(-1).max() * 0.3
        hue = (f[..., 0] - f[..., 2])[lit]
        assert float(hue.max() - hue.min()) > 0.3

    def test_plain_orbs_stay_single_channel(self):
        f, _, _ = field_of("orbs", {"count": 8})
        assert f.dim() == 2


class TestSchemaGuards:
    def test_ranges_are_enforced(self):
        bad = [
            {"type": "glow", "shift": [0.0, 99.0]},
            {"type": "glow", "shift": [0.0]},
            {"type": "glow", "pin": [0.0, 0.0, 0.0]},
            {"type": "glow", "shade": 4.0},
            {"type": "streak", "params": {"curve": 9.0}},
            {"type": "streak", "params": {"dash": 1.4}},
            {"type": "ring", "params": {"crescent": -0.5}},
            {"type": "orbs", "params": {"ring": 2.0}},
        ]
        for elem in bad:
            with pytest.raises(ValueError):
                validate_preset({"schema_version": 1, "elements": [elem]})

    def test_pin_accepts_null_and_numbers(self):
        e = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow", "pin": [None, -1.0]}]})["elements"][0]
        assert e["pin"] == [None, -1.0]
