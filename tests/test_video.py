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
from flare.colorspace import srgb_to_linear
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

    def test_lost_track_coasts_on_velocity(self):
        # light moves right at 0.03/frame, vanishes behind an occluder for
        # 4 frames, re-appears further along: the coasted track must keep
        # moving (not freeze at the edge) and re-acquire as the SAME track
        det = []
        for f in range(14):
            u = 0.2 + 0.03 * f
            det.append([] if 6 <= f <= 9 else
                       [{"u": u, "v": 0.5, "brightness": 1.0}])
        out = track_lights(det, smoothing=0.3, max_jump=0.1, hold=3, fade=3)
        # during the gap the position keeps advancing
        gap_us = [frame[0]["u"] for frame in out[6:10] if frame]
        assert len(gap_us) >= 3
        assert all(b > a for a, b in zip(gap_us, gap_us[1:]))
        # one identity across the whole shot
        assert {l["tid"] for f in out for l in f} == {0}

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


class TestOcclusionSmoothing:
    def test_smooth_series_softens_a_cut(self):
        from flare.track import smooth_series
        series = [0.0] * 6 + [1.0] * 6
        s = smooth_series(series, 0.6)
        steps = [abs(b - a) for a, b in zip(s, s[1:])]
        assert max(steps) < 0.6           # the 1.0 cliff became a ramp
        assert s[0] < 0.2 and s[-1] > 0.8  # endpoints still converge

    def test_render_node_smooths_tracked_occlusion(self):
        # a tracked light whose occlusion snaps 0 -> 1 mid-clip: flare energy
        # must fall over multiple frames, not one
        img = torch.zeros(10, 64, 96, 3)
        depth = torch.zeros(10, 64, 96, 3)
        depth[5:] = 0.9  # world becomes fully near from frame 5 on
        lights = [[{"u": 0.5, "v": 0.5, "brightness": 1.0, "tid": 0}]
                  for _ in range(10)]
        out, fp, _ = run_node(img, depth=depth, lights=lights,
                              occlusion_smooth=0.6, occlusion_radius=0.1)
        e = [fp[f].sum().item() for f in range(10)]
        peak = max(e)
        deltas = [abs(b - a) / peak for a, b in zip(e, e[1:])]
        # occlusion now shrinks as well as dims, so energy falls with a
        # higher exponent — the smoothed fade still must beat the raw cliff
        assert max(deltas) < 0.75
        # without tid the same setup keeps the hard cut (behavior preserved)
        lights_plain = [[{"u": 0.5, "v": 0.5, "brightness": 1.0}]
                        for _ in range(10)]
        out2, fp2, _ = run_node(img, depth=depth, lights=lights_plain,
                                occlusion_smooth=0.6, occlusion_radius=0.1)
        e2 = [fp2[f].sum().item() for f in range(10)]
        d2 = [abs(b - a) / peak for a, b in zip(e2, e2[1:])]
        assert max(d2) > 0.9
        assert max(deltas) < max(d2) * 0.8


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


class TestElementBlurMaskShrink:
    def _glow(self, **over):
        elem = {"type": "glow", "offset": 0.0, "scale": 0.25,
                "auto_rotate": False, "params": {"softness": 0.2, "falloff": 2.5}}
        elem.update(over)
        return validate_preset({"schema_version": 1, "elements": [elem]})

    def test_schema_bounds(self):
        p = self._glow(blur=0.5, light_mask=0.7)
        assert p["elements"][0]["blur"] == 0.5
        assert p["elements"][0]["light_mask"] == 0.7
        with pytest.raises(ValueError, match="blur"):
            self._glow(blur=1.5)
        with pytest.raises(ValueError, match="light_mask"):
            self._glow(light_mask=-0.1)

    def test_occlusion_shrinks_not_only_dims(self):
        lights_free = [{"x": 0.0, "y": 0.0, "occlusion": 0.0}]
        lights_half = [{"x": 0.0, "y": 0.0, "occlusion": 0.6}]
        p = self._glow()
        free = render_stack(p, lights_free, 128, 128, "cpu", torch.float32).sum(-1)
        occ = render_stack(p, lights_half, 128, 128, "cpu", torch.float32).sum(-1)
        # normalize away the brightness fade, compare FOOTPRINTS: the
        # occluded flare must be spatially smaller, not just dimmer
        free_n = free / free.max()
        occ_n = occ / occ.max()
        area_free = (free_n > 0.3).sum().item()
        area_occ = (occ_n > 0.3).sum().item()
        assert area_occ < area_free * 0.7
        assert occ.max() < free.max()  # and still dimmer

    def test_blur_softens_preserving_energy(self):
        sharp_p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glint", "offset": 0.0, "scale": 0.6, "auto_rotate": False,
             "params": {"points": 8, "length": 0.6, "thickness": 0.004,
                        "length_jitter": 0.0}}]})
        soft_p = validate_preset(json.loads(json.dumps({
            "schema_version": 1, "elements": [
                {"type": "glint", "offset": 0.0, "scale": 0.6, "blur": 0.6,
                 "auto_rotate": False,
                 "params": {"points": 8, "length": 0.6, "thickness": 0.004,
                            "length_jitter": 0.0}}]})))
        lights = [{"x": 0.0, "y": 0.0}]
        sharp = render_stack(sharp_p, lights, 128, 128, "cpu", torch.float32)
        soft = render_stack(soft_p, lights, 128, 128, "cpu", torch.float32)
        assert soft.max() < sharp.max() * 0.6            # peaks flattened
        assert soft.sum() == pytest.approx(sharp.sum().item(), rel=0.15)

    def test_light_mask_confines_to_bright_areas(self):
        p = self._glow(light_mask=1.0, scale=1.5,
                       params={"softness": 0.8, "falloff": 0.8})
        lights = [{"x": 0.0, "y": 0.0}]
        mask = torch.zeros(96, 96)
        mask[:, 48:] = 1.0  # right half of the scene is bright
        out = render_stack(p, lights, 96, 96, "cpu", torch.float32,
                           scene_mask=mask).sum(-1)
        left = out[:, :40].sum().item()
        right = out[:, 56:].sum().item()
        assert left < right * 0.05  # confined to the lit side

    def test_lens_dirt_lights_up_near_the_light(self):
        # the light itself illuminates lens dirt: on a pure black plate a
        # screen-space light_mask element concentrates around the light
        # position and stays dark in the far corner
        preset = json.dumps({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 2.0, "intensity": 0.8,
             "auto_rotate": False, "screen_space": True, "light_mask": 1.0,
             "params": {"softness": 1.2, "falloff": 0.6}}]})
        dark = torch.zeros(1, 96, 144, 3)
        _, fp, _ = run_node(dark, preset_json=preset,
                            light_x=0.25, light_y=0.35)
        near = fp[0, 20:50, 20:55].mean().item()   # around the light
        far = fp[0, 70:, 110:].mean().item()       # opposite corner
        assert near > 0.01
        assert near > far * 4

    def test_light_mask_follows_scene_brightness_too(self):
        # a bright practical in the plate lights the dirt even away from
        # the flare light
        preset = json.dumps({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 2.0, "intensity": 0.8,
             "auto_rotate": False, "screen_space": True, "light_mask": 1.0,
             "params": {"softness": 1.2, "falloff": 0.6}}]})
        plate = torch.zeros(1, 96, 144, 3)
        plate[0, 60:90, 110:140] = 0.95            # bright window, far corner
        _, fp, _ = run_node(plate, preset_json=preset,
                            light_x=0.15, light_y=0.2)
        window = fp[0, 60:90, 110:140].mean().item()
        dark_mid = fp[0, 60:90, 55:85].mean().item()
        # the window is FARTHER from the light than the middle, yet brighter:
        # scene luminance is driving the mask there
        assert window > dark_mid * 2


