# SPDX-License-Identifier: Apache-2.0
"""Video-readiness tests: temporal tracking, keyframes, stability across
frame sequences, and the QA-audit regressions (NaN presets, plateau
detection, blur crash, per-instance seeds)."""

import json
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flare.schema import load_preset, validate_preset
from flare.engine import render_stack, render_batch, element_seed
from flare.detect import detect_lights
from flare.depth import blur_depth, temporal_smooth_depth
from flare.track import track_lights, parse_keyframes, interpolate_keyframes
from flare.texture_prep import center_on_energy

from conftest import load_package, argmax_uv

PKG = load_package()
from test_nodes import run_node  # noqa: E402


class TestSchemaHardening:
    def test_nan_and_infinity_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            load_preset('{"schema_version":1,"elements":[{"type":"glow","scale":NaN}]}')
        with pytest.raises(ValueError, match="finite"):
            load_preset('{"schema_version":1,"elements":[{"type":"glow","intensity":Infinity}]}')
        with pytest.raises(ValueError, match="finite"):
            load_preset('{"schema_version":1,"global":{"tint":[NaN,1,1]},"elements":[]}')

    def test_negative_color_rejected(self):
        with pytest.raises(ValueError, match="color"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "glow", "color": [-2.0, 0, 0]}]})

    def test_hdr_color_still_allowed(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow", "color": [4.0, 2.0, 1.0]}]})
        assert p["elements"][0]["color"][0] == 4.0

    def test_dispersion_capped_before_coordinate_collapse(self):
        with pytest.raises(ValueError, match="dispersion"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "glow", "dispersion": 20}]})

    def test_count_and_points_bounded(self):
        with pytest.raises(ValueError, match="count"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "glow", "count": 100000}]})
        with pytest.raises(ValueError, match="points"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "glint", "params": {"points": 200000}}]})


class TestDetectPlateau:
    def test_saturated_disc_centres_not_corner(self):
        # a clipped disc: every pixel inside is 1.0 -> a plateau of tied peaks
        img = torch.zeros(1, 128, 128, 3)
        yy, xx = torch.meshgrid(torch.arange(128), torch.arange(128), indexing="ij")
        disc = ((yy - 64) ** 2 + (xx - 80) ** 2) < 12 ** 2
        img[0][disc] = 1.0
        lights = detect_lights(img, threshold=0.8, max_lights=1,
                               min_separation=0.2)
        light = lights[0][0]
        # centre of the disc is (u=80.5/128, v=64.5/128); the old top-left
        # tie-break landed ~12px off
        assert abs(light["u"] - 80.5 / 128) < 3 / 128
        assert abs(light["v"] - 64.5 / 128) < 3 / 128

    def test_blowout_capped_and_fast(self):
        img = torch.ones(1, 256, 256, 3)  # every pixel is a tied peak
        lights = detect_lights(img, threshold=0.5, max_lights=4)
        assert len(lights[0]) >= 1  # did not hang, returned something sane

    def test_moving_light_positions_track_smoothly(self):
        frames = []
        for f in range(10):
            img = torch.zeros(96, 96, 3)
            cx = 20 + f * 6
            img[46:51, cx - 2:cx + 3] = 1.0
            frames.append(img)
        batch = torch.stack(frames)
        det = detect_lights(batch, threshold=0.5)
        us = [d[0]["u"] for d in det]
        deltas = [us[i + 1] - us[i] for i in range(len(us) - 1)]
        assert all(d > 0 for d in deltas)          # monotonic travel
        mean = sum(deltas) / len(deltas)
        assert all(abs(d - mean) < 0.02 for d in deltas)  # no jumps


class TestTracker:
    def test_stable_ids_and_smoothing(self):
        det = [[{"u": 0.2 + 0.01 * f + (0.004 if f % 2 else -0.004),
                 "v": 0.5, "brightness": 1.0}] for f in range(20)]
        out = track_lights(det, smoothing=0.7, fade=1)
        tids = {light["tid"] for frame in out for light in frame}
        assert tids == {0}
        # raw consecutive steps alternate 0.002 / 0.018; smoothing must pull
        # the worst step well below the raw worst
        raw_worst_step = 0.018
        smoothed = [frame[0]["u"] for frame in out[5:]]
        wobble = max(abs(smoothed[i + 1] - smoothed[i]) for i in range(len(smoothed) - 1))
        assert wobble < raw_worst_step * 0.75

    def test_dropout_rides_through_hold(self):
        det = [[{"u": 0.5, "v": 0.5, "brightness": 1.0}] for _ in range(10)]
        det[4] = []  # one-frame detector dropout
        out = track_lights(det, hold=2, fade=2)
        assert len(out[4]) == 1          # the light survives the gap
        assert out[4][0]["tid"] == 0
        assert len({l["tid"] for f in out for l in f}) == 1  # no re-birth

    def test_fade_in_and_out(self):
        det = [[] for _ in range(12)]
        for f in range(3, 9):
            det[f] = [{"u": 0.5, "v": 0.5, "brightness": 1.0}]
        out = track_lights(det, hold=0, fade=3)
        b = [frame[0]["brightness"] if frame else 0.0 for frame in out]
        assert b[3] < b[4] < b[5]        # ramps in
        assert b[5] == pytest.approx(1.0)
        tail = b[9:]
        assert any(0 < x < 1 for x in tail)  # ramps out, not a strobe
        assert b[2] == 0.0

    def test_crossing_lights_keep_identity(self):
        det = []
        for f in range(11):
            t = f / 10
            det.append([
                {"u": 0.2 + 0.6 * t, "v": 0.45, "brightness": 1.0},
                {"u": 0.8 - 0.6 * t, "v": 0.55, "brightness": 0.9},
            ])
        out = track_lights(det, smoothing=0.3, max_jump=0.15, fade=1)
        # track 0 started left and must end right (it kept its identity
        # through the crossing instead of swapping)
        first = next(l for l in out[0] if l["tid"] == 0)
        last = next(l for l in out[-1] if l["tid"] == 0)
        assert first["u"] < 0.35 and last["u"] > 0.65


