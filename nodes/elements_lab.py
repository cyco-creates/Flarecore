# SPDX-License-Identifier: Apache-2.0
"""Nodes for making custom flare element textures.

The pipeline: FlareElementPrompts hands an editable, categorised prompt to
whatever image model the user runs (the shipped workflow uses their local
Krea2), FlareTexturePrepare turns the generated picture into a compositing-
safe element texture, and FlareElementSave files it in the element library.
The engine itself never generates anything.
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

# Style tails appended to a bank prompt: a lens era, a capture medium, the
# state of the front element, the weather on set, what is actually lighting
# the thing, what is screwed onto the matte box. Grouped the way the
# preset library is, so the dropdown reads as a shelf.
STYLES_FILE = Path(__file__).resolve().parents[1] / "prompts" / "element_styles.json"

# Categories whose elements sit ON the front element rather than being a
# shape in the image: they have to cover the frame, so they are prepared
# wide and un-feathered. `frame: auto` reads the category and picks.
LENS_PLATE_CATEGORIES = {"lens_dirt"}

# The generator has to make a plate at the shape it will be used at. Prepare
# can cover-crop a square generation into 16:9 without distorting it, but
# cropping throws away nearly half of what the sampler just made, so the
# shape belongs upstream of the sampler. These drive the latent through the
# prompt node's gen_width/gen_height outputs; leave them unwired and nothing
# changes. 1536x864 is exactly 16:9 with both sides a multiple of 16.
GEN_SIZE_SQUARE = (1328, 1328)
GEN_SIZE_WIDE = (1536, 864)


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


def _load_style_bank() -> dict:
    """Grouped style tails, or an empty dict if the file is unusable — the
    forge still works without them, the tail is just typed by hand."""
    try:
        with open(STYLES_FILE, encoding="utf-8") as f:
            bank = json.load(f)
        if isinstance(bank, dict):
            return {
                str(group): {str(k): str(v) for k, v in entries.items()}
                for group, entries in bank.items() if isinstance(entries, dict)
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
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("prompt", "category", "element_name",
                    "gen_width", "gen_height")

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
        wide = str(category).strip().lower() in LENS_PLATE_CATEGORIES
        gen_w, gen_h = GEN_SIZE_WIDE if wide else GEN_SIZE_SQUARE
        return (prompt, category or "custom", name or "element", gen_w, gen_h)


class FlareGeneratorSelect:
    """Choose which of two generator branches feeds the forge.

    The inputs are LAZY: only the branch the switch names is ever
    evaluated, so a hosted API generator sitting on the unselected input
    costs nothing while the local one is active, and the local one never
    warms the card while the API one is chosen. The editor mirrors the
    choice by muting whatever feeds only the other input, so the disabled
    branch also LOOKS disabled.
    """

    CATEGORY = "flare"
    FUNCTION = "select"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generator": (["a", "b"], {
                    "tooltip": "which input feeds through. Only the chosen "
                               "branch is executed at all; the other is "
                               "never run, whatever it costs.",
                }),
            },
            "optional": {
                "image_a": ("IMAGE", {"lazy": True}),
                "image_b": ("IMAGE", {"lazy": True}),
            },
        }

    def check_lazy_status(self, generator, image_a=None, image_b=None):
        return ["image_a" if generator == "a" else "image_b"]

    def select(self, generator, image_a=None, image_b=None):
        chosen = image_a if generator == "a" else image_b
        if chosen is None:
            port = "image_a" if generator == "a" else "image_b"
            raise ValueError(
                f"generator '{generator}' is selected but nothing is wired "
                f"into {port}; connect that branch or flip the switch. If "
                f"the branch IS wired, it may be muted -- un-mute it or "
                f"reload the page so the switch can re-sync."
            )
        return (chosen,)


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
                "size": ("INT", {"default": 2048, "min": 64, "max": 4096, "step": 64}),
                # appended after size (widgets_values is positional)
                "margin": ("FLOAT", {
                    "default": 0.25, "min": 0.0, "max": 0.45, "step": 0.01,
                    "tooltip": "Black breathing room on every side; 0.25 keeps "
                               "the content in the middle half so it never crops.",
                }),
                "frame": (["auto", "square", "wide_16_9"], {
                    "tooltip": "auto follows the category: lens_dirt is "
                               "prepared as a full-frame 16:9 plate (no crop, "
                               "no centring, no feather, no margin), anything "
                               "else as a square element on black. Connect "
                               "the prompt node's category output for auto to "
                               "see it.",
                }),
            },
            "optional": {
                "category": ("STRING", {
                    "forceInput": True,
                    "tooltip": "from the prompt node; only used by frame=auto",
                }),
            },
        }

    def prepare(self, image, mode, black_point, autocenter, feather, size,
                margin=0.25, frame="square", category=""):
        if frame == "auto":
            # a lens plate covers the whole front element; everything else is
            # a shape on black that needs room to breathe
            frame = ("wide_16_9"
                     if str(category).strip().lower() in LENS_PLATE_CATEGORIES
                     else "square")
        dtype = image.dtype if image.dtype.is_floating_point else torch.float32
        frames = [
            prepare_element(f[..., :3].to(dtype), mode=mode,
                            black_point=black_point, autocenter=autocenter,
                            feather=feather, size=size, margin=margin,
                            frame=frame)
            for f in image
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
