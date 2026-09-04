# SPDX-License-Identifier: Apache-2.0
"""Preset validation, defaults, and versioning.

Presets are versioned JSON. Validation is strict: unknown element types and
out-of-range values raise ValueError with a message naming the offending key;
unknown extra keys warn but do not fail, so presets from newer minor revisions
degrade gracefully.

Every key has a default. A minimal valid preset is:

    {"schema_version": 1, "elements": [{"type": "glow"}]}
"""

import copy
import json
import math
import warnings

SCHEMA_VERSION = 1

GLOBAL_DEFAULTS = {
    "intensity": 1.0,
    "scale": 1.0,
    "tint": [1.0, 1.0, 1.0],
    "seed": 0,
}

# Keys shared by every element regardless of type.
ELEMENT_COMMON_DEFAULTS = {
    "id": "",               # optional stable identity; seeds derive from it
    "enabled": True,
    "offset": 0.0,          # t along the flare axis (0 = on light, 1 = center)
    "scale": 0.5,           # size in half-frame-heights
    "stretch": [1.0, 1.0],  # per-axis multiplier on scale
    "rotation": 0.0,        # degrees
    "auto_rotate": True,    # add the flare axis angle to rotation
    "intensity": 1.0,
    "color": [1.0, 1.0, 1.0],
    "dispersion": 0.0,
    "dispersion_samples": 3,  # >= 3; 3 = plain R/G/B dispersion
    "count": 1,
    "spread": 0.0,          # t step between duplicated instances
    "count_falloff": 1.0,   # intensity multiplier per instance step
    "count_scale_step": 1.0,  # scale multiplier per instance step
}

# Per-type params defaults (the "params" sub-dict).
PARAM_DEFAULTS = {
    "glow": {"softness": 0.35, "falloff": 1.2},
    "iris": {"blades": 6, "edge_softness": 0.15, "hollow": 0.0},
    "streak": {"length": 0.8, "thickness": 0.02, "count": 1},
    "ring": {"radius": 0.5, "thickness": 0.05},
    "hoop": {"radius": 0.6, "thickness": 0.15, "angular_falloff": 0.8},
    "glint": {"points": 8, "length": 0.5, "thickness": 0.008, "length_jitter": 0.3},
    "spectral": {"shape": "ring", "radius": 0.5, "thickness": 0.08,
                 "blades": 8, "edge_softness": 0.1, "hollow": 0.0},
    "texture": {"file": "", "channel": "auto"},
}

# Per-type overrides of the common element defaults.
ELEMENT_TYPE_OVERRIDES = {
    "spectral": {"dispersion": 1.0, "dispersion_samples": 7},
}

ELEMENT_TYPES = tuple(sorted(PARAM_DEFAULTS.keys()))

_TOP_LEVEL_KEYS = {"schema_version", "name", "author", "global", "elements"}


def _require_number(value, key, lo=None, hi=None, hi_exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"preset key '{key}' must be a number, got {value!r}")
    v = float(value)
    # json.loads accepts bare NaN/Infinity, and every range comparison against
    # NaN is False — without this check a NaN scale poisons the whole render.
    if not math.isfinite(v):
        raise ValueError(f"preset key '{key}' must be finite, got {value!r}")
    if lo is not None and v < lo:
        raise ValueError(f"preset key '{key}' must be >= {lo}, got {v}")
    if hi is not None:
        if hi_exclusive and v >= hi:
            raise ValueError(f"preset key '{key}' must be < {hi}, got {v}")
        if not hi_exclusive and v > hi:
            raise ValueError(f"preset key '{key}' must be <= {hi}, got {v}")
    return v


def _require_int(value, key, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"preset key '{key}' must be an integer, got {value!r}")
    if lo is not None and value < lo:
        raise ValueError(f"preset key '{key}' must be >= {lo}, got {value}")
    if hi is not None and value > hi:
        raise ValueError(f"preset key '{key}' must be <= {hi}, got {value}")
    return value


def _require_vec(value, key, n, lo=None, hi=None):
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise ValueError(f"preset key '{key}' must be a list of {n} numbers, got {value!r}")
    return [_require_number(c, f"{key}[{i}]", lo=lo, hi=hi) for i, c in enumerate(value)]


def normalize_texture_ref(ref, key="params.file") -> str:
    """Shared texture-path guard: forward slashes, and never outside the
    element library. Both the schema and the loader call this one function so
    the security boundary cannot drift between them."""
    if not isinstance(ref, str):
        raise ValueError(f"'{key}' must be a string, got {ref!r}")
    norm = ref.replace("\\", "/")
    parts = norm.split("/")
    if norm.startswith("/") or ".." in parts or (len(norm) > 1 and norm[1] == ":"):
        raise ValueError(
            f"'{key}' must be a relative path inside the element library, got {ref!r}"
        )
    return norm


