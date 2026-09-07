# SPDX-License-Identifier: Apache-2.0
"""Stack evaluation and compositing.

The engine walks a validated preset's element stack for each light and
accumulates linear-light RGB. All inputs and outputs here are linear; sRGB
encode/decode is the caller's job (the nodes do it at the boundary).

Dispersion is per-channel radial scaling of the local coordinates with
red rendered outermost (longer wavelengths spread further):

    coordinate scale for sample j = 1 - dispersion * K * w_j

with w_j running from +1 (red) to -1 (blue). Scaling coordinates *down*
renders the feature *larger*, so red ends up outside. At the default 3
samples this is exactly a per-R/G/B evaluation; more samples blend a
piecewise wavelength ramp for a continuous rainbow at equal total energy.

Seeding: an element's random identity is its `id` when the preset gives one,
else its stack index, avalanche-mixed with the global seed and the instance
index — so reordering unrelated elements does not re-jitter a tuned glint,
each count-instance of a chain gets its own jitter, and adjacent seeds do not
walk sideways through the stack.
"""

import math
import zlib

import torch
import torch.nn.functional as F

from .axis import axis_angle, element_center
from .depth import blur_depth
from .elements import ELEMENT_FUNCTIONS
from .grid import make_grid
from .motion import apply_motion

K_DISPERSION = 0.05

# A partially occluded light shrinks its flare as well as dimming it: the
# visible emitting area is smaller, so every element scales by
# (1 - occlusion) ** OCCLUSION_SHRINK on top of the brightness fade.
OCCLUSION_SHRINK = 0.6

# blur = 1.0 softens an element with a gaussian sigma of 4% of frame height.
BLUR_SIGMA_MAX = 0.04

# fringe = 1.0 spreads red outward / blue inward by 2.5% of the half-height
# at the frame corners (radial, so nothing happens at the centre).
FRINGE_MAX = 0.025

# Piecewise-linear spectrum anchors from red to blue.
_SPECTRUM_ANCHORS = [
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 1.0, 1.0),
    (0.0, 0.0, 1.0),
]

_MASK64 = 0xFFFFFFFFFFFFFFFF


def _mix(a: int, b: int) -> int:
    """splitmix64-style avalanche of two integers into a positive 63-bit seed."""
    x = ((a & _MASK64) * 0x9E3779B97F4A7C15 + (b & _MASK64)) & _MASK64
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & _MASK64
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & _MASK64
    return (x ^ (x >> 31)) & 0x7FFFFFFFFFFFFFFF


def element_seed(base_seed: int, elem: dict, index: int, instance: int = 0) -> int:
    """Identity-stable seed for one instance of one element."""
    ident = elem.get("id") or ""
    key = zlib.crc32(ident.encode("utf-8")) if ident else index
    return _mix(_mix(base_seed, key), instance)


def _spectrum_color(x: float) -> tuple[float, float, float]:
    """Sample the red->blue ramp at x in [0, 1]."""
    pos = x * (len(_SPECTRUM_ANCHORS) - 1)
    i = min(int(pos), len(_SPECTRUM_ANCHORS) - 2)
    f = pos - i
    a, b = _SPECTRUM_ANCHORS[i], _SPECTRUM_ANCHORS[i + 1]
    return tuple(a[c] * (1.0 - f) + b[c] * f for c in range(3))


def dispersion_samples(n: int) -> list[tuple[float, tuple[float, float, float]]]:
    """Return [(w, rgb_weight), ...] for n spectral samples.

    w runs from +1 (red) to -1 (blue). RGB weights are normalized so each
    channel sums to 1 across samples: total energy is independent of n, and
    n=3 reduces exactly to one evaluation per R/G/B channel.
    """
    n = max(int(n), 3)
    ws = [1.0 - 2.0 * j / (n - 1) for j in range(n)]
    colors = [_spectrum_color(j / (n - 1)) for j in range(n)]
    sums = [max(sum(c[ch] for c in colors), 1e-9) for ch in range(3)]
    colors = [tuple(c[ch] / sums[ch] for ch in range(3)) for c in colors]
    return list(zip(ws, colors))