class TestAspectAndScreenSpace:
    def test_global_aspect_widens_elements(self):
        base = {"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 0.3, "auto_rotate": False,
             "params": {"softness": 0.25, "falloff": 2.0}}]}
        wide = json.loads(json.dumps(base))
        wide["global"] = {"aspect": 2.5}
        lights = [{"x": 0.0, "y": 0.0}]
        n = render_stack(validate_preset(base), lights, 96, 96, "cpu",
                         torch.float32).sum(-1)
        w = render_stack(validate_preset(wide), lights, 96, 96, "cpu",
                         torch.float32).sum(-1)

        def footprint(f):
            m = f > f.max() * 0.3
            ys, xs = torch.nonzero(m, as_tuple=True)
            return (xs.max() - xs.min()).item(), (ys.max() - ys.min()).item()

        nw, nh = footprint(n)
        ww, wh = footprint(w)
        assert abs(nw / nh - 1.0) < 0.2          # base is round
        assert ww / wh > 1.8                      # aspect stretched it wide

    def test_screen_space_ignores_light_position(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 0.3, "screen_space": True,
             "auto_rotate": False, "params": {"softness": 0.2, "falloff": 2.5}}]})
        a = render_stack(p, [{"x": -0.8, "y": -0.5}], 96, 96, "cpu", torch.float32)
        b = render_stack(p, [{"x": 0.7, "y": 0.4}], 96, 96, "cpu", torch.float32)
        assert torch.allclose(a, b)               # pinned to the lens
        u, v = argmax_uv(a.sum(-1))
        assert abs(u - 0.5) < 0.03 and abs(v - 0.5) < 0.03  # frame centre

    def test_screen_space_scales_with_strongest_light_not_count(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 0.3, "screen_space": True,
             "auto_rotate": False, "params": {"softness": 0.2, "falloff": 2.5}}]})
        one = render_stack(p, [{"x": 0.0, "y": 0.0, "brightness": 0.8}],
                           96, 96, "cpu", torch.float32)
        two = render_stack(p, [{"x": 0.0, "y": 0.0, "brightness": 0.8},
                               {"x": 0.5, "y": 0.0, "brightness": 0.5}],
                           96, 96, "cpu", torch.float32)
        assert torch.allclose(one, two)           # not duplicated per light
        occluded = render_stack(p, [{"x": 0.0, "y": 0.0, "brightness": 0.8,
                                     "occlusion": 1.0}],
                                96, 96, "cpu", torch.float32)
        assert occluded.abs().max() == 0.0        # light gone -> dirt dark

    def test_default_preset_is_cinematic_and_valid(self):
        import sys as _s
        render_mod = _s.modules["comfyui_flarecore.nodes.render"]
        preset = load_preset(render_mod.DEFAULT_PRESET)
        assert preset["name"] == "Cine Blue"
        assert len(preset["elements"]) >= 8


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
        lights, overlay, report = PKG.nodes.video.track_clip(
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

    def test_aspect_is_screen_horizontal_even_with_rotated_axis(self):
        # The light sits below the anchor, so with auto_rotate the local +u
        # axis points almost straight UP. If aspect multiplied stretch_x (the
        # old bug) the widening would follow that axis and the footprint
        # would grow VERTICALLY; screen-space aspect must widen along
        # screen X regardless of the axis angle.
        base = {"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0, "scale": 0.3, "auto_rotate": True,
             "params": {"softness": 0.25, "falloff": 2.0}}]}
        wide = json.loads(json.dumps(base))
        wide["global"] = {"aspect": 2.5}
        lights = [{"x": 0.0, "y": 0.9, "ax": 0.0, "ay": -0.9}]
        w = render_stack(validate_preset(wide), lights, 128, 128, "cpu",
                         torch.float32).sum(-1)
        m = w > w.max() * 0.3
        ys, xs = torch.nonzero(m, as_tuple=True)
        ww = (xs.max() - xs.min()).item()
        wh = (ys.max() - ys.min()).item()
        assert ww / max(wh, 1) > 1.8


