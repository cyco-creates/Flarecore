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

from .motion import MOTION_TARGETS, MOTION_DRIVERS

SCHEMA_VERSION = 1

GLOBAL_DEFAULTS = {
    "intensity": 1.0,
    "scale": 1.0,
    "aspect": 1.0,          # >1 widens every element (anamorphic squeeze look)
    "tint": [1.0, 1.0, 1.0],
    "seed": 0,
    "fringe": 0.0,          # 0..1 radial chromatic fringe on the whole flare
    "flicker_amount": 0.0,  # 0..1 per-light brightness flicker over frames
    "flicker_speed": 1.0,   # flicker rate multiplier (frames)
    "edge_fade_start": 0.0,  # half-heights outside the frame where fade begins
    "edge_fade_range": 0.0,  # fade length; 0 disables (lens-hood behaviour)
}

# Rule-based modulation without keyframes: as the light (or this element)
# nears the frame border or centre, brightness/scale/colour shift.
TRIGGER_DEFAULTS = {
    "mode": "none",         # none | border | center | light
    "source": "light",      # light | element (which position drives it;
                            # ignored by mode "light", which always measures
                            # the element's distance to the light)
    "inner": 0.0,           # distance (half-heights) where the rule is fully on
    "outer": 0.3,           # distance where it is fully off
    "falloff": "smooth",    # linear | smooth | exponential
    "brightness": 0.0,      # intensity ADDED at full trigger (can be negative)
    "scale": 0.0,           # added scale multiplier at full trigger
    "rotation": 0.0,        # degrees ADDED at full trigger
    "color": [1.0, 1.0, 1.0],  # tint at full trigger
}

# Keys shared by every element regardless of type.
ELEMENT_COMMON_DEFAULTS = {
    "id": "",               # optional stable identity; seeds derive from it
    "label": "",            # optional display name (editor UI); engine ignores
    "slot": "",             # optional element family (editor gallery); ignored
    "enabled": True,
    "solo": False,          # when any element is soloed, only soloed ones render
    "offset": 0.0,          # t along the flare axis (0 = on light, 1 = center)
    "scale": 0.5,           # size in half-frame-heights
    "stretch": [1.0, 1.0],  # per-axis multiplier on scale
    "move": [1.0, 1.0],     # how much of the light-driven motion this element
                            # follows per screen axis: [1, 0] slides only
                            # horizontally (anamorphic elements)
    "shift": [0.0, 0.0],    # screen-space offset in half-heights applied
                            # AFTER the axis position, so it does not swing
                            # with the flare angle the way `offset` does: a
                            # companion ghost's own streak sits a fixed
                            # distance below the source
    "pin": [None, None],    # lock a coordinate to the frame in grid units
                            # (y -1 top, +1 bottom); the other axis keeps
                            # following the light, which is the hotspot
                            # welded to the frame edge above the source.
                            # null leaves that axis free
    "shade": 0.0,           # -1..1 linear brightness ramp across the
                            # element's own axis: a ghost lit only on the
                            # edge facing the source reads as a comet, and
                            # as a cusped crescent once the frame cuts it
    "rotation": 0.0,        # degrees
    "auto_rotate": True,    # add the flare axis angle to rotation
    "intensity": 1.0,
    "color": [1.0, 1.0, 1.0],
    "blur": 0.0,            # 0..1 gaussian soften of the rendered element
    "irregular": 0.0,       # 0..1 seeded organic unevenness in the shape math
    "light_mask": 0.0,      # 0..1: modulate by scene brightness
    "mask_scene": 1.0,      # 0..1: how much the SCENE's brightness reveals
                            # this element, as opposed to the light's own
                            # pool. Scene luminance changes every frame, so
                            # a lens plate masked by it appears to crawl
                            # while the plate itself never moves; 0 pins the
                            # reveal to the light alone
    "mask_floor": 0.0,      # 0..0.95: hide the element wherever the light's
                            # pool is dimmer than this. The mask is
                            # max(scene luminance, the light's glow), and
                            # neither term ever reaches zero -- a real plate
                            # is never pure black and a gaussian has no
                            # edge -- so without a floor lens dirt stays
                            # faintly visible across the whole frame
    "fill_frame": False,    # map this element across the whole frame, at the
                            # frame's own aspect: a lens-surface plate covers
                            # the front element, it is not a shape on black
    "screen_space": False,  # lock to the lens (frame), not the flare axis —
                            # rendered once per frame, scaled by the
                            # brightest light; lens-dirt behaviour
    "dispersion": 0.0,
    "dispersion_samples": 3,  # >= 3; 3 = plain R/G/B dispersion
    "count": 1,
    "spread": 0.0,          # t step between duplicated instances
    "count_falloff": 1.0,   # intensity multiplier per instance step
    "count_scale_step": 1.0,  # scale multiplier per instance step
    "trigger": None,        # see TRIGGER_DEFAULTS
    "motion": None,         # independent source-driven response curves
    "screen_blend": "strongest",  # opt-in all-light response for lens plates
}

