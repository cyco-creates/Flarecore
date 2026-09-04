# SPDX-License-Identifier: Apache-2.0
"""The flare axis model.

Every element sits at a parametric offset t along the vector from the light
position P to the frame center C. With C at the grid origin:

    element_center = P * (1 - t)

    t = 0  -> on the light
    t = 1  -> at frame center
    t > 1  -> past center on the opposite side (the ghost chain)
    t < 0  -> outside the light, away from center

The axis angle is atan2(-P.y, -P.x): the direction from the light toward
center. Elements with auto_rotate add this to their own rotation so streaks
and polygon ghosts orient coherently as the light moves.
"""

import math


def axis_angle(px: float, py: float) -> float:
    """Angle in radians of the light-to-center direction."""
    return math.atan2(-py, -px)


def element_center(px: float, py: float, t: float) -> tuple[float, float]:
    """Position of an element at parametric offset t along the flare axis."""
    return px * (1.0 - t), py * (1.0 - t)