class TestIrregular:
    def _footprint(self, preset):
        return render_stack(validate_preset(preset),
                            [{"x": 0.0, "y": 0.0}], 96, 96, "cpu",
                            torch.float32).sum(-1)

    def test_irregular_changes_shape_and_is_deterministic(self):
        base = {"schema_version": 1, "global": {"seed": 11}, "elements": [
            {"type": "ring", "offset": 0, "scale": 0.5, "irregular": 0.8,
             "auto_rotate": False, "params": {"radius": 1.0, "thickness": 0.1}}]}
        clean = json.loads(json.dumps(base))
        clean["elements"][0]["irregular"] = 0.0
        a = self._footprint(base)
        b = self._footprint(base)
        c = self._footprint(clean)
        assert torch.allclose(a, b)               # seeded, reproducible
        assert not torch.allclose(a, c)           # visibly different from clean

    def test_irregular_ring_is_uneven_around_circumference(self):
        p = {"schema_version": 1, "global": {"seed": 3}, "elements": [
            {"type": "ring", "offset": 0, "scale": 0.5, "irregular": 1.0,
             "auto_rotate": False, "params": {"radius": 1.0, "thickness": 0.12}}]}
        f = self._footprint(p)
        # compare the ring's brightness on the left vs right half: with full
        # irregularity the sides should differ noticeably
        left = f[:, :48].sum().item()
        right = f[:, 48:].sum().item()
        ratio = max(left, right) / max(min(left, right), 1e-6)
        assert ratio > 1.05

    def test_irregular_zero_matches_previous_behaviour(self):
        p = {"schema_version": 1, "elements": [
            {"type": "glint", "offset": 0, "scale": 0.5,
             "params": {"points": 8, "length": 0.5, "thickness": 0.01,
                        "length_jitter": 0.4}}]}
        a = self._footprint(p)
        with_key = json.loads(json.dumps(p))
        with_key["elements"][0]["irregular"] = 0.0
        b = self._footprint(with_key)
        assert torch.allclose(a, b)

    def test_irregular_validation_range(self):
        import pytest
        from flare.schema import validate_preset as vp
        with pytest.raises(ValueError):
            vp({"schema_version": 1, "elements": [
                {"type": "glow", "irregular": 1.5, "params": {}}]})


class TestLightSourcePrecedence:
    """Flare Render's `lights` input outranks its own picker points whenever
    it carries data, and hands control back the moment it does not. This used
    to be routed through a switch node; the contract belongs to the render
    node and is pinned here directly."""

    def _clip(self, b=4, h=120, w=200):
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            cx = 30 + i * 40
            clip[i, 45:60, cx:cx + 14] = 1.0
        return clip

    def test_no_lights_hands_the_light_back_to_the_picker(self):
        # with nothing on the lights input, light_x/light_y move the flare
        clip = self._clip()
        a = run_node(clip, lights=None, light_x=0.2, light_y=0.3)[1]
        b = run_node(clip, lights=None, light_x=0.8, light_y=0.7)[1]
        assert not torch.allclose(a, b)
        ua, va = argmax_uv(a[0].sum(-1))
        ub, vb = argmax_uv(b[0].sum(-1))
        assert ua < ub and va < vb

    def test_connected_lights_still_override_the_picker(self):
        clip = self._clip()
        tracked = PKG.nodes.video.track_clip(
            clip, detect_threshold=0.75, detect_max_lights=1, smoothing=0.5,
            max_jump=0.4, hold_frames=3, fade_frames=1)[0]
        a = run_node(clip, lights=tracked, light_x=0.2, light_y=0.3)[1]
        b = run_node(clip, lights=tracked, light_x=0.8, light_y=0.7)[1]
        assert torch.allclose(a, b)          # the tracker owns the light

    def test_keyframed_anchor_animates(self):
        """anchor_keys drives the flare anchor per frame, so the ghost chain
        can be aimed independently of the light."""
        kf = PKG.NODE_CLASS_MAPPINGS["FlareKeyframes"]()
        lights = kf.make(frame_count=4, light_keys="0: 0.2,0.5; 3: 0.2,0.5",
                         anchor_keys="0: 0.3,0.5; 3: 0.95,0.5",
                         easing="linear", brightness=1.0)[0]
        aus = [f[0]["au"] for f in lights]
        assert aus[0] == pytest.approx(0.3) and aus[-1] == pytest.approx(0.95)
        assert aus[1] < aus[2]
        clip = torch.zeros(4, 120, 200, 3)
        fp = run_node(clip, lights=lights)[1]
        # the light is static, so any frame-to-frame change is the anchor
        assert not torch.allclose(fp[0], fp[-1])


class TestOneLightOneFlare:
    """max lights is a CAP, not a quota. One dot on screen must produce one
    flare no matter how high the cap is set."""

    def _moving_dot(self, b=14, h=540, w=960, speed=0.06, radius=5.0):
        """A single dot travelling faster than the default max_jump gate."""
        clip = torch.zeros(b, h, w, 3)
        ys, xs = torch.meshgrid(torch.arange(h).float(),
                                torch.arange(w).float(), indexing="ij")
        for i in range(b):
            cx = (0.2 + speed * i) * w
            cy = 0.45 * h
            clip[i] = torch.exp(-((xs - cx) ** 2 + (ys - cy) ** 2)
                                / (2 * radius ** 2)).unsqueeze(-1)
        return clip.clamp(0, 1)

    def _flares_per_frame(self, clip, max_lights, max_jump=0.05):
        from flare.colorspace import srgb_to_linear
        from flare.detect import detect_lights
        from flare.track import track_lights
        lin = srgb_to_linear(clip)
        det = detect_lights(lin, threshold=0.75 * float(lin.amax()),
                            max_lights=max(max_lights * 8, 12))
        tracks = track_lights(det, smoothing=0.6, max_jump=max_jump,
                              max_tracks=max_lights)
        return [len(d) for d in det], [len(f) for f in tracks]

    def test_one_fast_dot_does_not_spawn_ghost_flares(self):
        """The dot outruns max_jump, so its track cannot match it and coasts
        while a NEW track opens on the same dot. The old one keeps emitting
        through hold+fade, so a single dot showed up to `max lights` flares —
        the extra ones sitting on nothing."""
        clip = self._moving_dot()
        dets, flares = self._flares_per_frame(clip, max_lights=3)
        assert max(dets) == 1, f"the clip really does have one dot: {dets}"
        assert max(flares) == 1, (
            f"one dot produced up to {max(flares)} flares per frame: {flares}")

    def test_a_high_cap_is_harmless_when_the_scene_is_simple(self):
        clip = self._moving_dot()
        _, flares = self._flares_per_frame(clip, max_lights=8)
        assert max(flares) == 1, f"cap of 8 invented flares: {flares}"

    def test_real_extra_lights_still_get_their_own_flares(self):
        """The guard must not stop genuinely separate lights from tracking."""
        clip = self._moving_dot()
        h, w = clip.shape[1], clip.shape[2]
        ys, xs = torch.meshgrid(torch.arange(h).float(),
                                torch.arange(w).float(), indexing="ij")
        for i in range(clip.shape[0]):          # a second, static light
            clip[i] += torch.exp(-((xs - 0.85 * w) ** 2 + (ys - 0.2 * h) ** 2)
                                 / (2 * 5.0 ** 2)).unsqueeze(-1)
        clip = clip.clamp(0, 1)
        _, flares = self._flares_per_frame(clip, max_lights=3)
        assert max(flares) == 2, f"expected two flares for two lights: {flares}"


