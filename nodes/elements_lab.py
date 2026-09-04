# SPDX-License-Identifier: Apache-2.0
"""Nodes for making custom flare element textures.

The pipeline: FlareElementPrompts hands an editable, categorised prompt to
whatever image model the user runs (the shipped workflow uses their local
Krea2), FlareTexturePrepare turns the generated picture into a compositing-
safe element texture, FlareElementSave files it in the element library, and
FlareElementPicker references library entries from other nodes. The engine
itself never generates anything.
"""

import json
import re
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from ..flare.colorspace import luminance
from ..flare.texture_prep import prepare_element
from .library import ELEMENTS_DIR, list_elements

PROMPTS_FILE = Path(__file__).resolve().parents[1] / "prompts" / "element_prompts.json"


def _load_prompt_bank() -> dict:
    try:
        with open(PROMPTS_FILE, encoding="utf-8") as f:
            bank = json.load(f)
        if isinstance(bank, dict):
            return {
                str(cat): {str(k): str(v) for k, v in entries.items()}
                for cat, entries in bank.items() if isinstance(entries, dict)
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _sanitize(name: str, fallback: str) -> str:
    clean = re.sub(r"[^a-z0-9_\-]+", "_", name.strip().lower()).strip("_")
    return clean or fallback


class FlareElementPrompts:
    CATEGORY = "flare"
    FUNCTION = "pick"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "category", "element_name")

    @classmethod
    def INPUT_TYPES(cls):
        bank = _load_prompt_bank()
        entries = [f"{cat}/{name}" for cat in bank for name in bank[cat]]
        return {
            "required": {
                "element": (entries or ["<no prompts found>"],),
                "extra_style": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Appended to the bank prompt: colour, mood, lens character.",
                }),
                "custom_prompt": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Non-empty replaces the bank prompt entirely.",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, element, extra_style, custom_prompt):
        try:
            return str(PROMPTS_FILE.stat().st_mtime)
        except OSError:
            return ""

    def pick(self, element, extra_style, custom_prompt):
        category, _, name = element.partition("/")
        if custom_prompt.strip():
            prompt = custom_prompt.strip()
        else:
            bank = _load_prompt_bank()
            try:
                prompt = bank[category][name]
            except KeyError:
                raise ValueError(
                    f"prompt '{element}' not found in {PROMPTS_FILE.name}; "
                    "re-open the node to rescan the bank"
                )
        if extra_style.strip():
            prompt = f"{prompt} {extra_style.strip()}"
        return (prompt, category or "custom", name or "element")


class FlareTexturePrepare:
    CATEGORY = "flare"
    FUNCTION = "prepare"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("texture", "texture_alpha")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "mode": (["rgb", "luminance"],),
                "black_point": ("FLOAT", {"default": 0.06, "min": 0.0, "max": 0.5, "step": 0.005}),
                "autocenter": ("BOOLEAN", {"default": True}),
                "feather": ("FLOAT", {"default": 0.12, "min": 0.0, "max": 0.5, "step": 0.005}),
                "size": ("INT", {"default": 512, "min": 64, "max": 2048, "step": 64}),
            },
        }

    def prepare(self, image, mode, black_point, autocenter, feather, size):
        dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        frames = [
            prepare_element(frame[..., :3].to(dtype), mode=mode,
                            black_point=black_point, autocenter=autocenter,
                            feather=feather, size=size)
            for frame in image
        ]
        out = torch.stack(frames, dim=0)
        return (out, luminance(out).clamp(0.0, 1.0))


class FlareElementSave:
    CATEGORY = "flare"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("file", "texture")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "texture": ("IMAGE",),
                "category": ("STRING", {"default": "custom"}),
                "name": ("STRING", {"default": "my_element"}),
                "overwrite": ("BOOLEAN", {"default": False}),
            },
        }

    def save(self, texture, category, name, overwrite):
        cat = _sanitize(category, "custom")
        base = _sanitize(name, "element")
        folder = ELEMENTS_DIR / cat
        folder.mkdir(parents=True, exist_ok=True)

        # every frame of the batch is saved — the forge often generates
        # several variations at once, and dropping all but the first was a
        # silent data loss
        refs = []
        for b in range(texture.shape[0]):
            stem = base if b == 0 else f"{base}_v{b + 1:02d}"
            path = folder / f"{stem}.png"
            if path.exists() and not overwrite:
                i = 2
                while (folder / f"{stem}_{i:02d}.png").exists():
                    i += 1
                path = folder / f"{stem}_{i:02d}.png"

            frame = texture[b][..., :3].clamp(0.0, 1.0)
            arr = (frame.cpu().numpy() * 255).astype(np.uint8)
            Image.fromarray(arr).save(path)
            refs.append(path.relative_to(ELEMENTS_DIR).as_posix())

        return {"ui": {"text": refs}, "result": (refs[0], texture)}


class FlareElementPicker:
    CATEGORY = "flare"
    FUNCTION = "pick"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("file",)

    @classmethod
    def INPUT_TYPES(cls):
        files = list_elements()
        return {"required": {"file": (files or ["<library is empty>"],)}}

    @classmethod
    def IS_CHANGED(cls, file):
        try:
            return str((ELEMENTS_DIR / file).stat().st_mtime)
        except OSError:
            return ""

    def pick(self, file):
        if file == "<library is empty>":
            raise ValueError(
                "the element library is empty; generate and save an element "
                "first (see the element forge workflow)"
            )
        return (file,)
