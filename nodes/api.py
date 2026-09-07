# SPDX-License-Identifier: Apache-2.0
"""Local HTTP routes backing the flare editor UI.

Registered only when ComfyUI's server is importable; everything degrades
gracefully in library/test contexts. All routes are same-origin helpers for
the front-end widgets — nothing here talks to the network.
"""

import json
import re
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parents[1]
PRESETS_DIR = PACK_DIR / "presets"

# The names of the presets that ship with the pack, frozen at import time;
# /flarecore/save_preset refuses to overwrite them.
_SHIPPED = {p.name for p in PRESETS_DIR.glob("*.json")}

_registered = False


def _list_presets() -> list[str]:
    return sorted(p.name for p in PRESETS_DIR.glob("*.json"))


def _preset_index() -> list[dict]:
    """Every preset with the category it files itself under, so the editor
    can group the menu without opening each file. A preset that names no
    category simply lands in a trailing group."""
    out = []
    for path in sorted(PRESETS_DIR.glob("*.json")):
        entry = {"name": path.stem, "title": "", "category": "",
                 "subcategory": ""}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry["title"] = str(data.get("name", ""))
            entry["category"] = str(data.get("category", ""))
            entry["subcategory"] = str(data.get("subcategory", ""))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass          # a broken preset still deserves a menu entry
        out.append(entry)
    return out


def _find_preset(name: str):
    """Resolve a requested preset by its listed name — the same names the
    listing route returns, so anything advertised is loadable."""
    if "/" in name or "\\" in name or ".." in name:
        return None
    for candidate in (f"{name}.json", name):
        path = PRESETS_DIR / candidate
        if path.is_file() and path.parent == PRESETS_DIR:
            return path
    return None


_PREVIEW_CACHE: dict = {}
PREVIEW_W, PREVIEW_H = 256, 144


def _render_element_png(elem: dict, glob: dict) -> bytes:
    """Solo-render one element at preview size and encode it as PNG."""
    e = dict(elem)
    e["enabled"] = True
    e["solo"] = False
    return _render_preset_png(json.dumps({"schema_version": 1, "global": glob,
                                          "elements": [e]}), PREVIEW_W, PREVIEW_H)