# Angular window shared by the circular element types: completion in degrees
# (360 = whole circle) and a feathered fade at the window's ends.
_COMPLETION_DEFAULTS = {"completion": 360.0, "completion_feather": 0.2}

# The barrel cutting a ghost into a crescent: a clipping disc of the
# element's own radius slides across it, so 0 leaves the ghost whole and
# 0.9 leaves a thin arc. Shared by the round element types.
_CRESCENT_DEFAULTS = {"crescent": 0.0, "crescent_feather": 0.1}

# Per-type params defaults (the "params" sub-dict).
PARAM_DEFAULTS = {
    "glow": {"softness": 0.35, "falloff": 1.2},
    "iris": {"blades": 6, "edge_softness": 0.15, "hollow": 0.0, "roundness": 0.0,
             **_CRESCENT_DEFAULTS},
    "streak": {"length": 0.8, "thickness": 0.02, "count": 1,
               # a shallow arc instead of a rule, and gaps along it
               "curve": 0.0, "dash": 0.0},
    "ring": {"radius": 0.5, "thickness": 0.05, **_COMPLETION_DEFAULTS,
             **_CRESCENT_DEFAULTS},
    "hoop": {"radius": 0.6, "thickness": 0.15, "angular_falloff": 0.8,
             **_COMPLETION_DEFAULTS, **_CRESCENT_DEFAULTS},
    "glint": {"points": 8, "length": 0.5, "thickness": 0.008, "length_jitter": 0.3,
              **_COMPLETION_DEFAULTS},
    "spectral": {"shape": "ring", "radius": 0.5, "thickness": 0.08,
                 "blades": 8, "edge_softness": 0.1, "hollow": 0.0, "roundness": 0.0,
                 **_COMPLETION_DEFAULTS, **_CRESCENT_DEFAULTS},
    "texture": {"file": "", "channel": "auto"},
    # procedural out-of-focus spots on the lens, lit by proximity to the light
    "orbs": {"count": 24, "size": 0.12, "size_jitter": 0.6, "spread": 1.0,
             "edge_softness": 0.3, "illumination": 0.8, "shape": "disc",
             "blades": 6,
             # gather the specks on an annulus instead of filling the disc,
             # and let each diffract its own colour: the dusty rim of a
             # front-element reflection
             "ring": 0.0, "ring_width": 0.3, "spectral": 0.0},
}

# Per-type overrides of the common element defaults.
ELEMENT_TYPE_OVERRIDES = {
    "spectral": {"dispersion": 1.0, "dispersion_samples": 7},
    "orbs": {"screen_space": True, "auto_rotate": False, "scale": 1.0,
             "intensity": 0.5},
}

ELEMENT_TYPES = tuple(sorted(PARAM_DEFAULTS.keys()))

_TOP_LEVEL_KEYS = {"schema_version", "name", "author", "category",
                   "subcategory", "global", "elements"}

# How a preset files itself in the library. The editor's preset menu groups
# by these, so a browsable library is a property of the presets themselves
# rather than of their filenames -- renaming a preset would break every
# saved workflow that loads it by name.
PRESET_CATEGORIES = ("Anamorphic", "Spherical", "Scenario", "Utility")

