# SPDX-License-Identifier: Apache-2.0
"""Translation locks, dynamic triggering, flicker, edge fade, chromatic
fringe, circular completion, procedural orbs, solo, and sub-pixel thickness
anti-aliasing."""

import json
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.schema import validate_preset
from flare.engine import (render_stack, render_batch, trigger_factor,
                          flicker_gain, edge_fade, chromatic_fringe)
from flare.elements import ELEMENT_FUNCTIONS
from flare.schema import PARAM_DEFAULTS

from conftest import argmax_uv

H, W = 96, 144


def render(preset, lights, frame=0):
    return render_stack(validate_preset(preset), lights, H, W, "cpu",
                        torch.float32, frame=frame)


def glow(**kw):
    e = {"type": "glow", "offset": 0.0, "scale": 0.15, "auto_rotate": False,
         "params": {"softness": 0.3, "falloff": 3.0}}
    e.update(kw)
    return e


class TestMoveLocks:
    def test_horizontal_only_pins_vertical_position(self):
        p = {"schema_version": 1, "elements": [glow(offset=0.0, move=[1.0, 0.0])]}
        # light low-left; anchor at frame centre
        f = render(p, [{"x": -0.8, "y": 0.6}]).sum(-1)
        u, v = argmax_uv(f)
        assert v == pytest.approx(0.5, abs=0.03)        # y pinned to anchor
        assert u < 0.35                                  # x still follows

    def test_full_move_is_unchanged(self):
        base = {"schema_version": 1, "elements": [glow(offset=0.4)]}
        locked = json.loads(json.dumps(base))
        locked["elements"][0]["move"] = [1.0, 1.0]
        L = [{"x": -0.5, "y": 0.3}]
        assert torch.allclose(render(base, L), render(locked, L))

    def test_move_validation(self):
        with pytest.raises(ValueError):
            validate_preset({"schema_version": 1, "elements": [glow(move=[2.0, 0.0])]})


class TestTrigger:
    def test_border_trigger_brightens_near_edge(self):
        t = {"mode": "border", "inner": 0.0, "outer": 0.4, "brightness": 3.0}
        p = {"schema_version": 1, "elements": [glow(trigger=t)]}
        centre = render(p, [{"x": 0.0, "y": 0.0}]).sum()
        edge = render(p, [{"x": 0.0, "y": 0.95}]).sum()
        assert edge > centre * 2.0

    def test_center_trigger_scales(self):
        t = {"mode": "center", "inner": 0.0, "outer": 0.5, "scale": 2.0,
             "source": "light"}
        p = {"schema_version": 1, "elements": [glow(trigger=t)]}

        def footprint(f):
            m = f > f.max() * 0.3
            ys, xs = torch.nonzero(m, as_tuple=True)
            return (xs.max() - xs.min()).item()

        near = footprint(render(p, [{"x": 0.0, "y": 0.0}]).sum(-1))
        far = footprint(render(p, [{"x": 1.2, "y": 0.0}]).sum(-1))
        assert near > far * 2.0

    def test_trigger_color_tints(self):
        t = {"mode": "center", "inner": 0.0, "outer": 0.5, "color": [1.0, 0.0, 0.0]}
        p = {"schema_version": 1, "elements": [glow(trigger=t)]}
        f = render(p, [{"x": 0.0, "y": 0.0}])
        assert f[..., 0].sum() > 10 * f[..., 2].sum()

    def test_trigger_factor_geometry(self):
        border = {"mode": "border", "inner": 0.0, "outer": 0.5, "falloff": "linear"}
        assert trigger_factor(border, 0.0, 0.0, 1.5) == 0.0      # deep inside
        assert trigger_factor(border, 0.0, 1.2, 1.5) == 1.0      # outside
        assert 0.0 < trigger_factor(border, 0.0, 0.75, 1.5) < 1.0
        assert trigger_factor(None, 0.0, 0.0, 1.5) == 0.0

    def test_trigger_brightness_is_additive_from_zero(self):
        # the "off until triggered" idiom: intensity 0 + brightness rule
        t = {"mode": "center", "inner": 0.0, "outer": 0.5, "brightness": 1.0}
        p = {"schema_version": 1, "elements": [glow(intensity=0.0, trigger=t)]}
        assert render(p, [{"x": 0.0, "y": 0.0}]).sum() > 0.0
        assert render(p, [{"x": 1.3, "y": 0.0}]).sum() == 0.0

    def test_trigger_none_mode_drops_to_null(self):
        v = validate_preset({"schema_version": 1, "elements": [
            glow(trigger={"mode": "none", "brightness": 5.0})]})
        assert v["elements"][0]["trigger"] is None


