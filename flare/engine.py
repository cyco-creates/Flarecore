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
"""

import math

import torch

from .axis import axis_angle, element_center
from .elements import ELEMENT_FUNCTIONS
from .grid import make_grid

K_DISPERSION = 0.05

# Piecewise-linear spectrum anchors from red to blue.
_SPECTRUM_ANCHORS = [
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 1.0, 1.0),
    (0.0, 0.0, 1.0),
]


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


def _render_element_instance(x, y, elem, light, theta, global_scale, seed):
    """Accumulated linear RGB (H, W, 3) for every count-instance of one element."""
    fn = ELEMENT_FUNCTIONS[elem["type"]]
    params = dict(elem["params"])
    params["seed"] = seed

    px, py = light["x"], light["y"]
    stretch_x, stretch_y = elem["stretch"]
    base_color = elem["color"]
    dispersion = elem["dispersion"]

    rot = math.radians(elem["rotation"])
    if elem["auto_rotate"]:
        rot += theta
    cos_r, sin_r = math.cos(rot), math.sin(rot)

    out = None
    for i in range(elem["count"]):
        t_i = elem["offset"] + i * elem["spread"]
        intensity_i = elem["intensity"] * (elem["count_falloff"] ** i)
        scale_i = elem["scale"] * (elem["count_scale_step"] ** i) * global_scale
        if intensity_i <= 0.0:
            continue

        cx, cy = element_center(px, py, t_i)
        u0 = x - cx
        v0 = y - cy
        # rotate by -rot so the element's local frame is axis-aligned
        u = (u0 * cos_r + v0 * sin_r) / (scale_i * stretch_x)
        v = (-u0 * sin_r + v0 * cos_r) / (scale_i * stretch_y)

        if dispersion > 0.0:
            for w, rgb in dispersion_samples(elem["dispersion_samples"]):
                s = 1.0 - dispersion * K_DISPERSION * w
                field = fn(u * s, v * s, params)
                weight = torch.tensor(
                    [rgb[0] * base_color[0], rgb[1] * base_color[1], rgb[2] * base_color[2]],
                    device=field.device, dtype=field.dtype,
                ) * intensity_i
                contrib = field.unsqueeze(-1) * weight
                out = contrib if out is None else out + contrib
        else:
            field = fn(u, v, params)
            weight = torch.tensor(base_color, device=field.device, dtype=field.dtype) * intensity_i
            contrib = field.unsqueeze(-1) * weight
            out = contrib if out is None else out + contrib

    return out


def render_stack(preset, lights, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0, grid=None):
    """Render one frame's flare stack in linear light.

    preset: a validated preset dict (see schema.validate_preset).
    lights: list of dicts {"x", "y", "brightness", "occlusion"} with x/y in
        grid coordinates (half-frame-heights, center origin), brightness the
        linear source brightness, occlusion in [0, 1].
    intensity/scale: node-level global multipliers on top of the preset's.

    Returns (height, width, 3) linear RGB; genuinely zero where no element
    contributes.
    """
    if grid is None:
        grid = make_grid(height, width, device, dtype)
    x, y = grid

    g = preset["global"]
    g_intensity = g["intensity"] * intensity
    g_scale = g["scale"] * scale
    tint = g["tint"]
    base_seed = g["seed"] + extra_seed

    flare = torch.zeros(height, width, 3, device=device, dtype=dtype)
    for light in lights:
        weight = light.get("brightness", 1.0) * (1.0 - light.get("occlusion", 0.0))
        if weight <= 0.0:
            continue
        theta = axis_angle(light["x"], light["y"])
        for idx, elem in enumerate(preset["elements"]):
            if not elem["enabled"]:
                continue
            contrib = _render_element_instance(
                x, y, elem, light, theta, g_scale, seed=base_seed + idx
            )
            if contrib is not None:
                flare = flare + contrib * weight

    tint_t = torch.tensor(tint, device=device, dtype=dtype)
    return flare * (tint_t * g_intensity)


def render_batch(preset, lights_per_frame, height, width, device, dtype,
                 extra_seed=0, intensity=1.0, scale=1.0):
    """Render a batch: lights_per_frame is a list (length B) of light lists.

    Returns (B, height, width, 3) linear RGB.
    """
    grid = make_grid(height, width, device, dtype)
    frames = [
        render_stack(preset, lights, height, width, device, dtype,
                     extra_seed=extra_seed, intensity=intensity, scale=scale, grid=grid)
        for lights in lights_per_frame
    ]
    return torch.stack(frames, dim=0)


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
