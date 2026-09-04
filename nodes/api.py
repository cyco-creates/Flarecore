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

    from .library import list_elements

    @routes.get("/flarecore/elements")
    async def flarecore_elements(request):
        return web.json_response({"elements": list_elements()})

    @routes.get("/flarecore/presets")
    async def flarecore_presets(request):
        return web.json_response({"presets": _list_presets()})

    @routes.get("/flarecore/preset/{name}")
    async def flarecore_preset(request):
        path = _find_preset(request.match_info["name"])
        if path is None:
            return web.json_response(
                {"error": f"preset {request.match_info['name']!r} not found"},
                status=404)
        return web.json_response({"name": path.stem,
                                  "json": path.read_text(encoding="utf-8")})

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
            (PRESETS_DIR / filename).write_text(
                json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            return web.json_response({"error": f"could not write preset: {e}"},
                                     status=500)
        return web.json_response({"saved": filename})

    return True


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_")[:64]