# The eight families every element belongs to. A preset element's `slot`
# says which of them holds alternatives for it, so clicking its name in the
# editor opens the right shelf of the library. Folder names under
# elements/, the prompt bank's top-level keys and the editor's CATEGORY_OF
# targets are all this same list; tests/test_library.py pins them together.
ELEMENT_SLOTS = ("glows", "ghosts", "rays", "streaks", "rings", "hoops",
                 "caustics", "lens_dirt")


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


def _require_pin(value, key):
    """[x, y] in grid units where either entry may be null to leave that
    axis following the light."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(
            f"preset key '{key}' must be a list of 2 numbers or nulls, got {value!r}"
        )
    return [None if c is None else _require_number(c, f"{key}[{i}]", lo=-10.0, hi=10.0)
            for i, c in enumerate(value)]


def _validate_trigger(raw, where: str):
    """Fill and range-check an element's trigger block; None/absent -> None
    (no rule), which the engine treats as 'mode: none'."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{where}.trigger must be an object or null")
    t = copy.deepcopy(TRIGGER_DEFAULTS)
    for key, value in raw.items():
        if key not in TRIGGER_DEFAULTS:
            warnings.warn(f"{where}.trigger has unknown key '{key}' (ignored)")
            continue
        t[key] = value
    if t["mode"] not in ("none", "border", "center", "light"):
        raise ValueError(
            f"{where}.trigger.mode must be none, border, center or light")
    if t["source"] not in ("light", "element"):
        raise ValueError(f"{where}.trigger.source must be light or element")
    if t["falloff"] not in ("linear", "smooth", "exponential"):
        raise ValueError(f"{where}.trigger.falloff must be linear, smooth or exponential")
    t["inner"] = _require_number(t["inner"], f"{where}.trigger.inner", lo=-2.0, hi=4.0)
    t["outer"] = _require_number(t["outer"], f"{where}.trigger.outer", lo=-2.0, hi=4.0)
    if t["outer"] <= t["inner"]:
        t["outer"] = t["inner"] + 1e-3
    t["brightness"] = _require_number(t["brightness"], f"{where}.trigger.brightness",
                                      lo=-20.0, hi=20.0)
    t["scale"] = _require_number(t["scale"], f"{where}.trigger.scale", lo=-0.95, hi=20.0)
    t["rotation"] = _require_number(t["rotation"], f"{where}.trigger.rotation",
                                    lo=-720.0, hi=720.0)
    t["color"] = _require_vec(t["color"], f"{where}.trigger.color", 3, lo=0.0, hi=100.0)
    return None if t["mode"] == "none" else t


