# SPDX-License-Identifier: Apache-2.0
"""Tests for the movable flare anchor and texture elements."""
import json
import sys
from pathlib import Path

import pytest
import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flare.axis import axis_angle, element_center
from flare.engine import render_stack
from flare.schema import validate_preset
from flare.texture_prep import (
    subtract_floor, center_on_energy, feather_border, prepare_element,
)
from flare.grid import uv_to_grid


def tiny_glow(**over):
    elem = {"type": "glow", "offset": 1.0, "scale": 0.15, "auto_rotate": False,
            "params": {"softness": 0.15, "falloff": 3.0}}
    elem.update(over)
    return validate_preset({"schema_version": 1, "elements": [elem]})


def argmax_uv(field_hw):
    idx = field_hw.flatten().argmax().item()
    h, w = field_hw.shape
    row, col = divmod(idx, w)
    return (col + 0.5) / w, (row + 0.5) / h


class TestAnchor:
    def test_axis_math_generalizes(self):
        # default anchor (0,0) preserves the old behaviour
        assert element_center(0.7, -0.3, 1.0) == (0.0, 0.0)
        # custom anchor: t=1 sits on it, t=2 mirrors past it
        cx1, cy1 = element_center(0.2, 0.2, 1.0, 0.6, -0.4)
        assert cx1 == pytest.approx(0.6) and cy1 == pytest.approx(-0.4)
        cx, cy = element_center(0.2, 0.2, 2.0, 0.6, -0.4)
        assert cx == pytest.approx(1.0) and cy == pytest.approx(-1.0)
        assert axis_angle(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0)

    def test_element_lands_on_anchor(self):
        p = tiny_glow(offset=1.0)
        ax, ay = uv_to_grid(0.8, 0.7, 256, 256)
        lx, ly = uv_to_grid(0.2, 0.2, 256, 256)
        lights = [{"x": lx, "y": ly, "ax": ax, "ay": ay, "brightness": 1.0}]
        flare = render_stack(p, lights, 256, 256, "cpu", torch.float32)
        u, v = argmax_uv(flare.sum(-1))
        assert abs(u - 0.8) < 0.01 and abs(v - 0.7) < 0.01

    def test_spacing_scales_with_anchor_distance(self):
        # two glows at t=0 and t=1: their pixel distance equals |anchor-light|
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "glow", "offset": 0.0, "scale": 0.1,
             "params": {"softness": 0.15, "falloff": 3.0}},
            {"type": "glow", "offset": 1.0, "scale": 0.1,
             "params": {"softness": 0.15, "falloff": 3.0}},
        ]})
        lx, ly = uv_to_grid(0.3, 0.5, 256, 256)

        def peak_distance(anchor_u):
            ax, ay = uv_to_grid(anchor_u, 0.5, 256, 256)
            lights = [{"x": lx, "y": ly, "ax": ax, "ay": ay}]
            flare = render_stack(p, lights, 256, 256, "cpu", torch.float32).sum(-1)
            row = flare[128]
            # two distinct peaks on the horizontal axis
            left = row[:int(0.35 * 256)].argmax().item()
            right = int(0.35 * 256) + row[int(0.35 * 256):].argmax().item()
            return (right - left) / 256

        near = peak_distance(0.45)
        far = peak_distance(0.9)
        assert far > near * 2.5  # spacing grew with the anchor distance
        assert near == pytest.approx(0.15, abs=0.02)
        assert far == pytest.approx(0.6, abs=0.02)


