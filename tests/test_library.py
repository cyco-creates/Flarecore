# SPDX-License-Identifier: Apache-2.0
"""The library's shape: one taxonomy, agreed on by everything that uses it.

Four places name the element families -- the folders under elements/, the
prompt bank's top-level keys, the editor's CATEGORY_OF targets and every
preset element's `slot` -- and a fifth, the preset categories, decides how
the editor groups its menu. Nothing enforces agreement at runtime, so a
family added in one place and forgotten in another goes unnoticed until a
gallery opens empty. These tests are that enforcement.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flare.schema import (ELEMENT_SLOTS, PRESET_CATEGORIES, ELEMENT_TYPES,
                          validate_preset)

PRESETS = sorted((ROOT / "presets").glob("*.json"))
UI_JS = (ROOT / "web" / "flarecore_ui.js").read_text(encoding="utf-8")


def preset(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_presets_to_check():
    assert len(PRESETS) >= 20


class TestFamilies:
    def test_element_folders_are_the_eight_families(self):
        folders = {p.name for p in (ROOT / "elements").iterdir() if p.is_dir()}
        assert folders == set(ELEMENT_SLOTS)

    def test_prompt_bank_uses_the_same_families(self):
        bank = json.loads(
            (ROOT / "prompts" / "element_prompts.json").read_text(encoding="utf-8"))
        assert set(bank) == set(ELEMENT_SLOTS)

    def test_the_editor_files_every_look_into_one_of_them(self):
        block = UI_JS.split("const CATEGORY_OF = {", 1)[1].split("};", 1)[0]
        targets = set(re.findall(r':\s*"(\w+)"', block))
        assert targets <= set(ELEMENT_SLOTS), targets - set(ELEMENT_SLOTS)

    def test_legacy_names_remap_into_them(self):
        """Pre-consolidation folder names must still land somewhere real."""
        from conftest import load_package
        legacy = load_package().nodes.library._LEGACY_CATEGORY
        assert set(legacy.values()) <= set(ELEMENT_SLOTS)


class TestPresetsAreFiled:
    @pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
    def test_every_preset_names_a_known_category(self, path):
        assert preset(path).get("category") in PRESET_CATEGORIES

    @pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
    def test_every_element_names_a_known_slot(self, path):
        for i, elem in enumerate(preset(path)["elements"]):
            assert elem.get("slot") in ELEMENT_SLOTS, f"element {i} ({elem['type']})"

    @pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
    def test_every_element_carries_a_label(self, path):
        """The editor lists rows by label; an unnamed row is a blank line."""
        for i, elem in enumerate(preset(path)["elements"]):
            assert elem.get("label"), f"element {i} ({elem['type']}) has no label"

    @pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
    def test_every_preset_validates(self, path):
        validate_preset(preset(path))

    def test_a_subcategory_never_appears_alone(self):
        for path in PRESETS:
            d = preset(path)
            if d.get("subcategory"):
                assert d.get("category"), path.stem


class TestTexturesResolve:
    def test_every_referenced_texture_is_present(self):
        """A preset that names a missing texture raises at render time, so a
        shipped one must ship its files too."""
        missing = []
        for path in PRESETS:
            for elem in preset(path)["elements"]:
                ref = elem.get("params", {}).get("file")
                if elem["type"] == "texture" and ref:
                    if not (ROOT / "elements" / ref).is_file():
                        missing.append(f"{path.stem} -> {ref}")
        assert not missing, missing

    def test_a_texture_sits_in_the_family_its_slot_claims(self):
        for path in PRESETS:
            for elem in preset(path)["elements"]:
                if elem["type"] != "texture":
                    continue
                ref = elem.get("params", {}).get("file", "")
                if ref:
                    assert ref.split("/")[0] == elem.get("slot"), \
                        f"{path.stem}: {ref} filed under {elem.get('slot')!r}"


class TestStyleBank:
    """The forge's extra-style tails: real shooting conditions, grouped."""

    @staticmethod
    def styles():
        from conftest import load_package
        return load_package().nodes.elements_lab._load_style_bank()

    def test_it_loads_and_is_grouped(self):
        s = self.styles()
        assert len(s) >= 4, "expected several groups of conditions"
        assert all(isinstance(v, dict) and v for v in s.values())

    def test_every_tail_is_a_usable_sentence(self):
        for group, entries in self.styles().items():
            for name, text in entries.items():
                where = f"{group}/{name}"
                assert 30 <= len(text) <= 400, where
                assert text[0].islower(), f"{where}: a tail is appended, so it "\
                                          f"should not start a sentence"
                assert not text.endswith("."), f"{where}: no trailing full stop"

    def test_names_are_unique_across_groups(self):
        seen = []
        for entries in self.styles().values():
            seen += list(entries)
        assert len(seen) == len(set(seen)), "a style name appears twice"

    def test_no_brand_names(self):
        """The pack describes optics, never products."""
        banned = ("kodak", "fuji", "tiffen", "pro-mist", "cooke", "zeiss",
                  "arri", "panavision", "leitz", "leica", "canon", "nikon",
                  "hawk", "atlas", "laowa", "sigma")
        for group, entries in self.styles().items():
            for name, text in entries.items():
                low = (name + " " + text).lower()
                hits = [b for b in banned if b in low]
                assert not hits, f"{group}/{name}: {hits}"

    def test_appends_cleanly_to_a_bank_prompt(self):
        from conftest import load_package
        lab = load_package().nodes.elements_lab
        bank = lab._load_prompt_bank()
        cat = next(iter(bank))
        name = next(iter(bank[cat]))
        style = next(iter(next(iter(self.styles().values())).values()))
        node = lab.FlareElementPrompts()
        prompt = node.pick(f"{cat}/{name}", style, "")[0]
        assert prompt.endswith(style)
        assert bank[cat][name] in prompt


class TestSlotsSuitTheirType:
    """A slot decides which shelf of the library opens when the row's name is
    clicked, so it has to offer plausible replacements for what is there."""

    SENSIBLE = {
        "glow": {"glows", "ghosts"},
        "iris": {"ghosts", "rings"},
        "orbs": {"ghosts"},
        "streak": {"streaks"},
        "glint": {"rays", "streaks"},      # points:1 is a one-sided streak
        "ring": {"rings", "hoops"},
        "hoop": {"hoops", "rings"},
        "spectral": {"rings", "hoops"},
        "texture": set(ELEMENT_SLOTS),
    }

    def test_every_type_has_a_rule(self):
        assert set(self.SENSIBLE) == set(ELEMENT_TYPES)

    @pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
    def test_slots_match_their_element_type(self, path):
        for i, elem in enumerate(preset(path)["elements"]):
            allowed = self.SENSIBLE[elem["type"]]
            assert elem["slot"] in allowed, \
                f"element {i}: {elem['type']} filed under {elem['slot']!r}"
