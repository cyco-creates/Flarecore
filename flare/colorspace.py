# SPDX-License-Identifier: Apache-2.0
"""sRGB <-> linear light conversion (IEC 61966-2-1 piecewise curves).

All flare math and compositing happens in linear light. Values above 1.0 are
legal on the linear side (HDR headroom) and are carried through the encode's
power branch without clamping; clamping is the caller's decision.
"""

import torch


# Rec.709 luminance weights — the single definition; every module that needs
# luminance imports this instead of restating the constants.
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)


def luminance(rgb: torch.Tensor) -> torch.Tensor:
    """Rec.709 luminance of a (..., 3) tensor, in whatever domain it is in."""
    w = torch.tensor(LUMA_WEIGHTS, device=rgb.device, dtype=rgb.dtype)
    return (rgb * w).sum(dim=-1)


def srgb_to_linear(x: torch.Tensor) -> torch.Tensor:
    """Decode sRGB-encoded values to linear light. Preserves values > 1."""
    # torch.where evaluates both branches, so the pow input is clamped to keep
    # the unused branch finite for small/negative inputs.
    safe = x.clamp(min=0.04045)
    return torch.where(x <= 0.04045, x / 12.92, ((safe + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: torch.Tensor) -> torch.Tensor:
    """Encode linear light to sRGB. Preserves values > 1 (no clamping)."""
    x = x.clamp(min=0.0)
    safe = x.clamp(min=0.0031308)
    return torch.where(x <= 0.0031308, x * 12.92, 1.055 * safe ** (1.0 / 2.4) - 0.055)