class TestBusySceneTracking:
    """Dappled light (an avenue of trees, a row of lamps) puts several blobs
    of near-equal brightness on screen and the per-frame argmax hops between
    them. detect_max_lights must cap FLARES, not detections."""

    def _dappled(self, b=16, h=180, w=320):
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            clip[i, 60:76, 44:60] = 0.90 + 0.10 * math.sin(i * 1.1)
            clip[i, 60:76, 250:266] = 0.90 + 0.10 * math.sin(i * 1.1 + 2.0)
        return clip

    def _track(self, clip, **kw):
        args = dict(detect_threshold=0.75, detect_max_lights=1, smoothing=0.6,
                    max_jump=0.15, hold_frames=3, fade_frames=4)
        args.update(kw)
        return PKG.nodes.video.track_clip(clip, **args)

    def test_one_light_means_one_flare(self):
        lights, _, report = self._track(self._dappled())
        assert all(len(f) == 1 for f in lights), \
            f"expected a single flare per frame, got {[len(f) for f in lights]}"
        us = [f[0]["u"] for f in lights]
        assert max(us) - min(us) < 0.05        # it stays on one side

    def test_raw_detections_really_do_hop(self):
        # the input genuinely flips sides — the stability is the tracker's
        # doing, not a quiet test fixture
        from flare.detect import detect_lights
        from flare.colorspace import srgb_to_linear
        det = detect_lights(srgb_to_linear(self._dappled()), threshold=0.75,
                            max_lights=1)
        us = [f[0]["u"] for f in det if f]
        assert max(us) - min(us) > 0.5

    def test_two_lights_allows_two_flares(self):
        lights, _, _ = self._track(self._dappled(), detect_max_lights=2)
        assert max(len(f) for f in lights) == 2

    def test_search_region_selects_which_light(self):
        clip = self._dappled()
        left, _, report = self._track(clip, search_radius=0.35,
                                      search_u=0.16, search_v=0.38)
        right, _, _ = self._track(clip, search_radius=0.35,
                                  search_u=0.81, search_v=0.38)
        assert all(f[0]["u"] < 0.3 for f in left if f)
        assert all(f[0]["u"] > 0.6 for f in right if f)
        assert "search region rejected" in report

    def test_search_region_off_by_default(self):
        a = self._track(self._dappled())[0]
        b = self._track(self._dappled(), search_radius=0.0)[0]
        assert [len(f) for f in a] == [len(f) for f in b]

    def test_overlay_marks_the_search_region(self):
        clip = self._dappled()
        plain = self._track(clip)[1]
        ringed = self._track(clip, search_radius=0.3, search_u=0.5,
                             search_v=0.5)[1]
        assert not torch.allclose(plain, ringed)

class TestTrackerLocksOn:
    """A canopy breaks a sun into several rivals of similar size that take
    turns being brightest. The tracker must pick one and hold it, not roam
    the cluster — and must still follow a genuinely fast light."""

    def _cluster(self, b=14, h=270, w=480):
        spots = [(0.60, 0.25), (0.68, 0.31), (0.53, 0.30), (0.63, 0.17),
                 (0.72, 0.22)]
        yy, xx = torch.meshgrid(torch.arange(h, dtype=torch.float32),
                                torch.arange(w, dtype=torch.float32),
                                indexing="ij")
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            img = torch.zeros(h, w)
            for k, (gu, gv) in enumerate(spots):
                amp = 0.88 + 0.16 * math.sin(i * 1.15 + k * 1.7)
                gd = torch.sqrt((xx - gu * w) ** 2 + (yy - gv * h) ** 2)
                img = torch.maximum(img, torch.clamp(1.5 - gd / 17.0, 0, 1) * amp)
            clip[i] = (img.unsqueeze(-1).clamp(0, 1)
                       * torch.tensor([1.0, 0.98, 0.94])) + 0.06
        return clip.clamp(0, 1)

    def _travel(self, lights):
        pts = [(f[0]["u"], f[0]["v"]) for f in lights if f]
        return max(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(pts, pts[1:]))

    def test_locks_onto_one_source_in_a_cluster(self):
        lights, _, _ = PKG.nodes.video.track_clip(
            self._cluster(), detect_threshold=0.75, detect_max_lights=1,
            smoothing=0.6, max_jump=0.06, hold_frames=3, fade_frames=4)
        assert all(len(f) == 1 for f in lights)
        us = [f[0]["u"] for f in lights]
        assert max(us) - min(us) < 0.01, f"flare roamed the cluster: {us}"
        assert self._travel(lights) < 0.01

    def test_a_loose_gate_is_what_lets_it_roam(self):
        # the same clip with the old permissive gate: this is the behaviour
        # the default change fixes, pinned so the reason stays visible
        lights, _, _ = PKG.nodes.video.track_clip(
            self._cluster(), detect_threshold=0.75, detect_max_lights=1,
            smoothing=0.6, max_jump=0.15, hold_frames=3, fade_frames=4)
        us = [f[0]["u"] for f in lights]
        assert max(us) - min(us) > 0.03

    def test_fast_light_still_keeps_one_track(self):
        # a light crossing at 0.04 of frame height per frame — well beyond a
        # naive reading of max_jump=0.06 once you include its own motion
        b, h, w = 14, 270, 480
        yy, xx = torch.meshgrid(torch.arange(h, dtype=torch.float32),
                                torch.arange(w, dtype=torch.float32),
                                indexing="ij")
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            cx = (0.12 + 0.055 * i) * w
            d = torch.sqrt((xx - cx) ** 2 + (yy - 0.4 * h) ** 2)
            clip[i] = (torch.clamp(1.5 - d / 16.0, 0, 1).unsqueeze(-1)
                       * torch.tensor([1.0, 0.98, 0.94])) + 0.05
        lights, _, report = PKG.nodes.video.track_clip(
            clip.clamp(0, 1), detect_threshold=0.75, detect_max_lights=1,
            smoothing=0.6, max_jump=0.06, hold_frames=3, fade_frames=4)
        tids = {lg["tid"] for f in lights for lg in f}
        assert tids == {0}, f"fast light fragmented into tracks {tids}"
        us = [f[0]["u"] for f in lights if f]
        assert us[-1] - us[0] > 0.5           # it really did cross the frame

    def test_detector_reports_energy(self):
        from flare.detect import detect_lights
        from flare.colorspace import srgb_to_linear
        det = detect_lights(srgb_to_linear(self._cluster()), threshold=0.75,
                            max_lights=12)
        assert all("energy" in c and c["energy"] > 0
                   for frame in det for c in frame)