class TestFlickerAndEdgeFade:
    def test_flicker_varies_per_frame_and_per_light(self):
        g0 = [flicker_gain(7, 0, f, 1.0, 1.0) for f in range(12)]
        g1 = [flicker_gain(7, 1, f, 1.0, 1.0) for f in range(12)]
        assert max(g0) - min(g0) > 0.1
        assert g0 != g1
        assert all(0.0 <= g <= 2.0 for g in g0)
        assert flicker_gain(7, 0, 5, 0.0, 1.0) == 1.0

    def test_flicker_in_batch_changes_energy(self):
        p = {"schema_version": 1, "global": {"flicker_amount": 1.0},
             "elements": [glow()]}
        out = render_batch(validate_preset(p), [[{"x": 0.0, "y": 0.0}]] * 8,
                           H, W, "cpu", torch.float32)
        e = out.sum(dim=(1, 2, 3))
        assert (e.max() - e.min()) / e.mean() > 0.05

    def test_edge_fade_cuts_light_outside_frame(self):
        assert edge_fade(0.0, 0.0, 1.5, 0.0, 0.5) == 1.0
        assert edge_fade(0.0, 0.9, 1.5, 0.0, 0.5) == 1.0         # still inside
        assert edge_fade(0.0, 1.6, 1.5, 0.0, 0.5) == 0.0         # beyond range
        assert 0.0 < edge_fade(0.0, 1.25, 1.5, 0.0, 0.5) < 1.0
        assert edge_fade(0.0, 3.0, 1.5, 0.0, 0.0) == 1.0         # disabled

    def test_edge_fade_in_render(self):
        p = {"schema_version": 1,
             "global": {"edge_fade_start": 0.0, "edge_fade_range": 0.3},
             "elements": [glow(scale=1.0)]}
        inside = render(p, [{"x": 0.0, "y": 0.0}]).sum()
        gone = render(p, [{"x": 0.0, "y": 1.5}]).sum()
        assert gone == 0.0 and inside > 0.0