def _render_preset_png(text: str, width: int = 512, height: int = 288) -> bytes:
    """Render the actual full preset, including library textures, on black."""
    import io as _io
    import numpy as np
    import torch
    from PIL import Image
    from ..flare.schema import load_preset
    from ..flare.engine import render_stack
    from ..flare.grid import uv_to_grid
    from ..flare.colorspace import linear_to_srgb

    from .library import resolve_preset_textures
    preset = load_preset(text)
    resolve_preset_textures(preset, device=torch.device("cpu"), dtype=torch.float32)
    h, w = height, width
    # the light a third of the way in, the anchor past centre: enough axis
    # for a ghost chain to read, without the flare leaving the picture
    x, y = uv_to_grid(0.32, 0.42, h, w)
    ax, ay = uv_to_grid(0.64, 0.56, h, w)
    lights = [{"x": x, "y": y, "ax": ax, "ay": ay, "u": 0.32, "v": 0.42,
               "brightness": 1.0, "occlusion": 0.0, "index": 0}]
    with torch.no_grad():
        out = render_stack(preset, lights, h, w, torch.device("cpu"),
                           torch.float32)
        rgb = linear_to_srgb(out.clamp(0.0, 1.0))
    arr = (rgb.numpy() * 255.0 + 0.5).astype(np.uint8)
    buf = _io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def register_routes() -> bool:
    global _registered
    if _registered:
        return True
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return False
    _registered = True

    from .library import list_elements, ELEMENTS_DIR
    from ..flare.schema import normalize_texture_ref
    import asyncio
    preview_slots = asyncio.Semaphore(2)

    @routes.get("/flarecore/preset_preview/{name}")
    async def flarecore_preset_preview(request):
        path = _find_preset(request.match_info["name"])
        if path is None:
            return web.json_response({"error": "preset not found"}, status=404)
        try:
            text = path.read_text(encoding="utf-8")
            # Editing a referenced texture must also invalidate its preview.
            stamp = tuple((str(p.relative_to(ELEMENTS_DIR)), p.stat().st_mtime_ns)
                          for p in sorted(ELEMENTS_DIR.rglob("*.png")))
            key = (text, stamp)
            async with preview_slots:
                png = _PREVIEW_CACHE.get(key)
                if png is None:
                    png = await asyncio.to_thread(_render_preset_png, text)
                    if len(_PREVIEW_CACHE) >= 256:
                        _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))
                    _PREVIEW_CACHE[key] = png
            return web.Response(body=png, content_type="image/png",
                                headers={"Cache-Control": "no-cache"})
        except Exception:
            return web.json_response({"error": "Preview unavailable; check preset and texture files."}, status=400)

    @routes.get("/flarecore/motion_schema")
    async def motion_schema(request):
        from ..flare.motion import MOTION_TARGETS, MOTION_DRIVERS
        return web.json_response({"targets": MOTION_TARGETS, "drivers": MOTION_DRIVERS})

    # A rendered preview of ONE element as configured -- colour, scale,
    # count, blur, the lot -- for the editor's hover. Procedural elements
    # have no file to show, and a texture's file is not what it looks like
    # once it is tinted and scaled; a solo render is the truth. Small, on the
    # CPU so it never contends with a real render, and cached by content.
    @routes.post("/flarecore/element_preview")
    async def flarecore_element_preview(request):
        import hashlib
        try:
            body = await request.json()
            elem = body.get("element")
            glob = body.get("global") or {}
            if not isinstance(elem, dict):
                raise ValueError("no element")
            key = hashlib.sha1(json.dumps([elem, glob], sort_keys=True,
                                          default=str).encode()).hexdigest()
            png = _PREVIEW_CACHE.get(key)
            if png is None:
                png = _render_element_png(elem, glob)
                if len(_PREVIEW_CACHE) >= 256:
                    _PREVIEW_CACHE.pop(next(iter(_PREVIEW_CACHE)))
                _PREVIEW_CACHE[key] = png
            return web.Response(body=png, content_type="image/png",
                                headers={"Cache-Control": "no-store"})
        except Exception as e:  # a preview must never break the editor
            return web.json_response({"error": str(e)}, status=400)

    @routes.get("/flarecore/elements")
    async def flarecore_elements(request):
        return web.json_response({"elements": list_elements()})

    @routes.get("/flarecore/prompt_bank")
    async def flarecore_prompt_bank(request):
        # the forge panel's category/element dropdowns and editable prompt,
        # plus the grouped style tails its style picker offers
        from .elements_lab import _load_prompt_bank, _load_style_bank
        return web.json_response({"bank": _load_prompt_bank(),
                                  "styles": _load_style_bank()})

    @routes.get("/flarecore/element/{ref:.*}")
    async def flarecore_element(request):
        """Serve one library texture so the editor gallery can show thumbnails."""
        try:
            ref = normalize_texture_ref(request.match_info["ref"])
        except ValueError:
            return web.json_response({"error": "bad reference"}, status=400)
        path = ELEMENTS_DIR / ref
        if not path.is_file() or path.suffix.lower() != ".png":
            return web.json_response({"error": "not found"}, status=404)
        return web.FileResponse(path)

    @routes.get("/flarecore/presets")
    async def flarecore_presets(request):
        # `presets` stays a plain name list for anything already reading it;
        # `index` carries the grouping.
        return web.json_response({"presets": _list_presets(),
                                  "index": _preset_index()})

    @routes.get("/flarecore/preset/{name}")
    async def flarecore_preset(request):
        path = _find_preset(request.match_info["name"])
        if path is None:
            return web.json_response(
                {"error": f"preset {request.match_info['name']!r} not found"},
                status=404)
        try:
            text = path.read_text(encoding="utf-8")
            from ..flare.schema import load_preset
            load_preset(text)
        except (OSError, ValueError, TypeError):
            return web.json_response({"error": "Preset is invalid or unreadable."}, status=400)
        return web.json_response({"name": path.stem, "json": text})

    @routes.post("/flarecore/save_preset")
    async def flarecore_save_preset(request):
        try:
            body = await request.json()
            name = _safe_name(str(body.get("name", "")))
            text = str(body.get("json", ""))
            overwrite = bool(body.get("overwrite", False))
            if not name:
                raise ValueError("preset name is empty")
            # Validate before writing so the library never gains a broken file.
            from ..flare.schema import load_preset
            load_preset(text)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        filename = f"{name}.json"
        if filename in _SHIPPED and not overwrite:
            return web.json_response(
                {"error": f"'{name}' is a shipped preset; pick another name "
                          f"or pass overwrite", "shipped": True}, status=409)
        if (PRESETS_DIR / filename).exists() and not overwrite:
            return web.json_response(
                {"error": f"'{name}' already exists; pass overwrite to replace it",
                 "exists": True}, status=409)

        try:
            parsed = json.loads(text)
            parsed["preset_file"] = name
            (PRESETS_DIR / filename).write_text(
                json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            return web.json_response({"error": f"could not write preset: {e}"},
                                     status=500)
        return web.json_response({"saved": filename})

    return True


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_")[:64]
