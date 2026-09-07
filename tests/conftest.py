# SPDX-License-Identifier: Apache-2.0
"""Shared test scaffolding.

load_package() mirrors how ComfyUI actually loads the pack (importlib with a
package name), and lives here once — a copy that drifts in a hand-run script
dies with an opaque importlib error precisely when it is needed.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_package():
    """Load the repo as a package the way ComfyUI does."""
    if "comfyui_flarecore" in sys.modules:
        return sys.modules["comfyui_flarecore"]
    spec = importlib.util.spec_from_file_location(
        "comfyui_flarecore", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["comfyui_flarecore"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


def argmax_uv(field_hw):
    """UV position of a (H, W) field's brightest pixel."""
    idx = field_hw.flatten().argmax().item()
    h, w = field_hw.shape
    row, col = divmod(idx, w)
    return (col + 0.5) / w, (row + 0.5) / h
