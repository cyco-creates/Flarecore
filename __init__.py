# SPDX-License-Identifier: Apache-2.0
"""comfyui-flarecore: procedural lens flare nodes for ComfyUI."""

try:
    # Normal path: ComfyUI loads this directory as a package, so relative
    # imports work.
    from .nodes.render import FlareRender
    from .nodes.preset import FlarePresetLoader
    from .nodes.depth import FlareDepthAdapter
except ImportError:
    # No package context (pytest collecting the repo root, or a direct
    # import of this file). Re-load ourselves under a proper package name so
    # the relative imports above resolve.
    import importlib.util
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent
    _name = "comfyui_flarecore"
    if _name in sys.modules and hasattr(sys.modules[_name], "FlareRender"):
        _pkg = sys.modules[_name]
    else:
        _spec = importlib.util.spec_from_file_location(
            _name, _root / "__init__.py",
            submodule_search_locations=[str(_root)],
        )
        _pkg = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _pkg
        _spec.loader.exec_module(_pkg)
    FlareRender = _pkg.FlareRender
    FlarePresetLoader = _pkg.FlarePresetLoader
    FlareDepthAdapter = _pkg.FlareDepthAdapter

NODE_CLASS_MAPPINGS = {
    "FlareRender": FlareRender,
    "FlarePresetLoader": FlarePresetLoader,
    "FlareDepthAdapter": FlareDepthAdapter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FlareRender": "Flare Render",
    "FlarePresetLoader": "Flare Preset Loader",
    "FlareDepthAdapter": "Flare Depth Adapter",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