def _validate_motion(raw, where, etype):
    if raw is None:
        return None
    key = f"{where}.motion"
    if not isinstance(raw, dict):
        raise ValueError(f"{key} must be an object or null")
    if set(raw) - {'enabled', 'channels'}:
        raise ValueError(f"{key} has unknown keys")
    enabled = raw.get('enabled', True)
    if not isinstance(enabled, bool):
        raise ValueError(f"{key}.enabled must be boolean")
    channels = raw.get('channels', [])
    if not isinstance(channels, list) or len(channels) > 17:
        raise ValueError(f"{key}.channels must be a list of at most 17 curves")
    result, seen = [], set()
    for i, c in enumerate(channels):
        ck = f"{key}.channels[{i}]"
        if not isinstance(c, dict) or set(c) - {'target', 'driver', 'points', 'interpolation'}:
            raise ValueError(f"{ck} must be a curve object with known keys")
        target, driver = c.get('target'), c.get('driver', 'radius')
        if not isinstance(target, str) or target not in MOTION_TARGETS:
            raise ValueError(f"{ck}.target is unknown")
        if target in seen:
            raise ValueError(f"{ck}.target duplicates {target}")
        seen.add(target)
        spec = MOTION_TARGETS[target]
        if 'types' in spec and etype not in spec['types']:
            raise ValueError(f"{ck}.target {target} is not supported by {etype}")
        if not isinstance(driver, str) or driver not in MOTION_DRIVERS:
            raise ValueError(f"{ck}.driver is unknown")
        interpolation = c.get('interpolation', 'smooth')
        if interpolation not in ('smooth', 'linear'):
            raise ValueError(f"{ck}.interpolation must be smooth or linear")
        points = c.get('points')
        if not isinstance(points, list) or not 2 <= len(points) <= 16:
            raise ValueError(f"{ck}.points requires 2..16 [input, output] knots")
        clean = []
        for j, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"{ck}.points[{j}] requires [input, output]")
            x = _require_number(point[0], f"{ck}.points[{j}].input", lo=-100., hi=100.)
            y = _require_number(point[1], f"{ck}.points[{j}].output", lo=spec['min'], hi=spec['max'])
            if clean and x <= clean[-1][0]:
                raise ValueError(f"{ck}.points inputs must be strictly increasing")
            clean.append([x, y])
        result.append(dict(target=target, driver=driver, points=clean, interpolation=interpolation))
    return dict(enabled=enabled, channels=result)


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
    elem["label"] = str(elem["label"])
    elem["slot"] = str(elem["slot"])
    if elem["slot"] and elem["slot"] not in ELEMENT_SLOTS:
        warnings.warn(
            f"{where}.slot {elem['slot']!r} is not one of "
            f"{', '.join(ELEMENT_SLOTS)}; the gallery cannot filter it"
        )
    elem["enabled"] = bool(elem["enabled"])
    elem["solo"] = bool(elem["solo"])
    elem["offset"] = _require_number(elem["offset"], f"{where}.offset", lo=-10.0, hi=10.0)
    elem["scale"] = _require_number(elem["scale"], f"{where}.scale", lo=1e-6, hi=100.0)
    # colour is linear-light and may exceed 1 (HDR), but never go negative:
    # a negative flare darkens the plate and falsifies the alpha mask
    elem["stretch"] = _require_vec(elem["stretch"], f"{where}.stretch", 2)
    if elem["stretch"][0] <= 0 or elem["stretch"][1] <= 0:
        raise ValueError(f"{where}.stretch components must be > 0")
    elem["move"] = _require_vec(elem["move"], f"{where}.move", 2, lo=0.0, hi=1.0)
    elem["shift"] = _require_vec(elem["shift"], f"{where}.shift", 2, lo=-10.0, hi=10.0)
    elem["pin"] = _require_pin(elem["pin"], f"{where}.pin")
    elem["shade"] = _require_number(elem["shade"], f"{where}.shade", lo=-1.0, hi=1.0)
    elem["rotation"] = _require_number(elem["rotation"], f"{where}.rotation")
    elem["auto_rotate"] = bool(elem["auto_rotate"])
    elem["intensity"] = _require_number(elem["intensity"], f"{where}.intensity",
                                        lo=0.0, hi=1000.0)
    elem["color"] = _require_vec(elem["color"], f"{where}.color", 3, lo=0.0, hi=100.0)
    elem["blur"] = _require_number(elem["blur"], f"{where}.blur", lo=0.0, hi=1.0)
    elem["irregular"] = _require_number(elem["irregular"], f"{where}.irregular",
                                        lo=0.0, hi=1.0)
    elem["light_mask"] = _require_number(elem["light_mask"], f"{where}.light_mask",
                                         lo=0.0, hi=1.0)
    elem["mask_scene"] = _require_number(elem["mask_scene"], f"{where}.mask_scene",
                                        lo=0.0, hi=1.0)
    elem["mask_floor"] = _require_number(elem["mask_floor"], f"{where}.mask_floor",
                                        lo=0.0, hi=0.95)
    elem["fill_frame"] = bool(elem["fill_frame"])
    elem["screen_space"] = bool(elem["screen_space"])
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
    elem["trigger"] = _validate_trigger(elem["trigger"], where)
    elem["motion"] = _validate_motion(elem["motion"], where, etype)
    if elem["screen_blend"] not in ("strongest", "all"):
        raise ValueError(f"{where}.screen_blend must be strongest or all")

    # Validate per-type params.
    p = elem["params"]
    if etype in ("ring", "hoop", "glint", "spectral"):
        p["completion"] = _require_number(p["completion"], f"{where}.params.completion",
                                          lo=0.0, hi=360.0)
        p["completion_feather"] = _require_number(
            p["completion_feather"], f"{where}.params.completion_feather", lo=0.0, hi=1.0
        )
    if etype in ("iris", "ring", "hoop", "spectral"):
        p["crescent"] = _require_number(p["crescent"], f"{where}.params.crescent",
                                        lo=0.0, hi=0.98)
        p["crescent_feather"] = _require_number(
            p["crescent_feather"], f"{where}.params.crescent_feather",
            lo=0.001, hi=1.0
        )
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
        p["curve"] = _require_number(p["curve"], f"{where}.params.curve",
                                     lo=-1.0, hi=1.0)
        p["dash"] = _require_number(p["dash"], f"{where}.params.dash",
                                    lo=0.0, hi=1.0)
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
        # 1 is a single one-sided ray, not a degenerate star: it is how a
        # two-tone streak is built (a warm ray one way, a cool one the
        # other) and how a lone hair ray off the source is drawn
        p["points"] = _require_int(p["points"], f"{where}.params.points", lo=1, hi=256)
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
    elif etype == "orbs":
        p["count"] = _require_int(p["count"], f"{where}.params.count", lo=1, hi=200)
        p["size"] = _require_number(p["size"], f"{where}.params.size", lo=1e-3, hi=2.0)
        p["size_jitter"] = _require_number(p["size_jitter"], f"{where}.params.size_jitter",
                                           lo=0.0, hi=1.0)
        p["spread"] = _require_number(p["spread"], f"{where}.params.spread", lo=0.0, hi=5.0)
        p["edge_softness"] = _require_number(
            p["edge_softness"], f"{where}.params.edge_softness", lo=0.0, hi=1.0
        )
        p["illumination"] = _require_number(p["illumination"],
                                            f"{where}.params.illumination", lo=0.01, hi=10.0)
        if p["shape"] not in ("disc", "polygon"):
            raise ValueError(f"{where}.params.shape must be 'disc' or 'polygon', got {p['shape']!r}")
        p["blades"] = _require_int(p["blades"], f"{where}.params.blades", lo=3, hi=32)
        p["ring"] = _require_number(p["ring"], f"{where}.params.ring",
                                    lo=0.0, hi=1.0)
        p["ring_width"] = _require_number(p["ring_width"],
                                          f"{where}.params.ring_width",
                                          lo=0.01, hi=2.0)
        p["spectral"] = _require_number(p["spectral"], f"{where}.params.spectral",
                                        lo=0.0, hi=1.0)
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

    if etype in ("iris", "spectral"):
        p["roundness"] = _require_number(
            p["roundness"], f"{where}.params.roundness", lo=0.0, hi=1.0)

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
        "category": str(raw.get("category", "")),
        "subcategory": str(raw.get("subcategory", "")),
        "global": copy.deepcopy(GLOBAL_DEFAULTS),
        "elements": [],
    }
    if out["category"] and out["category"] not in PRESET_CATEGORIES:
        warnings.warn(
            f"preset category {out['category']!r} is not one of "
            f"{', '.join(PRESET_CATEGORIES)}; it will group on its own"
        )

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
    g["aspect"] = _require_number(g["aspect"], "global.aspect", lo=0.2, hi=5.0)
    g["tint"] = _require_vec(g["tint"], "global.tint", 3, lo=0.0, hi=100.0)
    g["seed"] = _require_int(g["seed"], "global.seed")
    g["fringe"] = _require_number(g["fringe"], "global.fringe", lo=0.0, hi=1.0)
    g["flicker_amount"] = _require_number(g["flicker_amount"], "global.flicker_amount",
                                          lo=0.0, hi=1.0)
    g["flicker_speed"] = _require_number(g["flicker_speed"], "global.flicker_speed",
                                         lo=0.0, hi=10.0)
    g["edge_fade_start"] = _require_number(g["edge_fade_start"], "global.edge_fade_start",
                                           lo=0.0, hi=4.0)
    g["edge_fade_range"] = _require_number(g["edge_fade_range"], "global.edge_fade_range",
                                           lo=0.0, hi=4.0)

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