class TestKeyframes:
    def test_parse_and_interpolate(self):
        keys = parse_keyframes(" 0: 0.1,0.2 ; 10: 0.9, 0.6 ")
        assert keys == [(0, 0.1, 0.2), (10, 0.9, 0.6)]
        path = interpolate_keyframes("0:0.0,0.0; 10:1.0,1.0", 11, "linear")
        assert path[0] == (0.0, 0.0)
        assert path[5][0] == pytest.approx(0.5)
        assert path[10] == (1.0, 1.0)

    def test_smooth_easing_eases(self):
        path = interpolate_keyframes("0:0.0,0.0; 10:1.0,0.0", 11, "smooth")
        # first step smaller than the middle step
        assert (path[1][0] - path[0][0]) < (path[6][0] - path[5][0])

    def test_holds_outside_keys(self):
        path = interpolate_keyframes("5:0.3,0.3; 8:0.7,0.7", 12)
        assert path[0] == (0.3, 0.3)
        assert path[11] == (0.7, 0.7)

    def test_errors_name_the_chunk(self):
        with pytest.raises(ValueError, match="bad keyframe"):
            parse_keyframes("0: 0.1")
        with pytest.raises(ValueError, match="duplicate"):
            parse_keyframes("0:0,0; 0:1,1")


class TestDepthVideo:
    def test_blur_no_longer_crashes_on_portrait(self):
        out = blur_depth(torch.rand(1, 1216, 832), 0.25)
        assert out.shape == (1, 1216, 832)
        assert torch.isfinite(out).all()

    def test_temporal_smooth_stills_noise_keeps_motion(self):
        b = 24
        ramp = torch.linspace(0, 1, b).view(b, 1, 1).expand(b, 8, 8).clone()
        noisy = ramp + torch.randn(b, 8, 8) * 0.05
        smoothed = temporal_smooth_depth(noisy, 0.8)
        # noise around the ramp shrinks
        assert (smoothed - ramp).abs().mean() < (noisy - ramp).abs().mean() * 0.7
        # motion is preserved end to end (zero-phase, no lag collapse)
        assert smoothed[-1].mean() - smoothed[0].mean() > 0.7

    def test_amount_zero_is_identity(self):
        d = torch.rand(6, 8, 8)
        assert torch.equal(temporal_smooth_depth(d, 0.0), d)


class TestEngineVideo:
    def test_count_instances_get_distinct_jitter(self):
        seeds = {element_seed(0, {"id": ""}, 3, i) for i in range(5)}
        assert len(seeds) == 5

    def test_id_survives_reorder(self):
        elem = {"id": "my_glint"}
        assert element_seed(7, elem, 0) == element_seed(7, elem, 12)

    def test_adjacent_seeds_do_not_collide(self):
        a = element_seed(0, {"id": ""}, 1)
        b = element_seed(1, {"id": ""}, 0)
        assert a != b

    def test_render_batch_prealloc_matches_per_frame(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow"}, {"type": "glint"},
            {"type": "iris", "offset": 0.8, "dispersion": 0.5}]})
        lights = [[{"x": -0.4, "y": 0.1}], [{"x": 0.2, "y": -0.3}]]
        batch = render_batch(p, lights, 48, 64, "cpu", torch.float32)
        for i in range(2):
            single = render_stack(p, lights[i], 48, 64, "cpu", torch.float32)
            assert torch.allclose(batch[i], single, atol=1e-6)

    def test_batch_determinism(self):
        p = validate_preset({"schema_version": 1, "elements": [{"type": "glint"}]})
        lights = [[{"x": 0.1, "y": 0.2}]] * 3
        a = render_batch(p, lights, 32, 32, "cpu", torch.float32)
        b = render_batch(p, lights, 32, 32, "cpu", torch.float32)
        assert torch.equal(a, b)
        assert torch.equal(a[0], a[1])  # identical lights -> identical frames


