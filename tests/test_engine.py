# SPDX-License-Identifier: Apache-2.0
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.schema import validate_preset
from flare.engine import render_stack, render_batch, composite, dispersion_samples
from flare.grid import uv_to_grid


def light_at(u, v, height=256, width=256, brightness=1.0, occlusion=0.0):
    x, y = uv_to_grid(u, v, height, width)
    return {"x": x, "y": y, "brightness": brightness, "occlusion": occlusion}


def preset_of(*elements, **global_over):
    g = dict(global_over)
    return validate_preset({"schema_version": 1, "global": g, "elements": list(elements)})


def argmax_uv(field_hw):
    idx = field_hw.flatten().argmax().item()
    h, w = field_hw.shape
    row, col = divmod(idx, w)
    return (col + 0.5) / w, (row + 0.5) / h


class TestPlacement:
    def test_t0_sits_on_light(self):
        p = preset_of({"type": "glow", "offset": 0.0, "scale": 0.2,
                       "params": {"softness": 0.1, "falloff": 2.0}})
        flare = render_stack(p, [light_at(0.25, 0.7)], 256, 256, "cpu", torch.float32)
        u, v = argmax_uv(flare.sum(-1))
        assert abs(u - 0.25) < 0.01 and abs(v - 0.7) < 0.01

    def test_t1_sits_at_center(self):
        p = preset_of({"type": "glow", "offset": 1.0, "scale": 0.2,
                       "params": {"softness": 0.1, "falloff": 2.0}})
        flare = render_stack(p, [light_at(0.25, 0.7)], 256, 256, "cpu", torch.float32)
        u, v = argmax_uv(flare.sum(-1))
        assert abs(u - 0.5) < 0.01 and abs(v - 0.5) < 0.01

    def test_t2_mirrors_past_center(self):
        p = preset_of({"type": "glow", "offset": 2.0, "scale": 0.2,
                       "params": {"softness": 0.1, "falloff": 2.0}})
        flare = render_stack(p, [light_at(0.25, 0.7)], 256, 256, "cpu", torch.float32)
        u, v = argmax_uv(flare.sum(-1))
        assert abs(u - 0.75) < 0.01 and abs(v - 0.3) < 0.01

    def test_ghost_chain_stays_on_axis(self):
        p = preset_of({"type": "iris", "offset": 0.4, "count": 5, "spread": 0.35,
                       "scale": 0.08, "intensity": 1.0,
                       "params": {"edge_softness": 0.3}})
        lu, lv = 0.2, 0.35
        flare = render_stack(p, [light_at(lu, lv)], 384, 384, "cpu", torch.float32)
        lum = flare.sum(-1)
        # every lit pixel's center must lie near the light->center line
        ys, xs = torch.nonzero(lum > lum.max() * 0.5, as_tuple=True)
        us = (xs.float() + 0.5) / 384
        vs = (ys.float() + 0.5) / 384
        # direction light -> center
        du, dv = 0.5 - lu, 0.5 - lv
        norm = math.hypot(du, dv)
        du, dv = du / norm, dv / norm
        # perpendicular distance from the axis line
        dist = ((us - lu) * dv - (vs - lv) * du).abs()
        assert dist.max() < 0.06


class TestAutoRotate:
    def _streak_field(self, u, v):
        p = preset_of({"type": "streak", "offset": 0.0, "scale": 0.6, "auto_rotate": True,
                       "params": {"length": 1.0, "thickness": 0.02}})
        return render_stack(p, [light_at(u, v)], 256, 256, "cpu", torch.float32).sum(-1)

    def test_streak_follows_axis(self):
        # light left of center: axis horizontal -> streak extends horizontally
        f = self._streak_field(0.25, 0.5)
        assert f[128, 250] > f[250, int(0.25 * 256)] * 10
        # light above center: axis vertical -> streak extends vertically
        f = self._streak_field(0.5, 0.25)
        assert f[250, 128] > f[int(0.25 * 256), 250] * 10


class TestDispersion:
    def test_red_outermost(self):
        p = preset_of({"type": "ring", "offset": 1.0, "scale": 0.5, "dispersion": 2.0,
                       "params": {"radius": 1.0, "thickness": 0.05}})
        flare = render_stack(p, [light_at(0.5, 0.5)], 512, 512, "cpu", torch.float32)
        # radius of the peak along a horizontal scanline from center, per channel
        row = flare[256, 256:, :]
        r_peak = row[:, 0].argmax().item()
        g_peak = row[:, 1].argmax().item()
        b_peak = row[:, 2].argmax().item()
        assert r_peak > g_peak > b_peak

    def test_three_samples_matches_rgb_split(self):
        samples = dispersion_samples(3)
        ws = [s[0] for s in samples]
        colors = [s[1] for s in samples]
        assert ws == [1.0, 0.0, -1.0]
        assert colors[0] == (1.0, 0.0, 0.0)
        assert colors[1] == (0.0, 1.0, 0.0)
        assert colors[2] == (0.0, 0.0, 1.0)

    def test_energy_independent_of_sample_count(self):
        for n in (3, 5, 7, 9):
            colors = [c for _, c in dispersion_samples(n)]
            for ch in range(3):
                assert sum(c[ch] for c in colors) == pytest.approx(1.0)

    def test_zero_dispersion_single_eval(self):
        p0 = preset_of({"type": "ring", "offset": 1.0, "dispersion": 0.0})
        flare = render_stack(p0, [light_at(0.5, 0.5)], 128, 128, "cpu", torch.float32)
        # all three channels identical when no dispersion and white color
        assert torch.allclose(flare[..., 0], flare[..., 1])
        assert torch.allclose(flare[..., 1], flare[..., 2])