def _validate_element(raw: dict, index: int) -> dict:
    where = f"elements[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object, got {raw!r}")
    etype = raw.get("type")
    if etype not in PARAM_DEFAULTS:
        raise ValueError(
            f"{where} has unknown element type {etype!r}; "
            f"valid types are: {', '.join(ELEMENT_TYPES)}"
        )

    elem = copy.deepcopy(ELEMENT_COMMON_DEFAULTS)
    elem.update(copy.deepcopy(ELEMENT_TYPE_OVERRIDES.get(etype, {})))
    elem["type"] = etype
    elem["params"] = copy.deepcopy(PARAM_DEFAULTS[etype])

    known = set(elem.keys()) | {"type", "params"}
    for key, value in raw.items():
        if key in ("type",):
            continue
        if key == "params":
            if not isinstance(value, dict):
                raise ValueError(f"{where}.params must be an object")
            for pkey, pval in value.items():
                if pkey not in PARAM_DEFAULTS[etype]:
                    warnings.warn(
                        f"{where}.params has unknown key '{pkey}' for type "
                        f"'{etype}' (ignored)"
                    )
                    continue
                elem["params"][pkey] = pval
            continue
        if key not in known:
            warnings.warn(f"{where} has unknown key '{key}' (ignored)")
            continue
        elem[key] = value

    # Validate common keys. Upper bounds exist because preset_json is a free
    # text field: an unbounded count or dispersion is a denial of service or a
    # coordinate-scale sign flip, not a creative choice.
    elem["id"] = str(elem["id"])
    elem["enabled"] = bool(elem["enabled"])
    elem["offset"] = _require_number(elem["offset"], f"{where}.offset", lo=-10.0, hi=10.0)
    elem["scale"] = _require_number(elem["scale"], f"{where}.scale", lo=1e-6, hi=100.0)
    # colour is linear-light and may exceed 1 (HDR), but never go negative:
    # a negative flare darkens the plate and falsifies the alpha mask
    elem["stretch"] = _require_vec(elem["stretch"], f"{where}.stretch", 2)
    if elem["stretch"][0] <= 0 or elem["stretch"][1] <= 0:
        raise ValueError(f"{where}.stretch components must be > 0")
    elem["rotation"] = _require_number(elem["rotation"], f"{where}.rotation")
    elem["auto_rotate"] = bool(elem["auto_rotate"])
    elem["intensity"] = _require_number(elem["intensity"], f"{where}.intensity",
                                        lo=0.0, hi=1000.0)
    elem["color"] = _require_vec(elem["color"], f"{where}.color", 3, lo=0.0, hi=100.0)
    # dispersion scales coordinates by 1 - d*0.05*w; past 8 the look is junk
    # and at 20 the red sample collapses to a full-frame constant
    elem["dispersion"] = _require_number(elem["dispersion"], f"{where}.dispersion",
                                         lo=0.0, hi=8.0)
    elem["dispersion_samples"] = _require_int(
        elem["dispersion_samples"], f"{where}.dispersion_samples", lo=3, hi=33
    )
    elem["count"] = _require_int(elem["count"], f"{where}.count", lo=1, hi=64)
    elem["spread"] = _require_number(elem["spread"], f"{where}.spread", lo=-10.0, hi=10.0)
    elem["count_falloff"] = _require_number(elem["count_falloff"], f"{where}.count_falloff",
                                            lo=0.0, hi=10.0)
    elem["count_scale_step"] = _require_number(
        elem["count_scale_step"], f"{where}.count_scale_step", lo=0.05, hi=10.0
    )

    # Validate per-type params.
    p = elem["params"]
    if etype == "glow":
        p["softness"] = _require_number(p["softness"], f"{where}.params.softness", lo=1e-4)
        p["falloff"] = _require_number(p["falloff"], f"{where}.params.falloff", lo=0.05)
    elif etype == "iris":
        p["blades"] = _require_int(p["blades"], f"{where}.params.blades", lo=3)
        p["edge_softness"] = _require_number(
            p["edge_softness"], f"{where}.params.edge_softness", lo=0.0, hi=1.0
        )
        p["hollow"] = _require_number(p["hollow"], f"{where}.params.hollow",
                                      lo=0.0, hi=1.0, hi_exclusive=True)
    elif etype == "streak":
        p["length"] = _require_number(p["length"], f"{where}.params.length", lo=1e-4)
        p["thickness"] = _require_number(p["thickness"], f"{where}.params.thickness", lo=1e-5)
        p["count"] = _require_int(p["count"], f"{where}.params.count", lo=1, hi=32)
    elif etype == "ring":
        p["radius"] = _require_number(p["radius"], f"{where}.params.radius", lo=0.0)
        p["thickness"] = _require_number(p["thickness"], f"{where}.params.thickness", lo=1e-5)
    elif etype == "hoop":
        p["radius"] = _require_number(p["radius"], f"{where}.params.radius", lo=0.0)
        p["thickness"] = _require_number(p["thickness"], f"{where}.params.thickness", lo=1e-5)
        p["angular_falloff"] = _require_number(
            p["angular_falloff"], f"{where}.params.angular_falloff", lo=0.0, hi=1.0
        )
    elif etype == "glint":
        p["points"] = _require_int(p["points"], f"{where}.params.points", lo=2, hi=256)
        p["length"] = _require_number(p["length"], f"{where}.params.length", lo=1e-4)
        p["thickness"] = _require_number(p["thickness"], f"{where}.params.thickness", lo=1e-5)
        p["length_jitter"] = _require_number(
            p["length_jitter"], f"{where}.params.length_jitter", lo=0.0, hi=1.0
        )
    elif etype == "texture":
        p["file"] = normalize_texture_ref(p["file"], f"{where}.params.file")
        if p["channel"] not in ("auto", "rgb", "luminance"):
            raise ValueError(
                f"{where}.params.channel must be 'auto', 'rgb' or "
                f"'luminance', got {p['channel']!r}"
            )
    elif etype == "spectral":
        if p["shape"] not in ("ring", "iris"):
            raise ValueError(f"{where}.params.shape must be 'ring' or 'iris', got {p['shape']!r}")
        p["radius"] = _require_number(p["radius"], f"{where}.params.radius", lo=0.0)
        p["thickness"] = _require_number(p["thickness"], f"{where}.params.thickness", lo=1e-5)
        p["blades"] = _require_int(p["blades"], f"{where}.params.blades", lo=3)
        p["edge_softness"] = _require_number(
            p["edge_softness"], f"{where}.params.edge_softness", lo=0.0, hi=1.0
        )
        p["hollow"] = _require_number(p["hollow"], f"{where}.params.hollow",
                                      lo=0.0, hi=1.0, hi_exclusive=True)

    return elem


