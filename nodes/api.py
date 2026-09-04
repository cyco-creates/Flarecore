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


def register_routes() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return False

    from .library import list_elements

    @routes.get("/flarecore/elements")
    async def flarecore_elements(request):
        return web.json_response({"elements": list_elements()})

    @routes.get("/flarecore/presets")
    async def flarecore_presets(request):
        files = sorted(p.name for p in PRESETS_DIR.glob("*.json"))
        return web.json_response({"presets": files})

    @routes.get("/flarecore/preset/{name}")
    async def flarecore_preset(request, ):
        name = _safe_name(request.match_info["name"])
        path = PRESETS_DIR / f"{name}.json"
        if not path.is_file():
            return web.json_response({"error": f"preset '{name}' not found"}, status=404)
        return web.json_response({"name": name, "json": path.read_text(encoding="utf-8")})

    @routes.post("/flarecore/save_preset")
    async def flarecore_save_preset(request):
        try:
            body = await request.json()
            name = _safe_name(str(body.get("name", "")))
            text = str(body.get("json", ""))
            if not name:
                raise ValueError("preset name is empty")
            # Validate before writing so the library never gains a broken file.
            from ..flare.schema import load_preset
            load_preset(text)
            path = PRESETS_DIR / f"{name}.json"
            parsed = json.loads(text)
            path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
            return web.json_response({"saved": path.name})
        except Exception as e:  # surfaced in the editor as a toast
            return web.json_response({"error": str(e)}, status=400)

    return True


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_")[:64]
