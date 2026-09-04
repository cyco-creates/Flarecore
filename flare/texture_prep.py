# SPDX-License-Identifier: Apache-2.0
"""Turn a generated image into a well-behaved flare element texture.

AI-generated element images arrive with a not-quite-black background, the
subject slightly off centre, and hard rectangular borders. Each of those
breaks compositing: a lifted background adds a visible rectangle in additive
light, an off-centre subject orbits its own pivot when rotated, and a hard
border pops when the texture edge crosses the frame. This module fixes all
three in pure tensor math, working in the encoded (display) domain the images
arrive in.
"""

import math

import torch
import torch.nn.functional as F

from .colorspace import luminance as _luminance


def subtract_floor(img: torch.Tensor, black_point: float) -> torch.Tensor:
    """Pull the background to true black: out = (x - bp) / (1 - bp), clamped."""
    if black_point <= 0.0:
        return img.clamp(0.0, 1.0)
    if black_point >= 1.0:
        raise ValueError(f"black_point must be < 1, got {black_point}")
    return ((img - black_point) / (1.0 - black_point)).clamp(0.0, 1.0)


def center_on_energy(img: torch.Tensor) -> torch.Tensor:
    """Shift the image so the luminance-squared centroid sits at the centre.

    Squaring weights the bright core over faint spill, which is what should
    define the element's pivot. The shift pads with black rather than
    wrapping: torch.roll would carry content off one edge back in on the
    opposite side, compositing a phantom ghost mirrored across the element.
    """
    lum = _luminance(img) ** 2
    total = lum.sum()
    if total <= 0:
        return img
    h, w = lum.shape
    ys = torch.arange(h, device=img.device, dtype=img.dtype)
    xs = torch.arange(w, device=img.device, dtype=img.dtype)
    cy = (lum.sum(dim=1) * ys).sum() / total
    cx = (lum.sum(dim=0) * xs).sum() / total
    dy = int(round(h / 2 - cy.item()))
    dx = int(round(w / 2 - cx.item()))
    if dy == 0 and dx == 0:
        return img
    # zero-pad on the side content moves away from, then crop back to size
    x = img.permute(2, 0, 1).unsqueeze(0)
    x = F.pad(x, (max(dx, 0), max(-dx, 0), max(dy, 0), max(-dy, 0)))
    y0 = max(-dy, 0)
    x0 = max(-dx, 0)
    x = x[:, :, y0:y0 + h, x0:x0 + w]
    return x[0].permute(1, 2, 0)


def center_crop_square(img: torch.Tensor) -> torch.Tensor:
    """Crop (H, W, C) to the centred square of the shorter side."""
    h, w = img.shape[:2]
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    return img[y0:y0 + side, x0:x0 + side]


def feather_border(img: torch.Tensor, feather: float) -> torch.Tensor:
    """Fade the outer border of the texture to zero with a cosine ramp.

    feather is the fraction of the half-extent over which the fade runs,
    measured on the Chebyshev distance so corners fade too. This is what
    keeps a texture element from showing its rectangle when scaled up.
    """
    if feather <= 0.0:
        return img
    feather = min(feather, 1.0)
    h, w = img.shape[:2]
    ys = torch.linspace(-1.0, 1.0, h, device=img.device, dtype=img.dtype)
    xs = torch.linspace(-1.0, 1.0, w, device=img.device, dtype=img.dtype)
    yy, xx = torch.meshgrid(ys, xs, indexing="ij")
    d = torch.maximum(yy.abs(), xx.abs())
    t = ((d - (1.0 - feather)) / feather).clamp(0.0, 1.0)
    fade = 0.5 + 0.5 * torch.cos(t * math.pi)
    return img * fade.unsqueeze(-1)


def pad_margin(img: torch.Tensor, size: int, margin: float) -> torch.Tensor:
    """Centre the content in a black (size, size) canvas with `margin` of
    breathing room on every side (margin 0.25 -> content spans the middle
    half). The margin is what guarantees a texture element never crops:
    rotation, stretch and dispersion all sample inside [-1, 1] of element
    space, and content that reaches the texture edge clips against it.
    """
    margin = min(max(float(margin), 0.0), 0.45)
    inner = max(int(round(size * (1.0 - 2.0 * margin))), 8)
    if inner >= size:
        return resize_square(img, size)
    content = resize_square(img, inner)
    canvas = torch.zeros(size, size, img.shape[-1],
                         device=img.device, dtype=img.dtype)
    o = (size - inner) // 2
    canvas[o:o + inner, o:o + inner] = content
    return canvas


def resize_square(img: torch.Tensor, size: int) -> torch.Tensor:
    """Resize a (S, S, C) square texture to (size, size, C), antialiased."""
    if img.shape[0] == size:
        return img
    x = img.permute(2, 0, 1).unsqueeze(0)
    x = F.interpolate(x, size=(size, size), mode="bilinear",
                      align_corners=False, antialias=True)
    return x[0].permute(1, 2, 0)


def prepare_element(img: torch.Tensor, mode: str = "rgb",
                    black_point: float = 0.06, autocenter: bool = True,
                    feather: float = 0.12, size: int = 512,
                    margin: float = 0.0) -> torch.Tensor:
    """Full pipeline: (H, W, 3) in [0, 1] -> (size, size, 3) element texture.

    mode 'luminance' collapses to a neutral intensity element (returned as
    grey RGB so it stays an IMAGE); 'rgb' keeps the colour. margin > 0 shrinks
    the content into the middle of the canvas with that fraction of black on
    every side, so the element has room to breathe and never clips.
    """
    if mode not in ("rgb", "luminance"):
        raise ValueError(f"mode must be 'rgb' or 'luminance', got {mode!r}")
    out = img.clamp(0.0, 1.0)
    if mode == "luminance":
        out = _luminance(out).unsqueeze(-1).expand(-1, -1, 3).contiguous()
    out = subtract_floor(out, black_point)
    out = center_crop_square(out)
    if autocenter:
        out = center_on_energy(out)
    if margin > 0.0:
        out = feather_border(out, feather)
        out = pad_margin(out, size, margin)
    else:
        out = resize_square(out, size)
        out = feather_border(out, feather)
    return out.clamp(0.0, 1.0)
