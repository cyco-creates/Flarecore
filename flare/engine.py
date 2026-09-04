# SPDX-License-Identifier: Apache-2.0
"""Stack evaluation and compositing.

The engine walks a validated preset's element stack for each light and
accumulates linear-light RGB. All inputs and outputs here are linear; sRGB
encode/decode is the caller's job (the nodes do it at the boundary).

Dispersion is per-channel radial scaling of the local coordinates with
red rendered outermost (see docs/DECISIONS.md):

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

from .axis import axis_angle, element_center
from .depth import blur_depth
from .elements import ELEMENT_FUNCTIONS
from .grid import make_grid

K_DISPERSION = 0.05

# A partially occluded light shrinks its flare as well as dimming it: the
# visible emitting area is smaller, so every element scales by
# (1 - occlusion) ** OCCLUSION_SHRINK on top of the brightness fade.
OCCLUSION_SHRINK = 0.6

# blur = 1.0 softens an element with a gaussian sigma of 4% of frame height.
BLUR_SIGMA_MAX = 0.04

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


def _accumulate_element(out, x, y, elem, passes, light, theta, global_scale,
                        base_seed, elem_index, light_weight, scene_mask=None):
    """Add every count-instance of one element for one light into `out`."""
    fn = ELEMENT_FUNCTIONS[elem["type"]]
    px, py = light["x"], light["y"]
    ax = light.get("ax", 0.0)
    ay = light.get("ay", 0.0)
    stretch_x, stretch_y = elem["stretch"]

    # a covered light emits from a smaller visible area: shrink with occlusion
    occ = light.get("occlusion", 0.0)
    size_mult = max(1.0 - occ, 1e-3) ** OCCLUSION_SHRINK if occ > 0.0 else 1.0

    blur = elem.get("blur", 0.0)
    lmask = elem.get("light_mask", 0.0)
    heavy = blur > 0.0 or (lmask > 0.0 and scene_mask is not None)

    rot = math.radians(elem["rotation"])
    if elem["auto_rotate"]:
        rot += theta
    cos_r, sin_r = math.cos(rot), math.sin(rot)

    for i in range(elem["count"]):
        t_i = elem["offset"] + i * elem["spread"]
        intensity_i = elem["intensity"] * (elem["count_falloff"] ** i)
        scale_i = max(elem["scale"] * (elem["count_scale_step"] ** i)
                      * global_scale, 1e-6) * size_mult
        if intensity_i <= 0.0:
            continue

        params = dict(elem["params"])
        params["seed"] = element_seed(base_seed, elem, elem_index, i)

        cx, cy = element_center(px, py, t_i, ax, ay)
        u0 = x - cx
        v0 = y - cy
        # rotate by -rot so the element's local frame is axis-aligned
        u = (u0 * cos_r + v0 * sin_r) / (scale_i * stretch_x)
        v = (-u0 * sin_r + v0 * cos_r) / (scale_i * stretch_y)

        if heavy:
            inst = torch.zeros_like(out)
            for s, weight in passes:
                field = fn(u * s, v * s, params) if s != 1.0 else fn(u, v, params)
                inst.add_(_apply_weight(field, weight))
            if blur > 0.0:
                inst = _blur_rgb(inst, blur * BLUR_SIGMA_MAX)
            if lmask > 0.0 and scene_mask is not None:
                # fade the element toward the scene's bright areas
                factor = (1.0 - lmask) + lmask * scene_mask
                inst = inst * factor.unsqueeze(-1)
            out.add_(inst, alpha=intensity_i * light_weight)
        else:
            for s, weight in passes:
                field = fn(u * s, v * s, params) if s != 1.0 else fn(u, v, params)
                out.add_(_apply_weight(field, weight), alpha=intensity_i * light_weight)


def render_stack(preset, lights, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0, grid=None, out=None,
                 scene_mask=None):
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

    Returns (height, width, 3) linear RGB; genuinely zero where no element
    contributes.
    """
    if grid is None:
        grid = make_grid(height, width, device, dtype)
    x, y = grid

    g = preset["global"]
    g_intensity = g["intensity"] * intensity
    g_scale = g["scale"] * scale
    base_seed = g["seed"] + extra_seed

    if out is None:
        out = torch.zeros(height, width, 3, device=device, dtype=dtype)

    enabled = [(idx, elem) for idx, elem in enumerate(preset["elements"])
               if elem["enabled"]]
    passes_by_idx = {idx: _element_passes(elem, device, dtype)
                     for idx, elem in enabled}

    for light in lights:
        weight = light.get("brightness", 1.0) * (1.0 - light.get("occlusion", 0.0))
        if weight <= 0.0:
            continue
        theta = axis_angle(light["x"], light["y"],
                           light.get("ax", 0.0), light.get("ay", 0.0))
        for idx, elem in enabled:
            _accumulate_element(out, x, y, elem, passes_by_idx[idx], light,
                                theta, g_scale, base_seed, idx, weight,
                                scene_mask=scene_mask)

    tint = torch.tensor(g["tint"], device=device, dtype=dtype)
    out.mul_(tint * g_intensity)
    return out


def render_batch(preset, lights_per_frame, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0, scene_masks=None):
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
                     scene_mask=None if scene_masks is None else scene_masks[i])
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
