# SPDX-License-Identifier: Apache-2.0
"""Element render functions.

Each function takes local coordinates (u, v) — already translated to the
element center, rotated, and divided by scale and stretch by the engine — and
a params dict, and returns a single-channel intensity field of the same shape
in [0, inf). Colour, dispersion, and intensity are applied by the engine, not
here.
"""

import math

import torch
import torch.nn.functional as F


def _smoothstep(edge0: float, edge1: float, x: torch.Tensor) -> torch.Tensor:
    """GLSL-style smoothstep; edge0 > edge1 gives a descending step."""
    t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# --- procedural irregularity -------------------------------------------------
#
# Real lens artefacts are never mathematically perfect: rings are brighter on
# one side, iris ghosts have wobbly edges and uneven fill, rays differ in
# brightness. The common `irregular` element key (0..1) drives seeded,
# deterministic low-order harmonic noise so procedural elements pick up that
# organic unevenness without losing their identity-stable seeding.

_NOISE_HARMONICS = (1, 2, 3, 5)


def _noise_coeffs(seed: int, salt: int):
    """Seeded amplitudes and phases for the harmonic noise (CPU, device-free)."""
    gen = torch.Generator(device="cpu")
    gen.manual_seed((int(seed) ^ (salt * 0x9E3779B9)) & 0x7FFFFFFFFFFFFFFF)
    n = len(_NOISE_HARMONICS)
    amps = torch.rand(n, generator=gen) + 0.25
    phases = torch.rand(n, generator=gen) * (2.0 * math.pi)
    return amps / amps.sum(), phases


def _harmonic_noise(x: torch.Tensor, seed: int, salt: int = 0) -> torch.Tensor:
    """Smooth zero-mean noise in ~[-1, 1] over x (radians for angular use:
    harmonics are integers, so the field is 2*pi-periodic and seam-free)."""
    amps, phases = _noise_coeffs(seed, salt)
    out = torch.zeros_like(x)
    for k, a, ph in zip(_NOISE_HARMONICS, amps.tolist(), phases.tolist()):
        out = out + a * torch.sin(k * x + ph)
    return out


def _irregular(p: dict) -> float:
    return float(p.get("irregular", 0.0))


def _completion_mask(phi: torch.Tensor, p: dict):
    """Angular window for the circular types: params.completion degrees of
    arc centred on local +u (rotation aims it), with a feathered fade at the
    ends. Returns None for a full circle so the common case costs nothing."""
    completion = float(p.get("completion", 360.0))
    if completion >= 360.0:
        return None
    half = math.radians(completion) / 2.0
    feather = float(p.get("completion_feather", 0.2)) * max(half, 1e-4)
    a = torch.abs(torch.remainder(phi + math.pi, 2.0 * math.pi) - math.pi)
    if feather <= 1e-6:
        return (a <= half).to(phi.dtype)
    return _smoothstep(half, half - feather, a)


# A streak's bow at `curve` 1, as a fraction of the local coordinate
# squared: real anamorphic lines sag, they are not ruled.
CURVE_MAX = 0.05

# Dash cycles per unit of local length. Fixed in element space, so the
# segments scale with the element instead of drifting with its length.
DASH_FREQ = 4.0


def _crescent_mask(u: torch.Tensor, v: torch.Tensor, p: dict):
    """The bite a barrel takes out of a round ghost.

    A clipping disc of the element's own radius slides across it as
    `crescent` rises: 0 leaves the ghost whole, 0.5 takes a third of it,
    and 0.9 leaves a thin arc bulging toward local +u. `rotation` aims it,
    so with auto_rotate on the crescent opens along the flare axis.
    Returns None when there is nothing to clip, so the common case costs
    nothing.
    """
    crescent = float(p.get("crescent", 0.0))
    if crescent <= 0.0:
        return None
    offset = -2.0 * (1.0 - crescent)
    feather = max(float(p.get("crescent_feather", 0.1)), 1e-3)
    du = u - offset
    d = torch.sqrt(du * du + v * v)
    return _smoothstep(1.0 - feather, 1.0 + feather, d)


def _aa_thickness(thickness: float, p: dict) -> tuple[float, float]:
    """Sub-pixel thin features alias and shimmer frame to frame. The engine
    passes the local size of one pixel as params['_px']; a feature thinner
    than ~0.8 px is widened to that and its gain reduced to conserve energy,
    so a hairline ray keeps its brightness budget instead of flickering."""
    px = float(p.get("_px", 0.0))
    floor = 0.8 * px
    if px <= 0.0 or thickness >= floor:
        return thickness, 1.0
    return floor, thickness / floor