class TestFringeCompletionOrbsSolo:
    def test_fringe_separates_channels_at_edge(self):
        img = torch.zeros(H, W, 3)
        img[:, 100:104, :] = 1.0          # white bar off-centre
        out = chromatic_fringe(img, 1.0)
        # red and blue columns of mass move in opposite directions
        xs = torch.arange(W, dtype=torch.float32)
        cr = (out[..., 0].sum(0) * xs).sum() / out[..., 0].sum()
        cb = (out[..., 2].sum(0) * xs).sum() / out[..., 2].sum()
        assert cr > cb + 0.3
        assert torch.allclose(out[..., 1], img[..., 1])

    def test_completion_limits_ring_arc(self):
        xs = torch.linspace(-1.5, 1.5, 128)
        v, u = torch.meshgrid(xs, xs, indexing="ij")
        p = dict(PARAM_DEFAULTS["ring"], radius=1.0, thickness=0.05)
        full = ELEMENT_FUNCTIONS["ring"](u, v, p)
        p["completion"] = 90.0
        arc = ELEMENT_FUNCTIONS["ring"](u, v, p)
        assert 0.15 < arc.sum() / full.sum() < 0.35
        # the arc is centred on +u: left half is dark
        assert arc[:, :40].sum() < 1e-6

    def test_orbs_light_by_proximity(self):
        xs = torch.linspace(-1.5, 1.5, 96)
        v, u = torch.meshgrid(xs, xs, indexing="ij")
        p = dict(PARAM_DEFAULTS["orbs"], count=40, seed=3, illumination=0.3)
        left = ELEMENT_FUNCTIONS["orbs"](u, v, dict(p, _light_local=(-1.0, 0.0)))
        right = ELEMENT_FUNCTIONS["orbs"](u, v, dict(p, _light_local=(1.0, 0.0)))
        assert left[:, :48].sum() > left[:, 48:].sum()
        assert right[:, 48:].sum() > right[:, :48].sum()
        assert torch.isfinite(left).all() and (left >= 0).all()

    def test_orbs_render_through_engine_screen_space(self):
        p = {"schema_version": 1, "elements": [
            {"type": "orbs", "params": {"count": 12, "illumination": 0.4}}]}
        a = render(p, [{"x": -1.0, "y": 0.0}]).sum(-1)
        b = render(p, [{"x": 1.0, "y": 0.0}]).sum(-1)
        # same orbs (lens-locked) but lit on the light's side
        assert a[:, :W // 2].sum() > a[:, W // 2:].sum()
        assert b[:, W // 2:].sum() > b[:, :W // 2].sum()

    def test_solo_isolates_element(self):
        p = {"schema_version": 1, "elements": [
            glow(offset=0.0, id="a"), glow(offset=1.5, id="b", solo=True)]}
        f = render(p, [{"x": -0.6, "y": 0.0}]).sum(-1)
        u, v = argmax_uv(f)
        assert u > 0.6                    # only the far element (t=1.5) rendered

    def test_thin_ray_energy_is_stable_across_resolution(self):
        # a hairline glint should carry about the same energy fraction of
        # the frame at low and high resolution instead of vanishing when it
        # falls between pixels
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glint", "offset": 0, "scale": 0.5, "auto_rotate": False,
             "params": {"points": 12, "length": 0.8, "thickness": 0.0015,
                        "length_jitter": 0.0}}]})
        lo = render_stack(p, [{"x": 0.0, "y": 0.0}], 72, 108, "cpu", torch.float32)
        hi = render_stack(p, [{"x": 0.0, "y": 0.0}], 576, 864, "cpu", torch.float32)
        e_lo = lo.sum() / (72 * 108)
        e_hi = hi.sum() / (576 * 864)
        assert 0.5 < e_lo / e_hi < 2.0


class TestSceneColour:
    def test_scene_color_tints_flare_by_plate(self):
        from test_nodes import run_node
        white = {"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 0.3, "auto_rotate": False,
             "params": {"softness": 0.3, "falloff": 2.0}}]}
        plate = torch.zeros(1, 64, 96, 3)
        plate[0, 20:32, 24:36] = torch.tensor([1.0, 0.35, 0.05])   # orange sun
        _, fp_neutral, _ = run_node(plate, preset_json=json.dumps(white),
                                    light_x=0.31, light_y=0.4, scene_color=0.0)
        _, fp_tinted, _ = run_node(plate, preset_json=json.dumps(white),
                                   light_x=0.31, light_y=0.4, scene_color=1.0)
        rb_neutral = fp_neutral[..., 0].sum() / fp_neutral[..., 2].sum()
        rb_tinted = fp_tinted[..., 0].sum() / fp_tinted[..., 2].sum()
        assert rb_neutral == pytest.approx(1.0, abs=0.05)
        assert rb_tinted > 3.0


