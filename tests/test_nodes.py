# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flare.colorspace import srgb_to_linear, linear_to_srgb  # noqa: E402


from conftest import load_package  # noqa: E402

PKG = load_package()
FlareRender = PKG.NODE_CLASS_MAPPINGS["FlareRender"]
FlarePresetLoader = PKG.NODE_CLASS_MAPPINGS["FlarePresetLoader"]

PRESET = json.dumps({
    "schema_version": 1,
    "elements": [
        {"type": "glow", "scale": 0.3},
        {"type": "iris", "offset": 0.8, "count": 3, "spread": 0.3, "scale": 0.1,
         "intensity": 0.3, "dispersion": 0.5},
    ],
})


def run_node(image, depth=None, **overrides):
    args = dict(
        preset_json=PRESET, position_mode="manual", light_x=0.3, light_y=0.4,
        flare_x=0.5, flare_y=0.5,
        detect_threshold=0.8, detect_max_lights=1, occlusion_radius=0.02,
        light_depth=0.0, invert_depth=False, intensity=1.0, scale=1.0,
        blend_mode="add", clamp_output=True, seed=0,
    )
    args.update(overrides)
    result = FlareRender().render(image=image, depth=depth, **args)
    # inside ComfyUI render() returns {"ui": ..., "result": ...}
    return result["result"] if isinstance(result, dict) else result