class TestTextureElement:
    def make_tex(self):
        # bright 8x8 block in the upper-left quadrant of a 64^2 texture
        tex = torch.zeros(64, 64)
        tex[12:20, 12:20] = 1.0
        return tex

    def test_schema_accepts_and_validates(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "texture", "params": {"file": "glows/warm_soft.png"}}]})
        assert p["elements"][0]["params"]["channel"] == "auto"
        with pytest.raises(ValueError, match="relative path"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "texture", "params": {"file": "../evil.png"}}]})
        with pytest.raises(ValueError, match="channel"):
            validate_preset({"schema_version": 1, "elements": [
                {"type": "texture", "params": {"file": "a.png", "channel": "alpha"}}]})

    def test_missing_texture_raises_clearly(self):
        from flare.elements import ELEMENT_FUNCTIONS
        u = torch.zeros(4, 4)
        with pytest.raises(ValueError, match="no texture loaded"):
            ELEMENT_FUNCTIONS["texture"](u, u, {"file": "x.png"})

    def test_renders_through_engine_with_transforms(self):
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "texture", "offset": 0.0, "scale": 0.5,
             "auto_rotate": False, "params": {"file": "ignored.png"}}]})
        p["elements"][0]["params"]["_texture"] = self.make_tex()
        lights = [{"x": 0.0, "y": 0.0}]
        flare = render_stack(p, lights, 128, 128, "cpu", torch.float32)
        lum = flare.sum(-1)
        assert lum.max() > 0.5
        # the block sits upper-left of texture centre -> upper-left of frame centre
        u, v = argmax_uv(lum)
        assert u < 0.5 and v < 0.5
        # zero outside the texture footprint
        assert lum[5, 5] == 0.0

    def test_rgb_texture_keeps_colour(self):
        tex = torch.zeros(32, 32, 3)
        tex[8:24, 8:24, 0] = 1.0  # pure red block
        p = validate_preset({"schema_version": 1, "elements": [
            {"type": "texture", "offset": 0.0, "scale": 0.5,
             "params": {"file": "ignored.png"}}]})
        p["elements"][0]["params"]["_texture"] = tex
        flare = render_stack(p, [{"x": 0.0, "y": 0.0}], 64, 64, "cpu", torch.float32)
        assert flare[..., 0].max() > 0.5
        assert flare[..., 1].max() == 0.0
        assert flare[..., 2].max() == 0.0


class TestTexturePrep:
    def test_subtract_floor(self):
        img = torch.tensor([[[0.05, 0.05, 0.05], [0.5, 0.5, 0.5]]])
        out = subtract_floor(img, 0.06)
        assert out[0, 0].max() == 0.0
        assert 0.4 < out[0, 1, 0] < 0.5

    def test_center_on_energy(self):
        img = torch.zeros(32, 32, 3)
        img[4:8, 4:8] = 1.0
        out = center_on_energy(img)
        lum = out.sum(-1)
        ys, xs = torch.nonzero(lum > 0.5, as_tuple=True)
        assert abs(ys.float().mean().item() - 15.5) < 1.0
        assert abs(xs.float().mean().item() - 15.5) < 1.0

    def test_feather_zeroes_borders(self):
        img = torch.ones(64, 64, 3)
        out = feather_border(img, 0.2)
        assert out[0, 0].max() == pytest.approx(0.0, abs=1e-4)
        assert out[32, 32].min() == pytest.approx(1.0)

    def test_full_pipeline(self):
        img = torch.rand(80, 120, 3) * 0.1
        img[30:50, 60:80] = 0.9
        out = prepare_element(img, mode="rgb", black_point=0.15,
                              autocenter=True, feather=0.1, size=128)
        assert out.shape == (128, 128, 3)
        assert out.min() >= 0.0 and out.max() <= 1.0
        # borders feathered to black
        assert out[0, 0].max() == pytest.approx(0.0, abs=1e-4)