class TestFlareLightsInput:
    def _lights(self, n):
        return [[{"u": 0.2 + 0.05 * f, "v": 0.4, "brightness": 1.0}]
                for f in range(n)]

    def test_lights_input_drives_positions(self):
        img = torch.zeros(4, 64, 96, 3)
        out, flare_pass, _ = run_node(img, lights=self._lights(4),
                                      preset_json=json.dumps({
                                          "schema_version": 1, "elements": [
                                              {"type": "glow", "scale": 0.2,
                                               "params": {"softness": 0.1,
                                                          "falloff": 2.5}}]}))
        for f in range(4):
            u, v = argmax_uv(flare_pass[f].sum(-1))
            assert abs(u - (0.2 + 0.05 * f)) < 0.03
            assert abs(v - 0.4) < 0.03

    def test_single_frame_broadcasts(self):
        img = torch.zeros(3, 32, 32, 3)
        out, fp, _ = run_node(img, lights=self._lights(1))
        assert torch.allclose(fp[0], fp[1])

    def test_mismatch_is_loud(self):
        img = torch.zeros(5, 32, 32, 3)
        with pytest.raises(ValueError, match="covers 2 frames"):
            run_node(img, lights=self._lights(2))

    def test_per_light_anchor(self):
        img = torch.zeros(1, 96, 96, 3)
        lights = [[{"u": 0.2, "v": 0.2, "au": 0.8, "av": 0.8}]]
        preset = json.dumps({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 1.0, "scale": 0.15,
             "params": {"softness": 0.1, "falloff": 2.5}}]})
        out, fp, _ = run_node(img, lights=lights, preset_json=preset)
        u, v = argmax_uv(fp[0].sum(-1))
        assert abs(u - 0.8) < 0.03 and abs(v - 0.8) < 0.03


class TestOcclusionSequence:
    def test_no_pop_through_render_node(self):
        # light slides behind a wall across 12 frames; flare energy must fall
        # monotonically-ish with no single-frame collapse-and-return
        frames, depths = [], []
        for f in range(12):
            img = torch.zeros(64, 96, 3)
            depth = torch.zeros(64, 96, 3)
            edge = int(96 * (0.55 - 0.03 * f))
            depth[:, :edge] = 0.9  # near wall advancing right-to-left... (edge shrinks)
            frames.append(img)
            depths.append(depth)
        img_b = torch.stack(frames)
        dep_b = torch.stack(depths)
        lights = [[{"u": 0.35, "v": 0.5, "brightness": 1.0}]] * 12
        out, fp, _ = run_node(img_b, depth=dep_b,
                              lights=[lights[0]] * 12,
                              occlusion_radius=0.12)
        energy = [fp[f].sum().item() for f in range(12)]
        # wall recedes from the light -> energy must be non-decreasing
        for a, b in zip(energy, energy[1:]):
            assert b >= a - 1e-3
        assert energy[-1] > energy[0]


class TestTextureCenterShift:
    def test_no_wraparound_ghost(self):
        img = torch.zeros(64, 64, 3)
        img[40:56, 40:56] = 1.0  # bright block lower-right
        out = center_on_energy(img)
        # content moved toward centre and nothing wrapped to the far corner
        assert out[:8, :8].sum() == 0.0
        assert out[56:, 56:].sum() == 0.0
        lum = out.sum(-1)
        ys, xs = torch.nonzero(lum > 0.5, as_tuple=True)
        assert abs(ys.float().mean() - 31.5) < 2.0
        assert abs(xs.float().mean() - 31.5) < 2.0


class TestVideoNodes:
    def test_flare_track_node(self):
        frames = []
        for f in range(8):
            img = torch.zeros(64, 96, 3)
            cx = 15 + f * 8
            img[30:34, cx:cx + 4] = 1.0
            frames.append(img)
        batch = torch.stack(frames)
        node = PKG.NODE_CLASS_MAPPINGS["FlareTrack"]()
        lights, overlay, report = node.track(
            batch, detect_threshold=0.5, detect_max_lights=1,
            smoothing=0.5, max_jump=0.2, hold_frames=2, fade_frames=2)
        assert len(lights) == 8
        assert overlay.shape == batch.shape
        assert "1 tracks" in report
        us = [f[0]["u"] for f in lights]
        assert us == sorted(us)

    def test_flare_keyframes_node(self):
        node = PKG.NODE_CLASS_MAPPINGS["FlareKeyframes"]()
        lights, n = node.make(10, "0: 0.1,0.5; 9: 0.9,0.5",
                              "0: 0.5,0.5", "linear", 1.0)
        assert n == 10 and len(lights) == 10
        assert lights[0][0]["u"] == pytest.approx(0.1)
        assert lights[9][0]["u"] == pytest.approx(0.9)
        assert lights[4][0]["au"] == pytest.approx(0.5)

    def test_keyframes_take_count_from_image(self):
        node = PKG.NODE_CLASS_MAPPINGS["FlareKeyframes"]()
        lights, n = node.make(999, "0: 0.5,0.5", "", "smooth", 1.0,
                              image=torch.zeros(6, 8, 8, 3))
        assert n == 6 and len(lights) == 6
