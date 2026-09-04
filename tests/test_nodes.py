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


def _load_package():
    """Load the repo as a package the way ComfyUI does (importlib, package name)."""
    if "comfyui_flarecore" in sys.modules:
        return sys.modules["comfyui_flarecore"]
    spec = importlib.util.spec_from_file_location(
        "comfyui_flarecore", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["comfyui_flarecore"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


PKG = _load_package()
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
            "FlareElementPrompts", "FlareTexturePrepare",
            "FlareElementSave", "FlareElementPicker",
        }
        assert PKG.WEB_DIRECTORY == "./web"
        assert FlareRender.CATEGORY == "flare"
        assert FlareRender.RETURN_TYPES == ("IMAGE", "IMAGE", "MASK")
        it = FlareRender.INPUT_TYPES()
        assert "image" in it["required"]
        assert "depth" in it["optional"]

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
        assert cls.CATEGORY == "flare"
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
        assert "clean_35mm.json" in files
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