class TestLibraryAndNodes:
    def _pkg(self):
        from test_nodes import PKG
        return PKG

    def test_library_lists_starters(self):
        pkg = self._pkg()
        import sys as _s
        lib = _s.modules["comfyui_flarecore.nodes.library"]
        files = lib.list_elements()
        assert "glows/warm_soft.png" in files
        assert "rings/rainbow_ring.png" in files

    def test_load_texture_linear_and_cached(self):
        import sys as _s
        lib = _s.modules["comfyui_flarecore.nodes.library"]
        a = lib.load_texture("rings/rainbow_ring.png", "rgb")
        b = lib.load_texture("rings/rainbow_ring.png", "rgb")
        assert a is b  # cache hit
        assert a.dim() == 3 and a.shape[-1] == 3
        with pytest.raises(ValueError, match="not found"):
            lib.load_texture("nope/missing.png")

    def test_texture_preset_renders_via_node(self):
        import json
        from test_nodes import run_node
        preset = json.dumps({"schema_version": 1, "elements": [
            {"type": "texture", "offset": 0.9, "scale": 0.3,
             "intensity": 1.0, "params": {"file": "ghosts/hex_soft.png"}}]})
        img = torch.zeros(1, 96, 128, 3)
        out, flare_pass, _ = run_node(img, preset_json=preset)
        assert flare_pass.max() > 0.05

    def test_prompts_node(self):
        pkg = self._pkg()
        node = pkg.NODE_CLASS_MAPPINGS["FlareElementPrompts"]()
        options = pkg.NODE_CLASS_MAPPINGS["FlareElementPrompts"].INPUT_TYPES()
        entries = options["required"]["element"][0]
        assert any(e.startswith("glows/") for e in entries)
        prompt, cat, name = node.pick(entries[0], "", "")[:3]
        assert "black background" in prompt
        prompt2, _, _ = node.pick(entries[0], "cold teal colour", "")[:3]
        assert prompt2.endswith("cold teal colour")
        prompt3, _, _ = node.pick(entries[0], "", "my own prompt")[:3]
        assert prompt3 == "my own prompt"

    def test_prepare_and_save_roundtrip(self, tmp_path, monkeypatch):
        import sys as _s
        pkg = self._pkg()
        lab = _s.modules["comfyui_flarecore.nodes.elements_lab"]
        lib = _s.modules["comfyui_flarecore.nodes.library"]
        monkeypatch.setattr(lab, "ELEMENTS_DIR", tmp_path)
        monkeypatch.setattr(lib, "ELEMENTS_DIR", tmp_path)

        img = torch.rand(1, 96, 96, 3) * 0.1
        img[0, 40:60, 40:60] = 0.95
        prep = pkg.NODE_CLASS_MAPPINGS["FlareTexturePrepare"]()
        tex, alpha = prep.prepare(img, "rgb", 0.12, True, 0.12, 128)
        assert tex.shape == (1, 128, 128, 3)

        save = pkg.NODE_CLASS_MAPPINGS["FlareElementSave"]()
        r = save.save(tex, "My Category!", "Test Element", False)
        ref = r["result"][0]
        assert ref == "my_category/test_element.png"
        assert (tmp_path / ref).is_file()
        # collision without overwrite picks a new name
        r2 = save.save(tex, "My Category!", "Test Element", False)
        assert r2["result"][0] == "my_category/test_element_02.png"
        # loads back through the library
        loaded = lib.load_texture(ref)
        assert loaded.shape[0] == 128


class TestMarginAndLegacyRefs:
    def test_margin_leaves_breathing_room(self):
        from flare.texture_prep import prepare_element
        img = torch.ones(256, 256, 3)              # content out to every edge
        out = prepare_element(img, black_point=0.0, autocenter=False,
                              feather=0.0, size=256, margin=0.25)
        assert out.shape == (256, 256, 3)
        border = 256 // 4 - 2                       # just inside the margin
        assert out[:border].sum() == 0              # top margin is pure black
        assert out[-border:].sum() == 0
        assert out[:, :border].sum() == 0
        assert out[:, -border:].sum() == 0
        assert out[128 - 20:128 + 20, 128 - 20:128 + 20].min() > 0.5

    def test_margin_zero_is_previous_behaviour(self):
        from flare.texture_prep import prepare_element
        img = torch.rand(128, 128, 3)
        a = prepare_element(img, size=96)
        b = prepare_element(img, size=96, margin=0.0)
        assert torch.allclose(a, b)

    def test_legacy_category_refs_still_resolve(self):
        from test_nodes import PKG
        lib = PKG.nodes.library
        # the library was reorganised; old presets may still say iris_ghosts/
        t = lib.load_texture("iris_ghosts/hex_soft.png", "luminance")
        assert t.shape[0] > 0


def _lib():
    from test_nodes import PKG
    return PKG.nodes.library