def _element_passes(elem, device, dtype):
    """Precompute this element's evaluation passes once per render:
    [(coordinate_scale, weight_tensor(3,))]. Folding the base colour in here
    keeps torch.tensor(...) out of the per-frame/per-light/per-instance loop
    — at video batch sizes those tiny host-to-device uploads dominate."""
    base = elem["color"]
    dispersion = elem["dispersion"]
    if dispersion > 0.0:
        passes = []
        for w, rgb in dispersion_samples(elem["dispersion_samples"]):
            s = 1.0 - dispersion * K_DISPERSION * w
            weight = torch.tensor(
                [rgb[0] * base[0], rgb[1] * base[1], rgb[2] * base[2]],
                device=device, dtype=dtype,
            )
            passes.append((s, weight))
        return passes
    return [(1.0, torch.tensor(base, device=device, dtype=dtype))]


def _ramp01(t: float, falloff: str) -> float:
    t = min(max(t, 0.0), 1.0)
    if falloff == "linear":
        return t
    if falloff == "exponential":
        return t * t
    return t * t * (3.0 - 2.0 * t)          # smooth


def trigger_factor(trig, x: float, y: float, frame_aspect: float,
                   light_x: float = 0.0, light_y: float = 0.0) -> float:
    """How strongly an element's trigger rule fires (0..1) for a point in
    grid coordinates.

    'border' measures the distance to the nearest frame edge (negative
    outside the frame, so a point that has left the frame is fully
    triggered); 'center' measures the distance to the frame centre; 'light'
    measures the distance to the light itself. inner..outer is the ramp from
    fully on to fully off.

    The editor's trigger preview reimplements this in JavaScript
    (web/flarecore_ui.js, triggerFactor) to paint the trigger region live.
    test_trigger_factor_reference_values pins the values both must produce —
    change one and update the other.
    """
    if trig is None:
        return 0.0
    mode = trig["mode"]
    if mode == "border":
        d = min(frame_aspect - abs(x), 1.0 - abs(y))
    elif mode == "light":
        d = math.hypot(x - light_x, y - light_y)
    else:
        d = math.hypot(x, y)
    span = max(trig["outer"] - trig["inner"], 1e-6)
    return 1.0 - _ramp01((d - trig["inner"]) / span, trig["falloff"])


def flicker_gain(base_seed: int, light_index: int, frame: int,
                 amount: float, speed: float) -> float:
    """Per-light brightness flicker: seeded smooth noise over the frame
    index, different for every light so a row of lamps never pulses in
    unison. amount 1 swings +-50%."""
    if amount <= 0.0:
        return 1.0
    t = frame * 0.12 * max(speed, 0.0)
    total = 0.0
    for k, (freq, w) in enumerate(((1.0, 0.5), (2.3, 0.3), (5.1, 0.2))):
        phase = (_mix(base_seed, light_index * 31 + k) % 10007) / 10007.0 * 2.0 * math.pi
        total += w * math.sin(freq * t + phase)
    return max(1.0 + amount * 0.5 * total, 0.0)


def edge_fade(x: float, y: float, frame_aspect: float, start: float, rng: float) -> float:
    """Lens-hood behaviour: a light that has travelled `start` half-heights
    beyond the frame edge fades out over the next `rng`. rng 0 disables."""
    if rng <= 0.0:
        return 1.0
    outside = max(abs(x) - frame_aspect, abs(y) - 1.0, 0.0)
    return 1.0 - _ramp01((outside - start) / rng, "smooth")


def _shade_field(field: torch.Tensor, ramp):
    """Ramp an element's brightness across its own axis.

    A ghost reflected off a curved surface is lit only on the edge facing
    the source: bright cusp on one side fading to nothing on the other,
    which reads as a comet and as a pointed crescent once the frame cuts
    it. `ramp` is None for the ordinary case and costs nothing.
    """
    if ramp is None:
        return field
    return field * (ramp.unsqueeze(-1) if field.dim() == 3 else ramp)