class TestWeights:
    def test_brightness_scales_output(self):
        p = preset_of({"type": "glow"})
        dim = render_stack(p, [light_at(0.5, 0.5, brightness=0.25)], 64, 64, "cpu", torch.float32)
        bright = render_stack(p, [light_at(0.5, 0.5, brightness=1.0)], 64, 64, "cpu", torch.float32)
        assert torch.allclose(dim * 4.0, bright, atol=1e-6)

    def test_occlusion_fades_and_shrinks(self):
        # a covered light dims AND contracts: brightness scales by (1-occ)
        # and element size by (1-occ)**OCCLUSION_SHRINK, so total energy
        # falls faster than the brightness fade alone
        p = preset_of({"type": "glow"})
        full = render_stack(p, [light_at(0.5, 0.5, occlusion=0.0)], 64, 64, "cpu", torch.float32)
        half = render_stack(p, [light_at(0.5, 0.5, occlusion=0.5)], 64, 64, "cpu", torch.float32)
        gone = render_stack(p, [light_at(0.5, 0.5, occlusion=1.0)], 64, 64, "cpu", torch.float32)
        assert 0.0 < half.sum() < full.sum() * 0.5
        assert gone.abs().max() == 0.0

    def test_tint_and_intensity(self):
        p = preset_of({"type": "glow"}, intensity=2.0, tint=[1.0, 0.5, 0.25])
        flare = render_stack(p, [light_at(0.5, 0.5)], 64, 64, "cpu", torch.float32)
        assert torch.allclose(flare[..., 0] * 0.5, flare[..., 1], atol=1e-6)
        assert torch.allclose(flare[..., 0] * 0.25, flare[..., 2], atol=1e-6)

    def test_disabled_element_skipped(self):
        p = preset_of({"type": "glow", "enabled": False})
        flare = render_stack(p, [light_at(0.5, 0.5)], 64, 64, "cpu", torch.float32)
        assert flare.abs().max() == 0.0

    def test_count_falloff_chain(self):
        p = preset_of({"type": "glow", "offset": 0.0, "count": 3, "spread": 0.5,
                       "count_falloff": 0.5, "scale": 0.3,
                       "params": {"softness": 0.1, "falloff": 4.0}})
        flare = render_stack(p, [light_at(0.2, 0.5)], 256, 256, "cpu", torch.float32).sum(-1)

        # window max around the expected peaks at t=0, 0.5, 1.0 on the axis
        def peak_near(u):
            c = int(u * 256)
            return flare[120:137, c - 8:c + 9].max()

        p0, p1, p2 = peak_near(0.2), peak_near(0.35), peak_near(0.5)
        assert p0 > p1 > p2
        assert (p1 / p0).item() == pytest.approx(0.5, abs=0.05)
        assert (p2 / p0).item() == pytest.approx(0.25, abs=0.05)


class TestBatchDeviceDtype:
    def test_batch_shape_and_independence(self):
        p = preset_of({"type": "glow"})
        lights = [[light_at(0.3, 0.5)], [light_at(0.7, 0.5)], [], [light_at(0.5, 0.5)]]
        out = render_batch(p, lights, 64, 96, "cpu", torch.float32)
        assert out.shape == (4, 64, 96, 3)
        assert out[2].abs().max() == 0.0  # frame with no lights is black
        assert not torch.allclose(out[0], out[1])

    def test_dtype_follows(self):
        p = preset_of({"type": "glow"})
        out = render_batch(p, [[light_at(0.5, 0.5)]], 32, 32, "cpu", torch.float32)
        assert out.dtype == torch.float32

    def test_determinism(self):
        p = preset_of({"type": "glint"})
        a = render_stack(p, [light_at(0.4, 0.6)], 64, 64, "cpu", torch.float32)
        b = render_stack(p, [light_at(0.4, 0.6)], 64, 64, "cpu", torch.float32)
        assert torch.equal(a, b)

    def test_seed_changes_glint(self):
        p = preset_of({"type": "glint"})
        a = render_stack(p, [light_at(0.4, 0.6)], 64, 64, "cpu", torch.float32, extra_seed=0)
        b = render_stack(p, [light_at(0.4, 0.6)], 64, 64, "cpu", torch.float32, extra_seed=1)
        assert not torch.equal(a, b)


class TestComposite:
    def test_add(self):
        img = torch.rand(2, 8, 8, 3)
        fl = torch.rand(2, 8, 8, 3) * 3.0
        assert torch.equal(composite(img, fl, "add"), img + fl)

    def test_screen_bounded(self):
        img = torch.rand(2, 8, 8, 3)
        fl = torch.rand(2, 8, 8, 3) * 5.0
        out = composite(img, fl, "screen")
        assert out.max() <= 1.0 + 1e-6
        assert (out >= img - 1e-6).all()

    def test_unknown_mode(self):
        with pytest.raises(ValueError, match="blend"):
            composite(torch.zeros(1, 2, 2, 3), torch.zeros(1, 2, 2, 3), "multiply")


def test_all_seven_types_render_without_error():
    p = validate_preset({
        "schema_version": 1,
        "elements": [
            {"type": "glow"}, {"type": "iris"}, {"type": "streak"},
            {"type": "ring"}, {"type": "hoop"}, {"type": "glint"},
            {"type": "spectral"},
        ],
    })
    out = render_stack(p, [light_at(0.35, 0.4)], 128, 192, "cpu", torch.float32)
    assert out.shape == (128, 192, 3)
    assert torch.isfinite(out).all()
    assert out.max() > 0.0
