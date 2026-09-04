# SPDX-License-Identifier: Apache-2.0
"""FlarePresetLoader: pick a preset file and output its JSON as a string."""

from pathlib import Path

PRESET_DIR = Path(__file__).resolve().parents[1] / "presets"


def _preset_files():
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.name for p in PRESET_DIR.glob("*.json"))


class FlarePresetLoader:
    CATEGORY = "flare"
    FUNCTION = "load"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("preset_json",)

    @classmethod
    def INPUT_TYPES(cls):
        # rescanned on every call so new preset files show up without a
        # ComfyUI restart
        files = _preset_files() or ["<no presets found>"]
        return {"required": {"preset_file": (files,)}}

    @classmethod
    def IS_CHANGED(cls, preset_file):
        path = PRESET_DIR / preset_file
        try:
            return str(path.stat().st_mtime)
        except OSError:
            return ""

    def load(self, preset_file):
        path = PRESET_DIR / preset_file
        if not path.is_file():
            raise FileNotFoundError(
                f"preset file '{preset_file}' not found in {PRESET_DIR}"
            )
        return (path.read_text(encoding="utf-8"),)