class TestTriggerLightModeAndRotation:
    def test_light_mode_fires_when_element_nears_the_light(self):
        # an element far along the axis is far from the light; as the anchor
        # pulls it back toward the light the rule fires
        t = {"mode": "light", "inner": 0.0, "outer": 0.6, "brightness": 2.0}
        p = {"schema_version": 1, "elements": [glow(offset=2.0, trigger=t)]}
        far = render(p, [{"x": -0.7, "y": 0.0, "ax": 0.0, "ay": 0.0}]).sum()
        # anchor almost on the light -> t=2 lands right next to it
        near = render(p, [{"x": -0.7, "y": 0.0, "ax": -0.65, "ay": 0.0}]).sum()
        assert near > far * 1.5

    def test_light_mode_ignores_source_and_is_per_instance(self):
        # source "light" must not collapse a light-mode rule to distance 0:
        # it always measures the ELEMENT's distance to the light, so a chain
        # fires only on the instances that sit near the light
        t = {"mode": "light", "source": "light", "inner": 0.0, "outer": 0.35,
             "brightness": 4.0}
        p = {"schema_version": 1, "elements": [
            glow(offset=0.0, count=3, spread=1.0, scale=0.1, trigger=t)]}
        f = render(p, [{"x": -0.9, "y": 0.0, "ax": 0.0, "ay": 0.0}]).sum(-1)
        near = f[:, :W // 3].sum()          # instance on the light
        far = f[:, 2 * W // 3:].sum()       # instance out at t=2
        # intensity 1 + brightness 4 on the near instance, 1 on the far one
        assert near / far == pytest.approx(5.0, rel=0.02)

    def test_rotation_trigger_turns_the_element(self):
        t = {"mode": "center", "inner": 0.0, "outer": 0.4, "rotation": 90.0}
        p = {"schema_version": 1, "elements": [
            {"type": "streak", "offset": 0.0, "scale": 0.6, "intensity": 1.0,
             "auto_rotate": False, "trigger": t,
             "params": {"length": 0.9, "thickness": 0.02}}]}

        def extent(f):
            m = f > f.max() * 0.25
            ys, xs = torch.nonzero(m, as_tuple=True)
            return ((xs.max() - xs.min()).item(), (ys.max() - ys.min()).item())

        off_w, off_h = extent(render(p, [{"x": 1.3, "y": 0.0}]).sum(-1))
        on_w, on_h = extent(render(p, [{"x": 0.0, "y": 0.0}]).sum(-1))
        assert off_w > off_h * 2        # untriggered: horizontal streak
        assert on_h > on_w * 2          # triggered: rotated upright

    def test_rotation_validation(self):
        with pytest.raises(ValueError):
            validate_preset({"schema_version": 1, "elements": [
                glow(trigger={"mode": "center", "rotation": 5000})]})

    def test_trigger_factor_reference_values(self):
        """Pins the values the editor's JavaScript preview must reproduce.

        web/flarecore_ui.js triggerFactor() is a port of trigger_factor();
        if this test is updated, update that function too.
        """
        A = 16 / 9
        cases = [
            # (mode, falloff, inner, outer, x, y, lx, ly, expected)
            ("border", "smooth", 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0),
            ("border", "smooth", 0.0, 0.5, 0.0, 0.75, 0.0, 0.0, 0.5),
            ("border", "linear", 0.0, 0.5, 0.0, 0.75, 0.0, 0.0, 0.5),
            ("border", "linear", 0.0, 0.5, 0.0, 1.2, 0.0, 0.0, 1.0),
            ("center", "linear", 0.0, 1.0, 0.6, 0.0, 0.0, 0.0, 0.4),
            ("center", "exponential", 0.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.75),
            ("light", "linear", 0.0, 1.0, 0.3, 0.4, 0.0, 0.0, 0.5),
            ("light", "smooth", 0.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.5),
        ]
        for mode, falloff, inner, outer, x, y, lx, ly, expected in cases:
            trig = {"mode": mode, "falloff": falloff, "inner": inner,
                    "outer": outer}
            got = trigger_factor(trig, x, y, A, lx, ly)
            assert got == pytest.approx(expected, abs=1e-6), (mode, falloff, x, y)

    def test_solo_on_a_disabled_element_renders_nothing(self):
        # solo means "only the soloed ones" even when the soloed one is off —
        # the alternative quietly hands the frame back to everything else
        p = {"schema_version": 1, "elements": [
            glow(offset=0.0, enabled=False, solo=True),
            glow(offset=1.5)]}
        assert render(p, [{"x": -0.5, "y": 0.0}]).sum() == 0.0
