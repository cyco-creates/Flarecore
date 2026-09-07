# SPDX-License-Identifier: Apache-2.0
"""The flare axis model.

Every element sits at a parametric offset t along the vector from the light
position P to the flare anchor E. The anchor defaults to the frame centre
(the classic lens-flare axis), but it is a free point: moving it changes both
the direction of the ghost chain and the spacing between elements, which
scales with |E - P|.

    element_center = P + t * (E - P)

    t = 0  -> on the light
    t = 1  -> on the anchor
    t > 1  -> past the anchor (the ghost chain tail)
    t < 0  -> behind the light, away from the anchor

The axis angle is the direction from the light toward the anchor. Elements
with auto_rotate add this to their own rotation so streaks and polygon ghosts
orient coherently as either point moves.
"""

import math


def axis_angle(px: float, py: float, ex: float = 0.0, ey: float = 0.0) -> float:
    """Angle in radians of the light-to-anchor direction."""
    return math.atan2(ey - py, ex - px)


def element_center(px: float, py: float, t: float,
                   ex: float = 0.0, ey: float = 0.0) -> tuple[float, float]:
    """Position of an element at parametric offset t along the flare axis."""
    return px + t * (ex - px), py + t * (ey - py)