class TestFlareRender:
    def test_output_shapes_and_types(self):
        img = torch.rand(2, 96, 128, 3)
        out, flare_pass, alpha = run_node(img)
        assert out.shape == (2, 96, 128, 3)
        assert flare_pass.shape == (2, 96, 128, 3)
        assert alpha.shape == (2, 96, 128)
        assert out.dtype == torch.float32

    def test_registration_contract(self):
        assert set(PKG.NODE_CLASS_MAPPINGS) == {
            "FlareRender", "FlarePresetLoader", "FlareDepthAdapter",
            "FlareKeyframes",
            "FlareElementPrompts", "FlareTexturePrepare",
            "FlareElementSave", "FlareGeneratorSelect",
        }
        assert PKG.WEB_DIRECTORY == "./web"
        assert FlareRender.CATEGORY == "Flarecore"
        assert FlareRender.RETURN_TYPES == ("IMAGE", "IMAGE", "MASK")
        it = FlareRender.INPUT_TYPES()
        assert "image" in it["required"]
        assert "depth" in it["optional"]

    def test_generator_select_passes_the_chosen_image_through(self):
        """The forge can carry two generators; the switch decides which one
        actually runs. Lazy inputs are the point: the branch that is not
        selected is never evaluated, so an API generator costs nothing
        while the local one is active."""
        node = PKG.NODE_CLASS_MAPPINGS["FlareGeneratorSelect"]()
        a = torch.zeros(1, 8, 8, 3)
        b = torch.ones(1, 8, 8, 3)
        assert node.select("a", image_a=a, image_b=b)[0] is a
        assert node.select("b", image_a=a, image_b=b)[0] is b

    def test_generator_select_requests_only_the_chosen_branch(self):
        node = PKG.NODE_CLASS_MAPPINGS["FlareGeneratorSelect"]()
        assert node.check_lazy_status("a") == ["image_a"]
        assert node.check_lazy_status("b", image_a=None, image_b=None) == ["image_b"]

    def test_generator_select_works_with_one_branch_wired(self):
        node = PKG.NODE_CLASS_MAPPINGS["FlareGeneratorSelect"]()
        a = torch.zeros(1, 8, 8, 3)
        assert node.select("a", image_a=a)[0] is a

    def test_generator_select_names_the_missing_branch(self):
        node = PKG.NODE_CLASS_MAPPINGS["FlareGeneratorSelect"]()
        with pytest.raises(ValueError, match="image_a"):
            node.select("a", image_b=torch.ones(1, 8, 8, 3))

    def test_generator_select_inputs_are_lazy(self):
        cls = PKG.NODE_CLASS_MAPPINGS["FlareGeneratorSelect"]
        opt = cls.INPUT_TYPES()["optional"]
        assert opt["image_a"][1].get("lazy") is True
        assert opt["image_b"][1].get("lazy") is True

    def test_flare_pass_composites_to_image_output(self):
        # Definition of done #4: decoding the pass and the source, adding in
        # linear, and re-encoding must equal the composite output.
        img = torch.rand(1, 64, 96, 3)
        out, flare_pass, _ = run_node(img, clamp_output=False)
        manual = linear_to_srgb(srgb_to_linear(img) + srgb_to_linear(flare_pass))
        assert torch.allclose(manual, out, atol=1e-4)

    def test_flare_pass_black_where_no_flare(self):
        img = torch.rand(1, 64, 96, 3)
        empty = json.dumps({"schema_version": 1, "elements": []})
        out, flare_pass, alpha = run_node(img, preset_json=empty, clamp_output=False)
        assert flare_pass.abs().max() == 0.0
        assert alpha.abs().max() == 0.0
        assert torch.allclose(out, linear_to_srgb(srgb_to_linear(img)), atol=1e-5)

    def test_clamp_output(self):
        img = torch.ones(1, 64, 96, 3) * 0.9
        out, flare_pass, _ = run_node(img, clamp_output=True, intensity=5.0)
        assert out.max() <= 1.0
        assert flare_pass.max() <= 1.0
        out_u, flare_u, _ = run_node(img, clamp_output=False, intensity=5.0)
        assert flare_u.max() > 1.0  # HDR survives with clamp off

    def test_invalid_preset_raises_clear_error(self):
        img = torch.rand(1, 32, 32, 3)
        with pytest.raises(ValueError, match="sparkle"):
            run_node(img, preset_json=json.dumps(
                {"schema_version": 1, "elements": [{"type": "sparkle"}]}))

    def test_detect_mode_places_flare_on_bright_spot(self):
        img = torch.zeros(1, 96, 96, 3)
        img[0, 24, 72] = 1.0  # bright pixel upper right
        out, flare_pass, _ = run_node(img, position_mode="detect",
                                      detect_threshold=0.5)
        lum = flare_pass[0].sum(-1)
        idx = lum.flatten().argmax().item()
        row, col = divmod(idx, 96)
        assert abs(row - 24) <= 2 and abs(col - 72) <= 2

    def test_detect_no_lights_black_pass(self):
        img = torch.zeros(1, 64, 64, 3)
        out, flare_pass, _ = run_node(img, position_mode="detect")
        assert flare_pass.abs().max() == 0.0

    def test_depth_occlusion_dims_flare(self):
        img = torch.rand(1, 64, 64, 3) * 0.2
        # near-is-white wall covering the light's neighbourhood entirely,
        # including the light's own pixel: this is the case that used to
        # report zero occlusion and render a full flare over the occluder.
        depth = torch.full((1, 64, 64, 3), 0.9)
        _, blocked, _ = run_node(img, depth=depth, occlusion_radius=0.1)
        _, free, _ = run_node(img, depth=None)
        assert blocked.sum() < free.sum() * 0.05

    def test_open_sky_depth_does_not_occlude(self):
        img = torch.rand(1, 64, 64, 3) * 0.2
        sky = torch.zeros(1, 64, 64, 3)  # everything at the far plane
        _, with_depth, _ = run_node(img, depth=sky, occlusion_radius=0.1)
        _, free, _ = run_node(img, depth=None)
        assert torch.allclose(with_depth, free, atol=1e-5)

    def test_light_depth_controls_what_blocks(self):
        img = torch.rand(1, 64, 64, 3) * 0.2
        mid = torch.full((1, 64, 64, 3), 0.5)  # a mid-scene layer
        _, light_behind, _ = run_node(img, depth=mid, occlusion_radius=0.1,
                                      light_depth=0.0)
        _, light_in_front, _ = run_node(img, depth=mid, occlusion_radius=0.1,
                                        light_depth=0.9)
        assert light_behind.sum() < light_in_front.sum() * 0.05

    def test_screen_blend_bounded(self):
        img = torch.rand(1, 48, 48, 3)
        out, _, _ = run_node(img, blend_mode="screen", intensity=8.0,
                             clamp_output=False)
        assert out.max() <= 1.0 + 1e-5

    def test_rgba_input_accepted(self):
        img = torch.rand(1, 32, 32, 4)
        out, _, _ = run_node(img)
        assert out.shape == (1, 32, 32, 3)