class TestDrawnPath:
    """The picker's path tool writes 'u,v; u,v; ...' and the light travels it
    across the batch — no frame numbers to type."""

    def test_parse_and_shape(self):
        from flare.track import parse_path
        assert parse_path("0.1,0.2; 0.5,0.6") == [(0.1, 0.2), (0.5, 0.6)]
        assert parse_path("  ") == []
        with pytest.raises(ValueError, match="bad path point"):
            parse_path("0.1")

    def test_single_point_holds_still(self):
        from flare.track import sample_path
        out = sample_path([(0.3, 0.4)], 5)
        assert out == [(0.3, 0.4)] * 5

    def test_endpoints_are_hit_exactly(self):
        from flare.track import sample_path
        pts = [(0.1, 0.5), (0.5, 0.2), (0.9, 0.5)]
        out = sample_path(pts, 21)
        assert out[0] == pytest.approx(pts[0])
        assert out[-1] == pytest.approx(pts[-1])

    def test_curve_passes_through_middle_points(self):
        from flare.track import sample_path
        pts = [(0.1, 0.5), (0.5, 0.2), (0.9, 0.5)]
        out = sample_path(pts, 21)
        assert min(math.hypot(u - 0.5, v - 0.2) for u, v in out) < 1e-6

    def test_travel_is_monotonic_along_a_line(self):
        from flare.track import sample_path
        out = sample_path([(0.0, 0.5), (1.0, 0.5)], 11)
        us = [u for u, _ in out]
        assert us == sorted(us)
        assert us[5] == pytest.approx(0.5, abs=1e-6)

    def test_path_reference_points(self):
        """Pins the curve the editor's JavaScript must draw (samplePath in
        web/flarecore_ui.js). Change one, change the other."""
        from flare.track import sample_path
        pts = [(0.10, 0.50), (0.40, 0.20), (0.70, 0.60), (0.95, 0.35)]
        got = sample_path(pts, 7)
        expected = [
            (0.100000, 0.500000), (0.231250, 0.325000), (0.400000, 0.200000),
            (0.553125, 0.396875), (0.700000, 0.600000), (0.843750, 0.500000),
            (0.950000, 0.350000),
        ]
        for g, e in zip(got, expected):
            assert g[0] == pytest.approx(e[0], abs=1e-6)
            assert g[1] == pytest.approx(e[1], abs=1e-6)

    def test_render_follows_the_path(self):
        plate = torch.zeros(9, 120, 200, 3)
        out = run_node(plate, position_mode="path",
                       light_path="0.1,0.5; 0.5,0.2; 0.9,0.5")[1]
        us = [argmax_uv(out[f].sum(-1))[0] for f in range(9)]
        assert us[0] < 0.25 and us[-1] > 0.75
        assert us == sorted(us)

    def test_path_mode_without_a_path_says_so(self):
        plate = torch.zeros(2, 64, 64, 3)
        with pytest.raises(ValueError, match="no path is drawn"):
            run_node(plate, position_mode="path", light_path="")

    def test_one_knot_per_frame_reproduces_a_track_exactly(self):
        """What "bake to path" relies on: a path with one point per frame is
        sampled at t = 0 of every segment, so a baked track comes back bit
        for bit. Subsampling to a couple of dozen knots is what distorted
        it -- measured 5.9 px off on a 1280-wide track that paused and then
        moved."""
        from flare.track import sample_path
        track = [(0.2 + 0.003 * f + 0.001 * (f % 3), 0.5 - 0.002 * f)
                 for f in range(60)]
        got = sample_path(track, 60)
        for (u, v), (gu, gv) in zip(track, got):
            assert abs(gu - u) < 1e-12 and abs(gv - v) < 1e-12

    def test_anchor_path_carries_the_flare_anchor(self):
        """A two-tracker solve baked to a path keeps its second point: the
        anchor travels its own drawn path, so the axis keeps the pair's
        rotation and scale instead of snapping to the static anchor."""
        from flare.track import sample_path
        node = PKG.FlareRender()
        lights = node._resolve_lights(None, 5, 5, "path", 0.5, 0.5, 0.8, 1,
                                      light_path="0.1,0.5; 0.9,0.5",
                                      anchor_path="0.5,0.1; 0.5,0.9")
        want = sample_path([(0.5, 0.1), (0.5, 0.9)], 5)
        for frame, (au, av) in zip(lights, want):
            assert abs(frame[0]["au"] - au) < 1e-9
            assert abs(frame[0]["av"] - av) < 1e-9
        plain = node._resolve_lights(None, 5, 5, "path", 0.5, 0.5, 0.8, 1,
                                     light_path="0.1,0.5; 0.9,0.5")
        assert "au" not in plain[0][0]