def validate_preset(raw: dict) -> dict:
    """Validate a parsed preset dict and return a fully-defaulted copy.

    Raises ValueError with a specific message on structural problems; warns on
    unknown keys.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"preset must be a JSON object, got {type(raw).__name__}")

    if "schema_version" not in raw:
        raise ValueError("preset is missing required key 'schema_version'")
    version = raw["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"schema_version must be an integer, got {version!r}")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {version}; this build supports version "
            f"{SCHEMA_VERSION}"
        )

    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            warnings.warn(f"preset has unknown top-level key '{key}' (ignored)")

    if "elements" not in raw:
        raise ValueError("preset is missing required key 'elements'")
    if not isinstance(raw["elements"], list):
        raise ValueError("preset key 'elements' must be a list")

    out = {
        "schema_version": SCHEMA_VERSION,
        "name": str(raw.get("name", "")),
        "author": str(raw.get("author", "")),
        "global": copy.deepcopy(GLOBAL_DEFAULTS),
        "elements": [],
    }

    raw_global = raw.get("global", {})
    if not isinstance(raw_global, dict):
        raise ValueError("preset key 'global' must be an object")
    for key, value in raw_global.items():
        if key not in GLOBAL_DEFAULTS:
            warnings.warn(f"preset global has unknown key '{key}' (ignored)")
            continue
        out["global"][key] = value
    g = out["global"]
    g["intensity"] = _require_number(g["intensity"], "global.intensity", lo=0.0, hi=1000.0)
    g["scale"] = _require_number(g["scale"], "global.scale", lo=1e-6, hi=100.0)
    g["tint"] = _require_vec(g["tint"], "global.tint", 3, lo=0.0, hi=100.0)
    g["seed"] = _require_int(g["seed"], "global.seed")

    for i, raw_elem in enumerate(raw["elements"]):
        out["elements"].append(_validate_element(raw_elem, i))

    return out


def load_preset(text: str) -> dict:
    """Parse a JSON preset string and validate it."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"preset is not valid JSON: {e}") from e
    return validate_preset(raw)