class TestLensPlates:
    """Dirt, orbs and droplets sit ON the front element: they cover the whole
    frame, carry no feathered border, and are revealed by the light rather
    than by their own shape."""

    def test_wide_frame_keeps_the_full_image(self):
        from flare.texture_prep import prepare_element
        img = torch.ones(720, 1280, 3) * 0.8
        out = prepare_element(img, black_point=0.0, feather=0.4, size=256,
                              margin=0.25, frame="wide_16_9")
        assert out.shape == (144, 256, 3)          # 16:9, not square
        # no feather and no margin: the corners are as bright as the middle
        for corner in (out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]):
            assert float(corner.mean()) == pytest.approx(0.8, abs=0.02)

    def test_wide_frame_does_not_recentre(self):
        from flare.texture_prep import prepare_element
        img = torch.zeros(720, 1280, 3)
        img[100:160, 100:160] = 1.0                # a mark up in the corner
        out = prepare_element(img, black_point=0.0, autocenter=True,
                              feather=0.0, size=256, frame="wide_16_9")
        ys, xs = torch.nonzero(out.mean(-1) > 0.5, as_tuple=True)
        assert xs.float().mean() < 128 * 0.5       # still off to the left
        assert ys.float().mean() < 144 * 0.5

    def test_wide_frame_does_not_distort_a_square_source(self):
        """The generator makes square images; the plate is 16:9. Squashing one
        into the other stretches every droplet into an ellipse and skews every
        scratch. Fill the frame by scaling to cover and cropping the overflow,
        which keeps circles circular."""
        from flare.texture_prep import prepare_element
        img = torch.zeros(512, 512, 3)
        img[192:320, 192:320] = 1.0               # a centred SQUARE mark
        out = prepare_element(img, black_point=0.0, autocenter=False,
                              feather=0.0, size=256, frame="wide_16_9")
        assert out.shape == (144, 256, 3)
        ys, xs = torch.nonzero(out.mean(-1) > 0.5, as_tuple=True)
        h = float(ys.max() - ys.min() + 1)
        w = float(xs.max() - xs.min() + 1)
        assert w / h == pytest.approx(1.0, abs=0.06), (
            f"square mark came out {w:.0f}x{h:.0f} — the source was stretched")

    def test_wide_frame_leaves_a_native_16_9_source_alone(self):
        """A plate generated at 16:9 must survive untouched: cover-scaling an
        image that already matches the target crops nothing."""
        from flare.texture_prep import prepare_element
        img = torch.zeros(576, 1024, 3)
        img[100:160, 100:160] = 1.0
        out = prepare_element(img, black_point=0.0, autocenter=False,
                              feather=0.0, size=256, frame="wide_16_9")
        ref = torch.nn.functional.interpolate(
            img.permute(2, 0, 1).unsqueeze(0), size=(144, 256),
            mode="bilinear", align_corners=False, antialias=True)[0].permute(1, 2, 0)
        assert torch.allclose(out, ref.clamp(0.0, 1.0), atol=1e-5)

    def test_square_frame_is_unchanged(self):
        from flare.texture_prep import prepare_element
        img = torch.rand(300, 300, 3)
        a = prepare_element(img, size=96)
        b = prepare_element(img, size=96, frame="square")
        assert torch.allclose(a, b)

    def test_fill_frame_covers_a_wide_frame_corner_to_corner(self):
        lib = _lib()
        # a plate that is solid everywhere: with fill_frame it must reach
        # every corner of a 16:9 render, where a normal element would not
        plate = torch.ones(1, 64, 114, 3)
        ref = "lens_dirt/_test_fill.png"
        path = lib.ELEMENTS_DIR / "lens_dirt" / "_test_fill.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray((plate[0].numpy() * 255).astype(np.uint8)).save(path)
        try:
            preset = {"schema_version": 1, "elements": [{
                "type": "texture", "offset": 0.0, "scale": 1.0,
                "intensity": 1.0, "auto_rotate": False, "screen_space": True,
                "fill_frame": True,
                "params": {"file": ref, "channel": "luminance"}}]}
            v = validate_preset(preset)
            lib.resolve_preset_textures(v, device="cpu", dtype=torch.float32)
            out = render_stack(v, [{"x": 0.0, "y": 0.0}], 90, 160, "cpu",
                               torch.float32).sum(-1)
            for corner in (out[0, 0], out[0, -1], out[-1, 0], out[-1, -1]):
                assert float(corner) > 0.5, "fill_frame left a corner empty"

            v2 = validate_preset({"schema_version": 1, "elements": [{
                "type": "texture", "offset": 0.0, "scale": 1.0,
                "intensity": 1.0, "auto_rotate": False, "screen_space": True,
                "params": {"file": ref, "channel": "luminance"}}]})
            lib.resolve_preset_textures(v2, device="cpu", dtype=torch.float32)
            plain = render_stack(v2, [{"x": 0.0, "y": 0.0}], 90, 160, "cpu",
                                 torch.float32).sum(-1)
            # without it the square element cannot reach the wide frame's sides
            assert float(plain[45, 0]) < 0.5
        finally:
            path.unlink(missing_ok=True)

    def test_mask_falloff_controls_how_far_the_dirt_shows(self):
        from test_nodes import run_node
        preset = json.dumps({"schema_version": 1, "elements": [{
            "type": "glow", "offset": 0.0, "scale": 2.5, "intensity": 1.0,
            "auto_rotate": False, "light_mask": 1.0,
            "params": {"softness": 1.2, "falloff": 0.4}}]})
        plate = torch.zeros(1, 90, 160, 3)
        tight = run_node(plate, preset_json=preset, light_x=0.5, light_y=0.5,
                         mask_falloff=0.1)[1]
        wide = run_node(plate, preset_json=preset, light_x=0.5, light_y=0.5,
                        mask_falloff=0.9)[1]
        assert wide.sum() > tight.sum() * 2


