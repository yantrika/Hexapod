"""Static joint-angle poses (stand, sit) derived from kinematics.

Every foot sits at its neutral horizontal position and a pose-specific
height below the body plane. Angles are radians, shape ``(6, 3)`` in
``config.LEG_NAMES`` x ``config.JOINTS_PER_LEG`` order.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

import config
from body import kinematics

JointArray = NDArray[np.float64]


def pose_angles(body_height: float) -> JointArray:
    """Joint angles that hold the body *body_height* above flat ground."""
    target = kinematics.NEUTRAL_FOOT_LOCAL.copy()
    target[2] = -body_height
    angles = kinematics.ik(target)
    if angles is None:
        raise ValueError(f"no IK solution for body height {body_height} m")
    return np.tile(np.array(angles, dtype=np.float64), (len(config.LEG_NAMES), 1))


STAND_ANGLES = pose_angles(config.BODY_HEIGHT_STAND)  # all zeros by construction
SIT_ANGLES = pose_angles(config.BODY_HEIGHT_SIT)