class TestDotMatte:
    """track_dots: a black plate with white dots, one flare per dot."""

    def _dots(self, paths, b=24, h=240, w=400, radius=9.0, level=0.92):
        yy, xx = torch.meshgrid(torch.arange(h, dtype=torch.float32),
                                torch.arange(w, dtype=torch.float32),
                                indexing="ij")
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            t = i / (b - 1)
            img = torch.zeros(h, w)
            for fn in paths:
                cu, cv = fn(t)
                d = torch.sqrt((xx - cu * w) ** 2 + (yy - cv * h) ** 2)
                img = torch.maximum(img, torch.clamp(1.0 - d / radius, 0, 1) * level)
            clip[i] = img.unsqueeze(-1)
        return clip

    def test_three_dots_make_three_flares(self):
        clip = self._dots([
            lambda t: (0.15 + 0.7 * t, 0.30),
            lambda t: (0.85 - 0.7 * t, 0.70),
            lambda t: (0.50, 0.20 + 0.6 * t),
        ])
        lights = PKG.NODE_CLASS_MAPPINGS["FlareRender"]()._resolve_lights(
            (lambda _t: (lambda a, b: _t[a:b]))(srgb_to_linear(clip)),
            clip.shape[0], 16, "track_dots", 0.5, 0.5, 0.5, 6)
        assert all(len(f) == 3 for f in lights)
        assert {l["tid"] for f in lights for l in f} == {0, 1, 2}

    def test_dots_are_found_though_they_never_reach_pure_white(self):
        # the matte peaks at 0.92 over a lifted black; an absolute threshold
        # of 0.8 linear would miss it entirely
        clip = self._dots([lambda t: (0.3 + 0.4 * t, 0.5)], level=0.92)
        clip = clip + 0.07
        lights = PKG.NODE_CLASS_MAPPINGS["FlareRender"]()._resolve_lights(
            (lambda _t: (lambda a, b: _t[a:b]))(srgb_to_linear(clip.clamp(0, 1))),
            clip.shape[0], 16, "track_dots", 0.5, 0.5, 0.5, 4)
        assert all(len(f) == 1 for f in lights)
        us = [f[0]["u"] for f in lights]
        assert us[0] < 0.4 and us[-1] > 0.6

    def test_dot_count_is_capped_by_detect_max_lights(self):
        clip = self._dots([
            lambda t: (0.2, 0.3), lambda t: (0.5, 0.3), lambda t: (0.8, 0.3),
        ])
        lights = PKG.NODE_CLASS_MAPPINGS["FlareRender"]()._resolve_lights(
            (lambda _t: (lambda a, b: _t[a:b]))(srgb_to_linear(clip)),
            clip.shape[0], 16, "track_dots", 0.5, 0.5, 0.5, 2)
        assert max(len(f) for f in lights) == 2


class TestWholeClipSolve:
    """`lock` reads the whole clip before deciding. Per-frame detection can
    hand the light to a rival on the far side of frame for a few frames and
    take it back; a path that does that costs far more travel than one that
    stays put, so the solve rejects it however bright those frames were."""

    def _two_rivals(self, b=20, h=180, w=320):
        """A steady source on the right; a rival on the left that briefly
        wins the brightness contest, exactly like sun vs a gap in trees."""
        clip = torch.zeros(b, h, w, 3)
        ys, xs = torch.meshgrid(torch.arange(h).float(),
                                torch.arange(w).float(), indexing="ij")
        def blob(cx, cy, r=6.0):
            return torch.exp(-(((xs - cx) ** 2 + (ys - cy) ** 2)) / (2 * r * r))
        for i in range(b):
            clip[i] = (blob(0.85 * w, 0.3 * h) * 0.9).unsqueeze(-1)
            if 8 <= i <= 11:                      # the rival takes over
                clip[i] = torch.maximum(clip[i],
                                        (blob(0.12 * w, 0.3 * h) * 1.0).unsqueeze(-1))
        return clip.clamp(0, 1)

    def _pool(self, clip):
        from flare.colorspace import srgb_to_linear
        from flare.detect import detect_lights
        lin = srgb_to_linear(clip)
        return detect_lights(lin, 0.5 * float(lin.amax()), 12)

    def test_per_frame_detection_defects_to_the_rival(self):
        from flare.colorspace import srgb_to_linear
        from flare.detect import detect_lights
        clip = self._two_rivals()
        lin = srgb_to_linear(clip)
        det = detect_lights(lin, 0.5 * float(lin.amax()), 1)
        left = sum(1 for f in det if f and f[0]["u"] < 0.5)
        assert left > 0, "the rival was supposed to win some frames"

    def test_the_solve_stays_on_the_real_source(self):
        from flare.track import solve_light_path
        solved = solve_light_path(self._pool(self._two_rivals()), max_tracks=1,
                                  motion_cost=1000.0)
        assert all(f and f[0]["u"] > 0.5 for f in solved),             f"the light crossed frame: {[round(f[0]['u'], 2) for f in solved]}"

    def test_it_carries_through_frames_with_no_detection(self):
        """A dot matte goes black for a stretch; the light should hold its
        place instead of dropping out and popping back."""
        from flare.track import solve_light_path
        clip = self._two_rivals()
        clip[5:9] = 0.0
        solved = solve_light_path(self._pool(clip), max_tracks=1,
                                  motion_cost=1000.0)
        assert all(len(f) == 1 for f in solved), "the light dropped out"

    def test_smoothing_irons_out_wobble_a_still_source_cannot_have(self):
        """The detector honestly reports the centre of whatever part of the
        source is visible, and foliage keeps eating different parts of it.
        A source that never moved must come out not moving."""
        from flare.track import solve_light_path
        import math, random
        rng = random.Random(4)
        det = [[{"u": 0.8 + rng.uniform(-0.05, 0.05),
                 "v": 0.3 + rng.uniform(-0.05, 0.05),
                 "brightness": 1.0, "energy": 1.0}] for _ in range(40)]
        def travel(sol):
            p = [(f[0]["u"], f[0]["v"]) for f in sol]
            return max(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(p, p[1:]))
        raw = travel(solve_light_path(det, 1, motion_cost=1000.0, smoothing=0.0))
        ironed = travel(solve_light_path(det, 1, motion_cost=1000.0, smoothing=1.0))
        assert raw > 0.02, "the fixture was supposed to wobble"
        assert ironed < raw / 10, f"still wobbling: {ironed:.4f} vs {raw:.4f}"

    def test_a_real_drift_survives_the_smoothing(self):
        """Ironing out wobble must not pin a light that genuinely travels."""
        from flare.track import solve_light_path
        det = [[{"u": 0.2 + 0.6 * i / 39, "v": 0.5,
                 "brightness": 1.0, "energy": 1.0}] for i in range(40)]
        sol = solve_light_path(det, 1, motion_cost=1000.0, smoothing=1.0)
        assert sol[0][0]["u"] < 0.25 and sol[-1][0]["u"] > 0.75,             f"the drift was flattened: {sol[0][0]['u']:.2f} -> {sol[-1][0]['u']:.2f}"

    def test_an_empty_clip_is_handled(self):
        from flare.track import solve_light_path
        assert solve_light_path([], max_tracks=1) == []
        assert solve_light_path([[], []], max_tracks=1) == [[], []]

    def test_the_node_exposes_it(self):
        modes = PKG.NODE_CLASS_MAPPINGS["FlareRender"].INPUT_TYPES()
        assert "lock" in modes["required"]["position_mode"][0]