class TestFlareDepthAdapter:
    def node(self):
        return PKG.NODE_CLASS_MAPPINGS["FlareDepthAdapter"]()

    def run(self, depth, **over):
        args = dict(normalize="per_batch", invert=False, blur=0.0,
                    black_point=0.0, white_point=1.0)
        args.update(over)
        return self.node().adapt(depth=depth, **args)

    def test_outputs_image_and_mask(self):
        d = torch.rand(2, 32, 48, 3)
        image, mask = self.run(d)
        assert image.shape == (2, 32, 48, 3)
        assert mask.shape == (2, 32, 48)
        assert image.min() >= 0.0 and image.max() <= 1.0

    def test_channels_agree(self):
        d = torch.rand(1, 16, 16, 3)
        image, mask = self.run(d)
        assert torch.allclose(image[..., 0], image[..., 1])
        assert torch.allclose(image[..., 0], mask)

    def test_invert_flips_convention(self):
        d = torch.zeros(1, 8, 8, 3)
        d[:, :, 4:, :] = 1.0
        plain, _ = self.run(d, invert=False)
        flipped, _ = self.run(d, invert=True)
        assert torch.allclose(plain, 1.0 - flipped, atol=1e-5)

    def test_normalizes_out_of_range_source(self):
        d = torch.rand(1, 16, 16, 3) * 50.0 + 10.0  # e.g. a raw Z-pass
        image, _ = self.run(d)
        assert image.min() == pytest.approx(0.0, abs=1e-5)
        assert image.max() == pytest.approx(1.0, abs=1e-5)

    def test_registration_contract(self):
        cls = PKG.NODE_CLASS_MAPPINGS["FlareDepthAdapter"]
        assert cls.CATEGORY == "Flarecore"
        assert cls.RETURN_TYPES == ("IMAGE", "MASK")
        assert "depth" in cls.INPUT_TYPES()["required"]

    def test_invalid_levels_raise(self):
        with pytest.raises(ValueError, match="white_point"):
            self.run(torch.rand(1, 8, 8, 3), black_point=0.9, white_point=0.2)

    def test_feeds_flare_render(self):
        # end to end: a near-is-black source, adapted, then used as the
        # occluder for a render
        img = torch.rand(1, 48, 48, 3) * 0.2
        raw = torch.full((1, 48, 48, 3), 0.05)  # near everywhere, near-is-black
        adapted, _ = self.run(raw, normalize="none", invert=True)
        _, blocked, _ = run_node(img, depth=adapted, occlusion_radius=0.1)
        _, free, _ = run_node(img, depth=None)
        assert blocked.sum() < free.sum() * 0.05


class TestFlarePresetLoader:
    def test_lists_shipped_presets(self):
        files = FlarePresetLoader.INPUT_TYPES()["required"]["preset_file"][0]
        assert "cine_blue.json" in files
        assert "specimen_all_elements.json" in files
        assert len(files) >= 5

    def test_loads_valid_json_that_validates(self):
        from flare.schema import load_preset
        loader = FlarePresetLoader()
        files = FlarePresetLoader.INPUT_TYPES()["required"]["preset_file"][0]
        for name in files:
            (text,) = loader.load(name)
            preset = load_preset(text)  # raises on invalid
            assert preset["schema_version"] == 1

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            FlarePresetLoader().load("does_not_exist.json")


