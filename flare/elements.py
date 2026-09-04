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
    (kept deliberately; see docs/DECISIONS.md). hollow in [0,1) punches an
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
    return field


def streak(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Anamorphic streak: exponential along, gaussian across.

    The asymmetry between the two falloffs is what makes it read as
    anamorphic rather than as a blurred line. count > 1 emits multiple
    streaks at even angular spacing (each streak spans the full line, so
    spacing is pi / count).
    """
    length = p["length"]
    thickness = p["thickness"]
    count = max(int(p["count"]), 1)

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
        line = torch.exp(-torch.abs(uu) / length) * torch.exp(-((vv / thickness) ** 2))
        if irr > 0.0:
            # brightness waver along the streak's length
            wav = 1.0 + irr * 0.45 * _harmonic_noise(
                uu * (2.5 / max(length, 1e-4)), int(p.get("seed", 0)), 4 + i)
            line = line * wav.clamp(min=0.0)
        field = field + line
    return field


def ring(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Thin gaussian annulus."""
    radius = p["radius"]
    thickness = p["thickness"]
    r = torch.sqrt(u * u + v * v)
    irr = _irregular(p)
    if irr > 0.0:
        # circumferential unevenness: one side of the ring runs brighter,
        # and the radius drifts slightly, like a real reflection ring
        phi = torch.atan2(v, u)
        seed = int(p.get("seed", 0))
        radius = radius * (1.0 + irr * 0.03 * _harmonic_noise(phi, seed, 5))
        gain = (1.0 + irr * 0.6 * _harmonic_noise(phi, seed, 6)).clamp(min=0.0)
        return torch.exp(-(((r - radius) / thickness) ** 2)) * gain
    return torch.exp(-(((r - radius) / thickness) ** 2))


def hoop(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Thick soft annulus fading toward the flare axis.

    The angular factor uses the local +u direction as the axis; with
    auto_rotate on (the default) local +u is the flare axis, matching the
    spec's cos(phi - theta_axis) form.
    """
    radius = p["radius"]
    thickness = p["thickness"]
    angular_falloff = p["angular_falloff"]
    r = torch.sqrt(u * u + v * v)
    phi = torch.atan2(v, u)
    irr = _irregular(p)
    if irr > 0.0:
        seed = int(p.get("seed", 0))
        radius = radius * (1.0 + irr * 0.03 * _harmonic_noise(phi, seed, 5))
    radial = torch.exp(-(((r - radius) / thickness) ** 2))
    angular = (1.0 - angular_falloff * torch.abs(torch.cos(phi))).clamp(min=0.0)
    if irr > 0.0:
        angular = angular * (1.0 + irr * 0.6 * _harmonic_noise(phi, seed, 6)).clamp(min=0.0)
    return radial * angular


def glint(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Starburst: one-sided rays at even angles with seeded length jitter.

    Deterministic for a given seed: jitter comes from a CPU torch.Generator
    seeded from params, independent of the tensor device.
    """
    points = max(int(p["points"]), 2)
    length = p["length"]
    thickness = p["thickness"]
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
    return field


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


ELEMENT_FUNCTIONS = {
    "glow": glow,
    "iris": iris,
    "streak": streak,
    "ring": ring,
    "hoop": hoop,
    "glint": glint,
    "spectral": spectral,
    "texture": texture,
}