def _apply_weight(field: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    """Colour a field: (H, W) intensity fields broadcast against the RGB
    weight; (H, W, 3) fields (colour textures) multiply per channel."""
    if field.dim() == 3:
        return field * weight
    return field.unsqueeze(-1) * weight


def _blur_rgb(rgb: torch.Tensor, amount: float) -> torch.Tensor:
    """Gaussian-soften an (H, W, 3) contribution; amount is a sigma as a
    fraction of frame height (reuses the depth module's separable blur)."""
    return blur_depth(rgb.permute(2, 0, 1), amount).permute(1, 2, 0)


def _mask_for(scene_mask: torch.Tensor, glow_mask, scene_amount: float):
    """How much of the reveal comes from the scene versus the light itself.

    scene_mask is max(scene luminance, the light's glow); glow_mask is that
    second term alone. At 1 the result IS scene_mask, bit for bit, so every
    existing preset renders unchanged. At 0 only the light's pool reveals the
    element, which is what a lens plate wants: scene luminance carries the
    frame's moving detail, and an element stencilled through it appears to
    crawl even though it never moves.
    """
    if scene_amount >= 1.0 or glow_mask is None:
        return scene_mask
    if scene_amount <= 0.0:
        return glow_mask
    return glow_mask + (scene_mask - glow_mask).clamp(min=0.0) * scene_amount


def _floored(mask: torch.Tensor, floor: float) -> torch.Tensor:
    """Rescale a light mask so everything below `floor` reads as nothing.

    The mask is max(scene luminance, the light's own glow), and neither term
    ever reaches zero: a real plate is never pure black, and a gaussian has
    no edge. An element modulated by that stays faintly lit across the whole
    frame -- lens dirt with particles still visible in the darkest corner.
    Subtracting the floor and renormalising gives the pool a real edge, and
    tightens it around the light as the floor rises, which is the falloff
    control the mask radius alone cannot provide.
    """
    if floor <= 0.0:
        return mask
    return ((mask - floor) / (1.0 - floor)).clamp_(0.0, 1.0)


def _accumulate_element(out, x, y, elem, passes, light, theta, global_scale,
                        base_seed, elem_index, light_weight, scene_mask=None,
                        glow_mask=None,
                        global_aspect=1.0, frame_aspect=1.0, light_rgb=None):
    """Add every count-instance of one element for one light into `out`.

    light_rgb: optional (3,) tensor multiplying this light's colour into the
    element (scene-sampled light colour).
    """
    dynamic, opacity = apply_motion(elem, light, frame_aspect)
    if opacity <= 0.0:
        return
    if dynamic is not elem:
        # Dispersion and coating transmission may also have changed. The
        # cached passes still serve every legacy/static element unchanged.
        elem = dynamic
        passes = _element_passes(elem, out.device, out.dtype)
    light_weight *= opacity
    fn = ELEMENT_FUNCTIONS[elem["type"]]
    px, py = light["x"], light["y"]
    ax = light.get("ax", 0.0)
    ay = light.get("ay", 0.0)
    # real light position for lens-locked elements (their px/py is the lens
    # centre); orbs light up by proximity to this
    real_lx = light.get("lx", px)
    real_ly = light.get("ly", py)
    stretch_x, stretch_y = elem["stretch"]
    move_x, move_y = elem.get("move", (1.0, 1.0))
    shift_x, shift_y = elem.get("shift", (0.0, 0.0))
    pin_x, pin_y = elem.get("pin", (None, None))
    shade = elem.get("shade", 0.0)
    trig = elem.get("trigger")
    # pixel size in grid units: the y axis spans [-1, 1] over the height
    px_grid = 2.0 / max(out.shape[0], 1)

    # Anamorphic widen is a property of the LENS, not of the element: it
    # always stretches along the screen's horizontal axis, so it is applied
    # to the screen-space x coordinate BEFORE the element's local rotation
    # (multiplying stretch_x instead would follow auto_rotate's axis angle).
    x_frame = x                      # untouched grid, for fill_frame elements
    if global_aspect != 1.0:
        x = x / global_aspect

    # a covered light emits from a smaller visible area: shrink with occlusion
    occ = light.get("occlusion", 0.0)
    size_mult = max(1.0 - occ, 1e-3) ** OCCLUSION_SHRINK if occ > 0.0 else 1.0

    blur = elem.get("blur", 0.0)
    lmask = elem.get("light_mask", 0.0)
    mfloor = elem.get("mask_floor", 0.0)
    mscene = elem.get("mask_scene", 1.0)
    heavy = blur > 0.0 or (lmask > 0.0 and scene_mask is not None)

    rot = math.radians(elem["rotation"])
    if elem["auto_rotate"]:
        rot += theta
    cos_r, sin_r = math.cos(rot), math.sin(rot)

    # A rule read at the light's own position is the same for every instance,
    # so it is evaluated once. Rules measured at the element (including every
    # "light" rule, which is about the element's distance TO the light) move
    # with each instance and are evaluated inside the loop.
    f_light = None
    if trig is not None and trig["mode"] != "light" and trig["source"] == "light":
        f_light = trigger_factor(trig, real_lx, real_ly, frame_aspect,
                                 real_lx, real_ly)

    for i in range(elem["count"]):
        t_i = elem["offset"] + i * elem["spread"]
        intensity_i = elem["intensity"] * (elem["count_falloff"] ** i)
        scale_i = max(elem["scale"] * (elem["count_scale_step"] ** i)
                      * global_scale, 1e-6) * size_mult
        # a zero-intensity element may still be lit by its trigger rule
        if intensity_i <= 0.0 and trig is None:
            continue

        params = dict(elem["params"])
        params["seed"] = element_seed(base_seed, elem, elem_index, i)
        params["irregular"] = elem.get("irregular", 0.0)

        cx, cy = element_center(px, py, t_i, ax, ay)
        # translation locks: follow only part of the light-driven motion per
        # screen axis (the anchor is the rest position)
        if move_x != 1.0 or move_y != 1.0:
            cx = ax + (cx - ax) * move_x
            cy = ay + (cy - ay) * move_y
        # a screen-space nudge, applied after the axis so it does not swing
        # with the flare angle, then an optional lock to the frame itself
        if shift_x != 0.0 or shift_y != 0.0:
            cx += shift_x
            cy += shift_y
        if pin_x is not None:
            cx = pin_x
        if pin_y is not None:
            cy = pin_y

        inst_weight = passes
        cos_i, sin_i = cos_r, sin_r
        if trig is not None:
            f = f_light if f_light is not None else trigger_factor(
                trig, cx, cy, frame_aspect, real_lx, real_ly)
            if f > 0.0:
                # brightness is ADDED in intensity units so an element can
                # sit at 0 and only exist while its rule fires
                intensity_i = max(intensity_i + f * trig["brightness"], 0.0)
                scale_i = max(scale_i * (1.0 + f * trig["scale"]), 1e-6)
                if trig["rotation"]:
                    rot_i = rot + math.radians(f * trig["rotation"])
                    cos_i, sin_i = math.cos(rot_i), math.sin(rot_i)
                tc = trig["color"]
                if tc != [1.0, 1.0, 1.0]:
                    mix = torch.tensor([1.0 + f * (c - 1.0) for c in tc],
                                       device=out.device, dtype=out.dtype)
                    inst_weight = [(sc, w * mix) for sc, w in passes]
        if intensity_i <= 0.0:
            continue
        if light_rgb is not None:
            inst_weight = [(sc, w * light_rgb) for sc, w in inst_weight]

        params["_px"] = px_grid / scale_i
        if elem.get("fill_frame"):
            # spans the frame exactly at scale 1: u,v reach +-1 at the frame
            # edges whatever the footage's aspect, and the anamorphic squeeze
            # does not apply — the plate is ON the lens, not through it
            u0 = x_frame - cx
            v0 = y - cy
            sx = scale_i * stretch_x * frame_aspect
            sy = scale_i * stretch_y
            u = (u0 * cos_i + v0 * sin_i) / sx
            v = (-u0 * sin_i + v0 * cos_i) / sy
        else:
            u0 = x - cx / global_aspect
            v0 = y - cy
            # rotate by -rot so the element's local frame is axis-aligned
            u = (u0 * cos_i + v0 * sin_i) / (scale_i * stretch_x)
            v = (-u0 * sin_i + v0 * cos_i) / (scale_i * stretch_y)
        if elem["type"] == "orbs":
            lu0 = real_lx / global_aspect - cx / global_aspect
            lv0 = real_ly - cy
            params["_light_local"] = (
                (lu0 * cos_i + lv0 * sin_i) / (scale_i * stretch_x),
                (-lu0 * sin_i + lv0 * cos_i) / (scale_i * stretch_y),
            )

        # the lit-edge ramp is the same for every dispersion sample, so it
        # is built once from the undispersed local coordinates
        ramp = (1.0 + shade * u).clamp(min=0.0) if shade != 0.0 else None

        if heavy:
            inst = torch.zeros_like(out)
            for s, weight in inst_weight:
                field = fn(u * s, v * s, params) if s != 1.0 else fn(u, v, params)
                inst.add_(_apply_weight(_shade_field(field, ramp), weight))
            if blur > 0.0:
                inst = _blur_rgb(inst, blur * BLUR_SIGMA_MAX)
            if lmask > 0.0 and scene_mask is not None:
                # fade the element toward the scene's bright areas
                m = _mask_for(scene_mask, glow_mask, mscene)
                factor = (1.0 - lmask) + lmask * _floored(m, mfloor)
                inst = inst * factor.unsqueeze(-1)
            out.add_(inst, alpha=intensity_i * light_weight)
        else:
            for s, weight in inst_weight:
                field = fn(u * s, v * s, params) if s != 1.0 else fn(u, v, params)
                out.add_(_apply_weight(_shade_field(field, ramp), weight),
                         alpha=intensity_i * light_weight)


def render_stack(preset, lights, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0, grid=None, out=None,
                 scene_mask=None, glow_mask=None, frame=0):
    """Render one frame's flare stack in linear light.

    preset: a validated preset dict (see schema.validate_preset).
    lights: list of dicts {"x", "y", "brightness", "occlusion"} with x/y in
        grid coordinates (half-frame-heights, center origin), brightness the
        linear source brightness, occlusion in [0, 1]. Optional "ax"/"ay"
        place the flare anchor (the t=1 point); it defaults to the frame
        centre, and element spacing scales with the light-to-anchor distance.
    intensity/scale: node-level global multipliers on top of the preset's.
    out: optional zeroed (height, width, 3) tensor to accumulate into, so a
        video batch can preallocate one output instead of stacking copies.
    scene_mask: optional (height, width) brightness mask in [0, 1]; elements
        with light_mask > 0 fade toward the mask's bright areas.
    frame: frame index (drives global flicker). Lights may carry "color"
        ([r, g, b] linear multiplier, e.g. sampled from the plate) and
        "index" (stable id for flicker; defaults to list position).

    Returns (height, width, 3) linear RGB; genuinely zero where no element
    contributes.
    """
    if grid is None:
        grid = make_grid(height, width, device, dtype)
    x, y = grid

    g = preset["global"]
    master = g.get("master", 1.0)
    g_intensity = g["intensity"] * intensity * master
    g_scale = g["scale"] * scale * master
    base_seed = g["seed"] + extra_seed

    if out is None:
        out = torch.zeros(height, width, 3, device=device, dtype=dtype)

    g_aspect = g.get("aspect", 1.0)
    frame_aspect = width / max(height, 1)
    enabled = [(idx, elem) for idx, elem in enumerate(preset["elements"])
               if elem["enabled"]]
    # solo: while any element is soloed only the soloed ones render (a
    # non-destructive way to isolate a layer while tuning it). The scan runs
    # over ALL elements, not just enabled ones: a soloed element that is
    # also disabled must yield an empty render, not quietly hand the frame
    # back to everything else.
    if any(e.get("solo") for e in preset["elements"]):
        enabled = [(i, e) for i, e in enabled if e.get("solo")]
    axis_elems = [(i, e) for i, e in enabled if not e.get("screen_space")]
    screen_elems = [(i, e) for i, e in enabled if e.get("screen_space")]
    passes_by_idx = {idx: _element_passes(elem, device, dtype)
                     for idx, elem in enabled}

    fl_amount = g.get("flicker_amount", 0.0)
    fl_speed = g.get("flicker_speed", 1.0)
    ef_start = g.get("edge_fade_start", 0.0)
    ef_range = g.get("edge_fade_range", 0.0)

    weights = []
    for k, light in enumerate(lights):
        weight = light.get("brightness", 1.0) * (1.0 - light.get("occlusion", 0.0))
        weight *= flicker_gain(base_seed, int(light.get("index", k)), frame,
                               fl_amount, fl_speed)
        weight *= edge_fade(light["x"], light["y"], frame_aspect, ef_start, ef_range)
        weights.append(weight)
        if weight <= 0.0:
            continue
        rgb = light.get("color")
        light_rgb = (torch.tensor(rgb, device=device, dtype=dtype)
                     if rgb is not None else None)
        theta = axis_angle(light["x"], light["y"],
                           light.get("ax", 0.0), light.get("ay", 0.0))
        for idx, elem in axis_elems:
            _accumulate_element(out, x, y, elem, passes_by_idx[idx], light,
                                theta, g_scale, base_seed, idx, weight,
                                scene_mask=scene_mask, glow_mask=glow_mask,
                                global_aspect=g_aspect,
                                frame_aspect=frame_aspect, light_rgb=light_rgb)

    # Screen-space elements sit on the LENS, not the flare axis: rendered
    # once per frame at frame centre, driven by the strongest light (lens
    # dirt lights up with the light, it does not duplicate per light).
    if screen_elems and weights:
        k_best = max(range(len(weights)), key=lambda k: weights[k])
        for idx, elem in screen_elems:
            indices = range(len(lights)) if elem.get("screen_blend") == "all" else [k_best]
            for k in indices:
                if weights[k] <= 0.0:
                    continue
                best = lights[k]
                # Same coordinates and seed on every pass: illumination
                # adds, while the dirt pattern remains fixed to the lens.
                lens_light = {"x": 0.0, "y": 0.0, "ax": 0.0, "ay": 0.0,
                              "occlusion": 0.0,
                              "source_occlusion": best.get("occlusion", 0.0),
                              "source_brightness": best.get("brightness", 1.0),
                              "lx": best["x"], "ly": best["y"]}
                rgb = best.get("color")
                light_rgb = (torch.tensor(rgb, device=device, dtype=dtype)
                             if rgb is not None else None)
                _accumulate_element(out, x, y, elem, passes_by_idx[idx],
                                    lens_light, 0.0, g_scale, base_seed, idx,
                                    weights[k], scene_mask=scene_mask,
                                    glow_mask=glow_mask,
                                    global_aspect=g_aspect,
                                    frame_aspect=frame_aspect,
                                    light_rgb=light_rgb)

    tint = torch.tensor(g["tint"], device=device, dtype=dtype)
    out.mul_(tint * g_intensity)

    fringe = g.get("fringe", 0.0)
    if fringe > 0.0:
        out.copy_(chromatic_fringe(out, fringe))
    return out


def chromatic_fringe(rgb: torch.Tensor, amount: float) -> torch.Tensor:
    """Radial lateral chromatic aberration on a finished (H, W, 3) frame:
    red is magnified outward and blue inward about the frame centre, so
    colour fringes grow toward the corners like a real wide lens."""
    h, w = rgb.shape[:2]
    k = amount * FRINGE_MAX
    ys = torch.linspace(-1.0, 1.0, h, device=rgb.device, dtype=rgb.dtype)
    xs = torch.linspace(-1.0, 1.0, w, device=rgb.device, dtype=rgb.dtype)
    gy, gx = torch.meshgrid(ys, xs, indexing="ij")
    img = rgb.permute(2, 0, 1).unsqueeze(0)          # (1, 3, H, W)

    def sample(channel, scale):
        grid = torch.stack([gx * scale, gy * scale], dim=-1).unsqueeze(0)
        return F.grid_sample(img[:, channel:channel + 1], grid, mode="bilinear",
                             padding_mode="zeros", align_corners=True)[0, 0]

    # sampling at a SMALLER radius magnifies the channel outward
    r = sample(0, 1.0 - k)
    b = sample(2, 1.0 + k)
    return torch.stack([r, rgb[..., 1], b], dim=-1)


def render_batch(preset, lights_per_frame, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0, scene_masks=None,
                 glow_masks=None,
                 frame_offset=0):
    """Render a batch: lights_per_frame is a list (length B) of light lists.

    Preallocates the (B, height, width, 3) result and renders every frame
    into it in place — no per-frame copies, no stack doubling. scene_masks
    is an optional (B, height, width) brightness stack for light_mask
    elements.
    """
    grid = make_grid(height, width, device, dtype)
    out = torch.zeros(len(lights_per_frame), height, width, 3,
                      device=device, dtype=dtype)
    for i, lights in enumerate(lights_per_frame):
        render_stack(preset, lights, height, width, device, dtype,
                     extra_seed=extra_seed, intensity=intensity, scale=scale,
                     grid=grid, out=out[i],
                     scene_mask=None if scene_masks is None else scene_masks[i],
                     glow_mask=None if glow_masks is None else glow_masks[i],
                     frame=frame_offset + i)
    return out


def composite(image_linear: torch.Tensor, flare_linear: torch.Tensor, mode: str) -> torch.Tensor:
    """Composite flare over image, both in linear light.

    'add' is the physically correct model for light addition and preserves
    HDR. 'screen' is a soft-clipping convenience computed on values clamped
    to [0, 1].
    """
    if mode == "add":
        return image_linear + flare_linear
    if mode == "screen":
        a = image_linear.clamp(0.0, 1.0)
        b = flare_linear.clamp(0.0, 1.0)
        return 1.0 - (1.0 - a) * (1.0 - b)
    raise ValueError(f"unknown blend mode {mode!r}; valid modes are 'add', 'screen'")