def test_every_shipped_preset_renders_over_image():
    loader = FlarePresetLoader()
    files = FlarePresetLoader.INPUT_TYPES()["required"]["preset_file"][0]
    img = torch.rand(1, 72, 128, 3)
    for name in files:
        (text,) = loader.load(name)
        out, flare_pass, alpha = run_node(img, preset_json=text)
        assert torch.isfinite(out).all(), name
        assert flare_pass.max() > 0.0, name


class TestColorspace:
    """`colorspace: linear` must be a true pass-through: an EXR plate goes in
    scene-linear and comes back scene-linear plus flare, with no hidden
    encode anywhere."""

    def test_linear_composite_is_plate_plus_flare_pass(self):
        # the compositor contract: in linear with clamp off, adding the
        # flare pass to the plate reproduces the composite EXACTLY — no
        # hidden encode, no gamma, nothing lost on a Nuke round trip
        plate = torch.rand(1, 72, 108, 3) * 2.0          # HDR values above 1
        out, fp, _ = run_node(plate, colorspace="linear", clamp_output=False,
                              light_x=0.4, light_y=0.5)
        assert torch.allclose(out, plate + fp, atol=1e-5)
        assert float(fp.max()) > 0.0

    def test_srgb_default_still_encodes(self):
        plate = torch.full((1, 48, 48, 3), 0.5)
        lin_out = run_node(plate, colorspace="linear")[0]
        srgb_out = run_node(plate, colorspace="srgb")[0]
        assert not torch.allclose(lin_out, srgb_out)

    def test_linear_hdr_survives_with_clamp_off(self):
        plate = torch.full((1, 48, 48, 3), 3.0)
        out, _, _ = run_node(plate, colorspace="linear", clamp_output=False)
        assert float(out.max()) >= 3.0


