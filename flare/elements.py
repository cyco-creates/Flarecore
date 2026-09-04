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


def _smoothstep(edge0: float, edge1: float, x: torch.Tensor) -> torch.Tensor:
    """GLSL-style smoothstep; edge0 > edge1 gives a descending step."""
    t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def glow(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Soft radial halo: inverse-power (Moffat-style) profile.

    softness controls core tightness, falloff controls tail length. The
    inverse-power tail is what makes it read as a halo rather than a blob.
    """
    softness = p["softness"]
    falloff = p["falloff"]
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

    field = _smoothstep(1.0, 1.0 - edge_softness, d)
    if hollow > 0.0:
        inner = _smoothstep(hollow, hollow * (1.0 - edge_softness), d)
        field = (field - inner).clamp(min=0.0)
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

    field = torch.zeros_like(u)
    for i in range(count):
        a = i * math.pi / count
        if i == 0:
            uu, vv = u, v
        else:
            ca, sa = math.cos(a), math.sin(a)
            uu = u * ca + v * sa
            vv = -u * sa + v * ca
        field = field + torch.exp(-torch.abs(uu) / length) * torch.exp(-((vv / thickness) ** 2))
    return field


def ring(u: torch.Tensor, v: torch.Tensor, p: dict) -> torch.Tensor:
    """Thin gaussian annulus."""
    radius = p["radius"]
    thickness = p["thickness"]
    r = torch.sqrt(u * u + v * v)
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
    radial = torch.exp(-(((r - radius) / thickness) ** 2))
    angular = (1.0 - angular_falloff * torch.abs(torch.cos(phi))).clamp(min=0.0)
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

    gen = torch.Generator(device="cpu")
    gen.manual_seed(seed & 0x7FFFFFFFFFFFFFFF)
    rand = torch.rand(points, generator=gen)
    lengths = length * (1.0 + jitter * (rand * 2.0 - 1.0))

    field = torch.zeros_like(u)
    for i in range(points):
        a = i * 2.0 * math.pi / points
        ca, sa = math.cos(a), math.sin(a)
        uu = u * ca + v * sa
        vv = -u * sa + v * ca
        li = max(float(lengths[i]), 1e-4)
        ray = torch.exp(-uu.clamp(min=0.0) / li) * torch.exp(-((vv / thickness) ** 2))
        field = field + ray * (uu > 0.0)
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


ELEMENT_FUNCTIONS = {
    "glow": glow,
    "iris": iris,
    "streak": streak,
    "ring": ring,
    "hoop": hoop,
    "glint": glint,
    "spectral": spectral,
}