class TestLightTravel:
    """One dial for how much the flare is allowed to move: 1 follows the
    tracked path, 0 pins it for the whole clip."""

    def _clip(self, b=12, h=120, w=200):
        clip = torch.zeros(b, h, w, 3)
        for i in range(b):
            cx = 20 + i * 12
            clip[i, 40:52, cx:cx + 10] = 1.0
        return clip

    def _light_path(self, travel, mode="track"):
        lights_seen = []
        out, fp, alpha = run_node(self._clip(), position_mode=mode,
                                  detect_threshold=0.6, detect_max_lights=1,
                                  light_travel=travel)
        B, H, W, _ = fp.shape
        lum = fp.mean(-1)
        return [( float(int(lum[i].argmax()) % W) / W,
                  float(int(lum[i].argmax()) // W) / H) for i in range(B)]

    def test_zero_holds_the_flare_still(self):
        pts = self._light_path(0.0)
        us = [p[0] for p in pts]
        assert max(us) - min(us) < 0.01, f"still moving: {us}"

    def test_one_is_the_tracked_path(self):
        pts = self._light_path(1.0)
        us = [p[0] for p in pts]
        assert max(us) - min(us) > 0.3, f"barely moved: {us}"

    def test_half_moves_about_half_as_far(self):
        full = self._light_path(1.0); half = self._light_path(0.5)
        span = lambda p: max(x for x, _ in p) - min(x for x, _ in p)
        assert 0.3 < span(half) / span(full) < 0.7,             f"{span(half):.3f} of {span(full):.3f}"

    def test_the_anchor_keeps_its_own_centre(self):
        """Damping the anchor toward the LIGHT's centre put both points in one
        place at travel 0 and collapsed the flare axis to nothing."""
        _damp_travel = PKG.nodes.render._damp_travel
        frames = [[{"u": 0.2 + 0.1 * i, "v": 0.5,
                    "au": 0.6 + 0.1 * i, "av": 0.2, "tid": 0}] for i in range(5)]
        d = _damp_travel(frames, 0.0)
        light = (d[0][0]["u"], d[0][0]["v"]); anchor = (d[0][0]["au"], d[0][0]["av"])
        assert abs(light[0] - 0.4) < 1e-9 and abs(anchor[0] - 0.8) < 1e-9
        assert light != anchor, "the axis collapsed"

    def test_each_dot_is_damped_about_its_own_centre(self):
        """Several dots must not collapse onto one shared point at travel 0."""
        _damp_travel = PKG.nodes.render._damp_travel
        frames = [[{"u": 0.2 + 0.01 * i, "v": 0.5, "tid": 0},
                   {"u": 0.8 - 0.01 * i, "v": 0.5, "tid": 1}] for i in range(10)]
        damped = _damp_travel(frames, 0.0)
        assert all(abs(f[0]["u"] - damped[0][0]["u"]) < 1e-9 for f in damped)
        assert damped[0][0]["u"] < 0.35 and damped[0][1]["u"] > 0.65,             "the two lights were pulled together"


class TestTrackingInsideRender:
    """Tracking lives in Flare Render now: hold, fade and a search region
    centred on the picker's light point, and the solved path reported back
    to the editor so it can be drawn and baked into a motion path."""

    def _two_lights(self, b=10, h=90, w=160):
        """A steady light on the left; a BRIGHTER rival on the right."""
        clip = torch.zeros(b, h, w, 3)
        clip[:, 40:48, 20:28] = 0.8
        clip[:, 40:48, 130:138] = 1.0
        return clip

    def test_search_region_keeps_the_flare_off_a_brighter_rival(self):
        clip = self._two_lights()
        free = run_node(clip, position_mode="track", detect_threshold=0.5,
                        detect_max_lights=1, search_radius=0.0)[1]
        fenced = run_node(clip, position_mode="track", detect_threshold=0.5,
                          detect_max_lights=1, search_radius=0.25,
                          light_x=0.15, light_y=0.49)[1]
        W = clip.shape[2]
        cx_free = int(free[0].mean(-1).argmax()) % W / W
        cx_fenced = int(fenced[0].mean(-1).argmax()) % W / W
        assert cx_free > 0.5, "unfenced, the brighter rival should win"
        assert cx_fenced < 0.5, "the fence should hold the flare on the left"

    def test_restrict_to_region_measures_in_frame_height(self):
        restrict = PKG.nodes.render.restrict_to_region
        dets = [[{"u": 0.5, "v": 0.5}, {"u": 0.7, "v": 0.5}, {"u": 0.5, "v": 0.7}]]
        # aspect 2: a 0.2 horizontal offset is 0.4 in height units
        kept = restrict(dets, 0.5, 0.5, 0.25, 2.0)[0]
        assert [d["u"] for d in kept] == [0.5, 0.5]

    def test_hold_and_fade_reach_the_tracker(self):
        """A light that vanishes mid-clip survives `hold` frames, then fades."""
        clip = torch.zeros(12, 60, 100, 3)
        clip[:4, 25:31, 40:46] = 1.0                    # gone from frame 4 on
        long_hold = run_node(clip, position_mode="track", detect_threshold=0.5,
                             track_hold=6, track_fade=1, visibility_mode="depth")[1]
        short_hold = run_node(clip, position_mode="track", detect_threshold=0.5,
                              track_hold=0, track_fade=1, visibility_mode="depth")[1]
        assert float(long_hold[6].sum()) > 0.0, "held light should still shine"
        assert float(short_hold[6].sum()) == 0.0, "unheld light should be gone"

    def test_the_solved_path_is_reported_to_the_editor(self):
        from test_nodes import FlareRender, PRESET
        clip = torch.zeros(5, 60, 100, 3)
        for i in range(5):                 # 3 px/frame: inside the jump gate
            clip[i, 25:31, 10 + i * 3:16 + i * 3] = 1.0
        res = FlareRender().render(
            image=clip, preset_json=PRESET, position_mode="track", light_x=0.5,
            light_y=0.5, flare_x=0.5, flare_y=0.5, detect_threshold=0.5,
            detect_max_lights=1, occlusion_radius=0.02, light_depth=0.1,
            invert_depth=False, intensity=1.0, scale=1.0, blend_mode="add",
            clamp_output=True, seed=0, track_smoothing=0.0, track_max_jump=0.2)
        if not isinstance(res, dict):
            pytest.skip("ui payload only exists inside ComfyUI")
        track = res["ui"]["fc_track"][0]
        assert len(track) == 5
        us = [t[0] for t in track if t]
        assert us == sorted(us) and us[-1] - us[0] > 0.08, us
        assert all(t[2] is None for t in track), "no anchor in plain track"


class TestSceneAnchoring:
    """A detected light is held to the way the picture moves. A sun is at
    infinity and moves only with the camera; the detector's disagreements
    with that -- wobble as branches cross it, a hop to a rival -- are noise."""

    def _panning_scene(self, b=20, h=120, w=200, dx=2.0):
        """A textured scene panning left at dx px/frame, with a bright spot
        that moves WITH it -- and a rival spot that wins two frames."""
        import torch.nn.functional as F
        g = torch.Generator().manual_seed(3)
        base = F.avg_pool2d(torch.rand(1, 1, h, w * 2, generator=g), 5, 1, 2)[0, 0] * 0.35
        clip = torch.zeros(b, h, w, 3)
        truth = []
        for i in range(b):
            x0 = int(dx * i)
            frame = base[:, x0:x0 + w].clone()
            lx = 150 - int(dx * i)                  # the light rides the scene
            frame[30:36, lx:lx + 6] = 1.0
            if 8 <= i <= 9:                          # a BIGGER rival, briefly
                frame[74:92, 14:32] = 1.0
            clip[i] = frame.unsqueeze(-1).expand(-1, -1, 3)
            truth.append(((lx + 3) / w, 33 / h))
        return clip.clamp(0, 1), truth

    def _light_u(self, fp):
        B, H, W, _ = fp.shape
        return [int(fp[i].mean(-1).argmax()) % W / W for i in range(B)]

    def test_a_rival_that_wins_two_frames_cannot_drag_the_light(self):
        clip, truth = self._panning_scene()
        raw = run_node(clip, position_mode="detect", detect_threshold=0.6,
                       scene_lock=0.0)[1]
        held = run_node(clip, position_mode="detect", detect_threshold=0.6,
                        scene_lock=1.0, track_smoothing=0.85)[1]
        ur, uh = self._light_u(raw), self._light_u(held)
        assert min(ur) < 0.3, "the fixture's rival was supposed to win"
        assert min(uh) > 0.4, f"the rival still dragged the light: {uh}"

    def test_the_held_light_still_follows_the_pan(self):
        clip, truth = self._panning_scene()
        held = run_node(clip, position_mode="detect", detect_threshold=0.6,
                        scene_lock=1.0, track_smoothing=0.85)[1]
        uh = self._light_u(held)
        assert uh[0] - uh[-1] > 0.12, f"the pan was flattened: {uh[0]:.2f} -> {uh[-1]:.2f}"

    def test_scene_lock_zero_leaves_a_moving_light_alone(self):
        """A torch crossing a static room moves against the scene; with the
        lock off its motion must survive in full."""
        clip = torch.zeros(16, 90, 160, 3)
        clip[:] = 0.2
        for i in range(16):
            clip[i, 40:46, 10 + i * 8:16 + i * 8] = 1.0
        out = run_node(clip, position_mode="track", detect_threshold=0.6,
                       scene_lock=0.0)[1]
        u = self._light_u(out)
        assert u[-1] - u[0] > 0.6, f"the light was pinned: {u[0]:.2f} -> {u[-1]:.2f}"

    def test_a_matte_with_no_scene_is_left_alone(self):
        """Black plate, one moving dot: there is nothing to anchor to, and
        the dot's own motion is all there is."""
        clip = torch.zeros(16, 90, 160, 3)
        for i in range(16):
            clip[i, 40:46, 10 + i * 8:16 + i * 8] = 1.0
        out = run_node(clip, position_mode="track_dots", detect_threshold=0.5,
                       scene_lock=1.0)[1]
        u = self._light_u(out)
        assert u[-1] - u[0] > 0.6, f"the dot was pinned: {u[0]:.2f} -> {u[-1]:.2f}"

    def test_follow_carries_an_off_frame_light_with_the_pan(self):
        clip, _ = self._panning_scene()
        from test_nodes import FlareRender, PRESET
        res = FlareRender().render(
            image=clip, preset_json=PRESET, position_mode="follow", light_x=0.6,
            light_y=-0.15, flare_x=0.5, flare_y=0.5, detect_threshold=0.6,
            detect_max_lights=1, occlusion_radius=0.02, light_depth=0.1,
            invert_depth=False, intensity=1.0, scale=1.0, blend_mode="add",
            clamp_output=True, seed=0, track_smoothing=0.0)
        track = res["ui"]["fc_track"][0] if isinstance(res, dict) else None
        if track is None:
            pytest.skip("ui payload only inside ComfyUI")
        us = [t[0] for t in track]; vs = [t[1] for t in track]
        assert all(v < 0 for v in vs), "the light should stay above the frame"
        assert us[0] - us[-1] > 0.12, f"not carried by the pan: {us[0]:.2f} -> {us[-1]:.2f}"