class TestChunkedRendering:
    """Streaming the clip through in slices must be invisible: identical
    pixels, identical tracking, identical flicker phase."""

    def _clip(self, b=11):
        clip = torch.zeros(b, 72, 128, 3)
        for i in range(b):
            cx = int((0.1 + 0.8 * i / (b - 1)) * 128)
            clip[i, 30:40, cx:cx + 10] = 1.0
            clip[i] += 0.05
        return clip.clamp(0, 1)

    def test_depth_need_not_match_the_image_resolution(self):
        """Depth models return their own resolution -- Depth Anything hands
        back 512x910 for a 720x1280 clip. Occlusion samples the map in
        normalised u,v, so the two never had to agree; the per-chunk buffer
        must be shaped from the DEPTH, not from the image."""
        clip = self._clip(6)
        h, w = clip.shape[1], clip.shape[2]
        depth = torch.rand(6, h * 512 // 720, w * 910 // 1280, 3)
        assert depth.shape[1:3] != clip.shape[1:3]      # genuinely mismatched
        for chunk_frames, norm in ((2, "per_batch"), (0, "per_frame"),
                                   (64, "as_is")):
            out, fp, alpha = run_node(
                clip, depth=depth, depth_normalize=norm,
                depth_temporal_smooth=0.4, chunk_frames=chunk_frames)
            assert out.shape == clip.shape
            assert torch.isfinite(fp).all()

    @pytest.mark.parametrize("mode,extra", [
        ("manual", {}), ("detect_with_manual_offset", {"light_x": 0.6, "light_y": 0.4}),
        ("detect", {}), ("track", {}), ("lock", {}), ("track_dots", {}),
        ("follow", {"light_x": 0.6, "light_y": -0.1}),
        ("point_track", {"track_points": "0.5,0.5"}),
        ("path", {"light_path": "0.2,0.3; 0.8,0.6"}),
    ])
    def test_every_mode_is_chunk_invariant(self, mode, extra):
        """Modes that read the whole clip (scene motion, the point tracker,
        the whole-clip solve) gather it from slices; the slice size must
        never show in the pixels."""
        clip = self._clip(9)
        # give the scene some texture so motion estimation has features
        g = torch.Generator().manual_seed(1)
        clip = (clip + torch.rand(clip.shape, generator=g) * 0.25).clamp(0, 1)
        args = dict(position_mode=mode, detect_threshold=0.6, scene_lock=1.0,
                    track_smoothing=0.85, **extra)
        whole = run_node(clip, chunk_frames=64, **args)
        sliced = run_node(clip, chunk_frames=2, **args)
        for a, b in zip(whole, sliced):
            assert torch.allclose(a, b, atol=2e-5),                 f"{mode}: slicing changed the pixels by {(a - b).abs().max():.2e}"

    def test_chunked_equals_unchunked_with_tracking_and_flicker(self):
        import json as _json
        p = {"schema_version": 1,
             "global": {"flicker_amount": 0.6, "seed": 9},
             "elements": [
                 {"type": "glow", "params": {"softness": 0.3, "falloff": 2.0}},
                 {"type": "glow", "light_mask": 1.0, "scale": 1.5,
                  "params": {"softness": 1.0, "falloff": 0.8}}]}
        clip = self._clip()
        kw = dict(preset_json=_json.dumps(p), position_mode="track",
                  scene_color=0.4)
        whole = run_node(clip, chunk_frames=64, **kw)
        sliced = run_node(clip, chunk_frames=2, **kw)   # 6 slices, odd tail
        for a, b in zip(whole, sliced):
            # one float ulp of slack: conv batch size changes reduction
            # order, nothing more
            assert torch.allclose(a, b, atol=1e-6), "chunking changed the pixels"

    def test_chunked_equals_unchunked_with_depth(self):
        clip = self._clip(7)
        depth = torch.rand(7, 72, 128, 3)
        kw = dict(depth=depth, depth_normalize="per_batch", depth_blur=0.01,
                  depth_temporal_smooth=0.4, light_x=0.4, light_y=0.5)
        whole = run_node(clip, chunk_frames=64, **kw)
        sliced = run_node(clip, chunk_frames=3, **kw)
        for a, b in zip(whole, sliced):
            assert torch.allclose(a, b, atol=1e-6), "depth chunking drifted"

    def test_auto_chunk_is_sane(self):
        r = PKG.NODE_CLASS_MAPPINGS["FlareRender"]()
        assert r._chunk_size(0, 500, 1080, 1920, torch.device("cpu")) >= 1
        assert r._chunk_size(5, 500, 1080, 1920, torch.device("cpu")) == 5
        assert r._chunk_size(0, 3, 64, 64, torch.device("cpu")) == 3


class TestCacheInvalidation:
    """ComfyUI caches a node's result against its INPUTS. Editing the engine
    and leaving the graph alone -- what happens all through developing a look
    -- left the inputs identical, so Run replayed the previous render in
    0.00s and every fix looked like it had not worked."""

    def test_editing_the_engine_changes_the_cache_key(self):
        import pathlib
        cls = PKG.NODE_CLASS_MAPPINGS["FlareRender"]
        before = cls.IS_CHANGED(image=None, preset_json="{}")
        pathlib.Path(PKG.__file__).parent.joinpath("flare", "engine.py").touch()
        assert cls.IS_CHANGED(image=None, preset_json="{}") != before

    def test_it_is_stable_when_nothing_is_edited(self):
        """Otherwise every Run re-renders and caching stops working at all."""
        cls = PKG.NODE_CLASS_MAPPINGS["FlareRender"]
        assert cls.IS_CHANGED(image=None, preset_json="{}") ==                cls.IS_CHANGED(image=None, preset_json="{}")

    def test_it_accepts_whatever_inputs_comfy_passes(self):
        cls = PKG.NODE_CLASS_MAPPINGS["FlareRender"]
        assert isinstance(cls.IS_CHANGED(), str)