class TestGenerationShape:
    """The plate is made 16:9, not squashed into it afterwards."""

    def _pick(self, element):
        from test_nodes import PKG
        node = PKG.NODE_CLASS_MAPPINGS["FlareElementPrompts"]()
        entries = node.INPUT_TYPES()["required"]["element"][0]
        match = next((e for e in entries if e.startswith(element + "/")), None)
        if match is None:
            pytest.skip(f"no {element} entries in the prompt bank")
        return node.pick(match, "", "")

    def test_lens_dirt_generates_wide(self):
        out = self._pick("lens_dirt")
        w, h = out[3], out[4]
        assert w / h == pytest.approx(16 / 9, abs=1e-6), f"{w}x{h} is not 16:9"
        assert w % 16 == 0 and h % 16 == 0, "latent sides must be a multiple of 16"

    def test_other_categories_generate_square(self):
        out = self._pick("glows")
        assert out[3] == out[4]

    def test_existing_outputs_keep_their_positions(self):
        """widgets and links are positional: gen_width/gen_height are appended,
        so a saved workflow's prompt/category/element_name links still land."""
        from test_nodes import PKG
        cls = PKG.NODE_CLASS_MAPPINGS["FlareElementPrompts"]
        assert cls.RETURN_NAMES[:3] == ("prompt", "category", "element_name")


class TestAutoFramePicksTheTreatment:
    """A lens_dirt element must come out of the forge as a full-frame 16:9
    plate without anyone remembering to flip a switch."""

    def _node(self):
        from test_nodes import PKG
        return PKG.NODE_CLASS_MAPPINGS["FlareTexturePrepare"]()

    def _run(self, category, frame="auto"):
        img = torch.rand(1, 720, 1280, 3) * 0.8 + 0.1
        out, _ = self._node().prepare(
            img, mode="rgb", black_point=0.0, autocenter=True, feather=0.2,
            size=512, margin=0.25, frame=frame, category=category)
        return out

    def test_lens_dirt_is_wide_and_unfeathered(self):
        out = self._run("lens_dirt")
        assert out.shape[1:3] == (288, 512), "expected 16:9, got %s" % (out.shape,)
        # no feather and no margin: the corners carry image, not black
        for corner in (out[0, 0, 0], out[0, 0, -1], out[0, -1, 0], out[0, -1, -1]):
            assert float(corner.mean()) > 0.02

    def test_other_categories_stay_square_with_margin(self):
        out = self._run("glows")
        assert out.shape[1:3] == (512, 512)
        assert float(out[0, :40].sum()) == 0.0        # the breathing margin

    def test_auto_is_case_and_space_tolerant(self):
        assert self._run(" Lens_Dirt ").shape[1:3] == (288, 512)

    def test_explicit_frame_still_wins(self):
        assert self._run("lens_dirt", frame="square").shape[1:3] == (512, 512)
        assert self._run("glows", frame="wide_16_9").shape[1:3] == (288, 512)

    def test_missing_category_falls_back_to_square(self):
        assert self._run("").shape[1:3] == (512, 512)
