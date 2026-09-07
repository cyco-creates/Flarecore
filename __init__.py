# SPDX-License-Identifier: Apache-2.0
"""comfyui-flarecore: procedural lens flare nodes for ComfyUI.

Two import contexts exist. ComfyUI loads this directory as a real package,
so the relative imports work. Tests (and a bare ``import __init__``) load it
without package context — that case is detected EXPLICITLY via __package__,
never by catching ImportError: an except-ImportError fallback swallowed
missing dependencies (no torch, no Pillow) and re-executed the package in a
loop until RecursionError, burying the actual cause.
"""

if __package__:
    from .nodes import (
        FlareRender,
        FlarePresetLoader,
        FlareDepthAdapter,
        FlareKeyframes,
        FlareElementPrompts,
        FlareTexturePrepare,
        FlareElementSave,
        FlareGeneratorSelect,
    )
    from .nodes.api import register_routes
else:
    # No package context: re-load this file once under a proper package name
    # so the relative imports above resolve. Dependency failures inside that
    # exec propagate unchanged — a missing torch reports as missing torch.
    import importlib.util
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent
    _name = "comfyui_flarecore"
    if _name in sys.modules:
        _pkg = sys.modules[_name]
    else:
        _spec = importlib.util.spec_from_file_location(
            _name, _root / "__init__.py",
            submodule_search_locations=[str(_root)],
        )
        _pkg = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _pkg
        try:
            _spec.loader.exec_module(_pkg)
        except BaseException:
            # never leave a half-initialized module behind
            sys.modules.pop(_name, None)
            raise
    FlareRender = _pkg.FlareRender
    FlarePresetLoader = _pkg.FlarePresetLoader
    FlareDepthAdapter = _pkg.FlareDepthAdapter
    FlareKeyframes = _pkg.FlareKeyframes
    FlareElementPrompts = _pkg.FlareElementPrompts
    FlareTexturePrepare = _pkg.FlareTexturePrepare
    FlareElementSave = _pkg.FlareElementSave
    FlareGeneratorSelect = _pkg.FlareGeneratorSelect
    register_routes = _pkg.register_routes

# Editor and point-picker widgets.
WEB_DIRECTORY = "./web"

# Same-origin helper routes for the editor; idempotent, a no-op outside
# ComfyUI.
register_routes()

NODE_CLASS_MAPPINGS = {
    "FlareRender": FlareRender,
    "FlarePresetLoader": FlarePresetLoader,
    "FlareDepthAdapter": FlareDepthAdapter,
    "FlareKeyframes": FlareKeyframes,
    "FlareElementPrompts": FlareElementPrompts,
    "FlareTexturePrepare": FlareTexturePrepare,
    "FlareElementSave": FlareElementSave,
    "FlareGeneratorSelect": FlareGeneratorSelect,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlareRender": "Flarecore · Render",
    "FlarePresetLoader": "Flarecore · Preset Loader",
    "FlareDepthAdapter": "Flarecore · Depth Adapter",
    "FlareKeyframes": "Flarecore · Keyframes",
    "FlareElementPrompts": "Flarecore · Element Forge",
    "FlareTexturePrepare": "Flarecore · Prepare Texture",
    "FlareElementSave": "Flarecore · Save Element",
    "FlareGeneratorSelect": "Flarecore · Generator Select",
}

# One discoverable family; serialized node IDs remain unchanged.
for _node_class in NODE_CLASS_MAPPINGS.values():
    _node_class.CATEGORY = "Flarecore"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