def glow(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Soft radial halo: inverse-power (Moffat-style) profile.

    softness controls core tightness, falloff controls tail length. The
    inverse-power tail is what makes it read as a halo rather than a blob.
    """
    softness = p["softness"]
    falloff = p["falloff"]
    irr = _irregular(p)
    if irr > 0.0:
        # asymmetric halo: the effective radius breathes with angle, so the
        # glow bulges to one side instead of being a perfect disc
        phi = torch.atan2(v, u)
        wobble = 1.0 + irr * 0.18 * _harmonic_noise(phi, int(p.get("seed", 0)), 1)
        r2 = (u * u + v * v) * wobble * wobble
    else:
        r2 = u * u + v * v
    return (1.0 + r2 / (softness * softness)) ** (-falloff)


def iris(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Regular n-gon ghost via angular folding.

    d = r / r_edge is the radius relative to the polygon boundary. The
    smoothstep band over d is physically wider at corners than at edge
    midpoints, which mimics the softened corners of real curved iris blades
    (kept for preset compatibility). hollow in [0,1) punches an
    inner n-gon, producing ring ghosts.
    """
    blades = max(int(p["blades"]), 3)
    edge_softness = max(float(p["edge_softness"]), 1e-4)
    hollow = float(p["hollow"])

    r = torch.sqrt(u * u + v * v)
    phi = torch.atan2(v, u)
    sector = 2.0 * math.pi / blades
    phi_folded = torch.remainder(phi + sector / 2.0, sector) - sector / 2.0
    r_edge = math.cos(math.pi / blades) / torch.cos(phi_folded)
    # Artist approximation of curved blades, distinct from edge blur.
    # Zero preserves the legacy polygon; one gives a circular aperture.
    roundness = float(p.get("roundness", 0.0))
    if roundness > 0.0:
        r_edge = r_edge + roundness * (1.0 - r_edge)
    d = r / r_edge

    irr = _irregular(p)
    if irr > 0.0:
        # wobbly blade edge + uneven fill: real iris ghosts are never a
        # perfect polygon of uniform brightness
        seed = int(p.get("seed", 0))
        d = d * (1.0 + irr * 0.08 * _harmonic_noise(phi, seed, 2))

    field = _smoothstep(1.0, 1.0 - edge_softness, d)
    if hollow > 0.0:
        inner = _smoothstep(hollow, hollow * (1.0 - edge_softness), d)
        field = (field - inner).clamp(min=0.0)

    if irr > 0.0:
        shade = 1.0 + irr * 0.45 * _harmonic_noise(phi, seed, 3) * d.clamp(0.0, 1.0)
        field = field * shade.clamp(min=0.0)
    mask = _crescent_mask(u, v, p)
    return field * mask if mask is not None else field


def streak(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Anamorphic streak: exponential along, gaussian across.

    The asymmetry between the two falloffs is what makes it read as
    anamorphic rather than as a blurred line. count > 1 emits multiple
    streaks at even angular spacing (each streak spans the full line, so
    spacing is pi / count).
    """
    length = p["length"]
    thickness, gain = _aa_thickness(p["thickness"], p)
    count = max(int(p["count"]), 1)
    curve = float(p.get("curve", 0.0))
    dash = float(p.get("dash", 0.0))
    seed = int(p.get("seed", 0))

    irr = _irregular(p)
    field = torch.zeros_like(u)
    for i in range(count):
        a = i * math.pi / count
        if i == 0:
            uu, vv = u, v
        else:
            ca, sa = math.cos(a), math.sin(a)
            uu = u * ca + v * sa
            vv = -u * sa + v * ca
        if curve != 0.0:
            # a shallow parabolic arc: straight at the source, sagging
            # toward the ends, which is how a real anamorphic line runs.
            # Subtracted so a POSITIVE curve sags down the screen.
            vv = vv - curve * CURVE_MAX * uu * uu
        line = torch.exp(-torch.abs(uu) / length) * torch.exp(-((vv / thickness) ** 2))
        if dash > 0.0:
            # gaps along the line: threshold the same harmonic noise the
            # irregularity uses, so the segments are seeded and hold still
            n = _harmonic_noise(uu * DASH_FREQ, seed, 20 + i)
            thr = 2.0 * dash - 1.0
            line = line * _smoothstep(thr - 0.15, thr + 0.15, n)
        if irr > 0.0:
            # brightness waver along the streak's length
            wav = 1.0 + irr * 0.45 * _harmonic_noise(
                uu * (2.5 / max(length, 1e-4)), seed, 4 + i)
            line = line * wav.clamp(min=0.0)
        field = field + line
    return field * gain if gain != 1.0 else field


def ring(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Thin gaussian annulus."""
    radius = p["radius"]
    thickness, gain = _aa_thickness(p["thickness"], p)
    r = torch.sqrt(u * u + v * v)
    irr = _irregular(p)
    needs_phi = irr > 0.0 or float(p.get("completion", 360.0)) < 360.0
    phi = torch.atan2(v, u) if needs_phi else None
    if irr > 0.0:
        # circumferential unevenness: one side of the ring runs brighter,
        # and the radius drifts slightly, like a real reflection ring
        seed = int(p.get("seed", 0))
        radius = radius * (1.0 + irr * 0.03 * _harmonic_noise(phi, seed, 5))
        gain = gain * (1.0 + irr * 0.6 * _harmonic_noise(phi, seed, 6)).clamp(min=0.0)
    field = torch.exp(-(((r - radius) / thickness) ** 2)) * gain
    mask = _completion_mask(phi, p) if phi is not None else None
    if mask is not None:
        field = field * mask
    crescent = _crescent_mask(u, v, p)
    return field * crescent if crescent is not None else field


def hoop(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Thick soft annulus fading toward the flare axis.

    The angular factor uses the local +u direction as the axis; with
    auto_rotate on (the default) local +u is the flare axis, matching the
    spec's cos(phi - theta_axis) form.
    """
    radius = p["radius"]
    thickness, gain = _aa_thickness(p["thickness"], p)
    angular_falloff = p["angular_falloff"]
    r = torch.sqrt(u * u + v * v)
    phi = torch.atan2(v, u)
    irr = _irregular(p)
    if irr > 0.0:
        seed = int(p.get("seed", 0))
        radius = radius * (1.0 + irr * 0.03 * _harmonic_noise(phi, seed, 5))
    radial = torch.exp(-(((r - radius) / thickness) ** 2)) * gain
    angular = (1.0 - angular_falloff * torch.abs(torch.cos(phi))).clamp(min=0.0)
    if irr > 0.0:
        angular = angular * (1.0 + irr * 0.6 * _harmonic_noise(phi, seed, 6)).clamp(min=0.0)
    mask = _completion_mask(phi, p)
    if mask is not None:
        angular = angular * mask
    crescent = _crescent_mask(u, v, p)
    if crescent is not None:
        angular = angular * crescent
    return radial * angular


def glint(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Starburst: one-sided rays at even angles with seeded length jitter.

    Deterministic for a given seed: jitter comes from a CPU torch.Generator
    seeded from params, independent of the tensor device.
    """
    points = max(int(p["points"]), 1)
    length = p["length"]
    thickness, gain = _aa_thickness(p["thickness"], p)
    jitter = p["length_jitter"]
    seed = int(p.get("seed", 0))

    irr = _irregular(p)

    gen = torch.Generator(device="cpu")
    gen.manual_seed(seed & 0x7FFFFFFFFFFFFFFF)
    rand = torch.rand(points, generator=gen)
    lengths = length * (1.0 + jitter * (rand * 2.0 - 1.0))
    # irregular rays: per-ray brightness variation and angular wobble on top
    # of the length jitter — even spokes are the giveaway of a fake starburst
    gains = 1.0 + irr * 1.2 * (torch.rand(points, generator=gen) - 0.5)
    wobble = irr * 0.5 * (torch.rand(points, generator=gen) - 0.5) \
        * (2.0 * math.pi / points)

    field = torch.zeros_like(u)
    for i in range(points):
        a = i * 2.0 * math.pi / points + float(wobble[i])
        ca, sa = math.cos(a), math.sin(a)
        uu = u * ca + v * sa
        vv = -u * sa + v * ca
        li = max(float(lengths[i]), 1e-4)
        ray = torch.exp(-uu.clamp(min=0.0) / li) * torch.exp(-((vv / thickness) ** 2))
        field = field + ray * (uu > 0.0) * max(float(gains[i]), 0.1)
    if gain != 1.0:
        field = field * gain
    mask = _completion_mask(torch.atan2(v, u), p)
    return field * mask if mask is not None else field


def spectral(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Ring or iris intended to be rendered with high dispersion.

    The rainbow comes from the engine's dispersion path (the schema defaults
    this type to dispersion 1.0 with 7 spectral samples); the shape itself is
    just a ring or an iris.
    """
    if p["shape"] == "iris":
        return iris(u, v, p)
    return ring(u, v, p)


def texture(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Sample a texture as an element field.

    The texture spans [-1, 1] of local element space and is zero outside, so
    it obeys the same transform pipeline (offset, scale, stretch, rotation,
    auto_rotate, count, dispersion) as procedural elements. The tensor itself
    is injected at render time as params['_texture'] — (H, W) for an
    intensity field or (H, W, 3) for a colour texture, in LINEAR light —
    because presets only carry a file reference, not pixels.
    """
    tex = p.get("_texture")
    if tex is None:
        raise ValueError(
            "texture element has no texture loaded; set params.file to a "
            "file in the element library (elements/<category>/<name>.png)"
        )
    tex = tex.to(device=u.device, dtype=u.dtype)
    if tex.dim() == 2:
        tex_in = tex.unsqueeze(0).unsqueeze(0)          # (1, 1, H, W)
    else:
        tex_in = tex.permute(2, 0, 1).unsqueeze(0)      # (1, 3, H, W)

    grid = torch.stack([u, v], dim=-1).unsqueeze(0)     # (1, Hout, Wout, 2)
    sampled = F.grid_sample(tex_in, grid, mode="bilinear",
                            padding_mode="zeros", align_corners=False)
    out = sampled[0].permute(1, 2, 0)                    # (Hout, Wout, C)
    if out.shape[-1] == 1:
        return out.squeeze(-1)
    return out


def orbs(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Procedural out-of-focus spots on the lens.

    Seeded discs (or polygons) scattered within `spread` of the element
    centre, each lit by its proximity to the light: the engine passes the
    light's position in this element's local frame as params['_light_local'],
    and an orb's brightness falls off as 1 / (1 + (d / illumination)^2). With
    the type's default screen_space=True the orbs stay glued to the lens while
    the light sweeps across them, which is what real lens dirt does.
    """
    count = max(int(p["count"]), 1)
    size = float(p["size"])
    jitter = float(p["size_jitter"])
    spread = float(p["spread"])
    edge = max(float(p["edge_softness"]), 1e-3)
    illum = max(float(p["illumination"]), 1e-3)
    seed = int(p.get("seed", 0))
    lx, ly = p.get("_light_local", (0.0, 0.0))

    gen = torch.Generator(device="cpu")
    gen.manual_seed(seed & 0x7FFFFFFFFFFFFFFF)
    rnd = torch.rand(count, 5, generator=gen)
    radii = spread * torch.sqrt(rnd[:, 0])          # uniform over the disc
    ring = float(p.get("ring", 0.0))
    if ring > 0.0:
        # gather the specks onto an annulus instead: the dusty rim of a
        # front-element reflection, where the grime catches the light
        rw = float(p.get("ring_width", 0.3))
        on_rim = spread * (1.0 - rw * 0.5 + rw * rnd[:, 0])
        radii = radii * (1.0 - ring) + on_rim * ring
    angles = rnd[:, 1] * 2.0 * math.pi
    ox = radii * torch.cos(angles)
    oy = radii * torch.sin(angles)
    sizes = size * (1.0 + jitter * (rnd[:, 2] * 2.0 - 1.0)).clamp(min=0.15)
    gains = 0.6 + 0.8 * rnd[:, 3]
    rots = rnd[:, 4] * 2.0 * math.pi

    # Each speck diffracts its own colour. The hue column is drawn AFTER
    # the five above so switching it on does not re-roll an existing
    # preset's layout.
    spectral = float(p.get("spectral", 0.0))
    tints = None
    if spectral > 0.0:
        from .engine import _spectrum_color      # lazy: engine imports us
        hue = torch.rand(count, generator=gen)
        tints = [tuple(1.0 + spectral * (c - 1.0)
                       for c in _spectrum_color(float(hue[i])))
                 for i in range(count)]

    polygon = p.get("shape") == "polygon"
    blades = max(int(p.get("blades", 6)), 3)
    sector = 2.0 * math.pi / blades

    field = (torch.zeros(u.shape + (3,), device=u.device, dtype=u.dtype)
             if tints is not None else torch.zeros_like(u))
    for i in range(count):
        cx, cy, sz = float(ox[i]), float(oy[i]), float(sizes[i])
        du = (u - cx) / sz
        dv = (v - cy) / sz
        r = torch.sqrt(du * du + dv * dv)
        if polygon:
            phi = torch.atan2(dv, du) + float(rots[i])
            folded = torch.remainder(phi + sector / 2.0, sector) - sector / 2.0
            r = r / (math.cos(math.pi / blades) / torch.cos(folded))
        disc = _smoothstep(1.0, 1.0 - edge, r)
        d_light = math.hypot(cx - lx, cy - ly)
        lit = 1.0 / (1.0 + (d_light / illum) ** 2)
        lobe = disc * (float(gains[i]) * lit)
        if tints is None:
            field = field + lobe
        else:
            t = tints[i]
            field[..., 0] += lobe * t[0]
            field[..., 1] += lobe * t[1]
            field[..., 2] += lobe * t[2]
    return field


ELEMENT_FUNCTIONS = {
    "glow": glow,
    "iris": iris,
    "streak": streak,
    "ring": ring,
    "hoop": hoop,
    "glint": glint,
    "spectral": spectral,
    "texture": texture,
    "orbs": orbs,
}
