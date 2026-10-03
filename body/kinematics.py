"""Per-leg forward and inverse kinematics (coxa yaw, femur pitch, tibia pitch).

Pure functions, no simulator dependency.

Leg-local frame: origin at the coxa joint (where the leg mounts on the body),
+X pointing radially outward along the leg's mount direction, +Y to the
counter-clockwise side of +X (seen from above), +Z up. Every leg uses the same
local frame and the same IK; left/right differences come only from the mount
yaw, so there are no per-side sign flips.

Joint frame (clean: 0 = neutral, no offsets), angles in radians:
- coxa:  yaw about +Z, positive counter-clockwise from above.
- femur: pitch, positive raises the femur above horizontal.
- tibia: pitch relative to the femur. At 0 the tibia is perpendicular to the
  femur (pointing straight down when the femur is horizontal), so all-zero
  joints give the neutral pose: femur horizontal, tibia vertical.
  Positive straightens the leg (the knee opens), negative bends it.

IK returns the knee-up branch: in the leg's vertical plane the knee lies on
the upper side of the line from the femur joint to the foot.

``leg_to_body`` / ``body_to_leg`` are the only places this module touches the
body frame, and they get the mount orientation solely from
``config.mount_yaw_rad`` (the project's one conversion point).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

import config

Vec3 = NDArray[np.float64]
JointAngles = tuple[float, float, float]  # (coxa, femur, tibia), radians

_EPS = 1e-9  # slack for boundary rounding (metres / radians)


def _vec(x: float, y: float, z: float) -> Vec3:
    return np.array([x, y, z], dtype=np.float64)


def fk_joints(coxa: float, femur: float, tibia: float) -> tuple[Vec3, Vec3, Vec3]:
    """Return ``(femur_joint, knee, foot)`` in the leg-local frame."""
    cos_c, sin_c = math.cos(coxa), math.sin(coxa)
    femur_joint = _vec(config.COXA_LENGTH * cos_c, config.COXA_LENGTH * sin_c, 0.0)

    knee_r = config.FEMUR_LENGTH * math.cos(femur)
    knee = femur_joint + _vec(knee_r * cos_c, knee_r * sin_c, config.FEMUR_LENGTH * math.sin(femur))

    tibia_dir = femur + tibia - math.pi / 2  # absolute pitch of the tibia
    foot_r = config.TIBIA_LENGTH * math.cos(tibia_dir)
    foot = knee + _vec(foot_r * cos_c, foot_r * sin_c, config.TIBIA_LENGTH * math.sin(tibia_dir))
    return femur_joint, knee, foot


def fk(coxa: float, femur: float, tibia: float) -> Vec3:
    """Foot position in the leg-local frame for the given joint angles."""
    return fk_joints(coxa, femur, tibia)[2]


def _within_hard_limits(angles: JointAngles) -> bool:
    for joint, angle in zip(config.JOINTS_PER_LEG, angles, strict=True):
        low, high = config.JOINT_HARD_LIMITS_DEG[joint]
        if not math.radians(low) - _EPS <= angle <= math.radians(high) + _EPS:
            return False
    return True


def ik(point: Sequence[float] | Vec3) -> JointAngles | None:
    """Joint angles ``(coxa, femur, tibia)`` that put the foot at *point*.

    *point* is in the leg-local frame. Returns the knee-up solution, or
    ``None`` if the point is unreachable or the solution violates
    ``config.JOINT_HARD_LIMITS_DEG``. Never raises for any input.
    """
    try:
        x, y, z = (float(v) for v in point)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, z)):
        return None

    femur_len, tibia_len = config.FEMUR_LENGTH, config.TIBIA_LENGTH
    coxa = math.atan2(y, x)
    r = math.hypot(x, y) - config.COXA_LENGTH  # horizontal reach from the femur joint
    dist = math.hypot(r, z)  # femur joint -> foot
    min_reach = abs(femur_len - tibia_len)
    if dist > femur_len + tibia_len + _EPS or dist < min_reach - _EPS or dist < _EPS:
        return None

    # Law of cosines, clamped so boundary rounding cannot raise in acos.
    cos_alpha = (femur_len**2 + dist**2 - tibia_len**2) / (2.0 * femur_len * dist)
    cos_gamma = (femur_len**2 + tibia_len**2 - dist**2) / (2.0 * femur_len * tibia_len)
    alpha = math.acos(max(-1.0, min(1.0, cos_alpha)))  # femur vs. femur-joint->foot line
    gamma = math.acos(max(-1.0, min(1.0, cos_gamma)))  # interior knee angle

    femur = math.atan2(z, r) + alpha  # "+" is the knee-up branch
    tibia = gamma - math.pi / 2
    angles = (coxa, femur, tibia)
    return angles if _within_hard_limits(angles) else None


def hip_position(leg: str) -> Vec3:
    """Coxa joint position in the body frame."""
    yaw = config.mount_yaw_rad(leg)
    return _vec(config.BODY_RADIUS * math.cos(yaw), config.BODY_RADIUS * math.sin(yaw), 0.0)


def _rotation_z(yaw: float) -> NDArray[np.float64]:
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return np.array([[cos_y, -sin_y, 0.0], [sin_y, cos_y, 0.0], [0.0, 0.0, 1.0]])


def leg_to_body(leg: str, point: Sequence[float] | Vec3) -> Vec3:
    """Convert a leg-local point to the body frame."""
    local = np.asarray(point, dtype=np.float64)
    rotated: Vec3 = _rotation_z(config.mount_yaw_rad(leg)) @ local
    return hip_position(leg) + rotated


def body_to_leg(leg: str, point: Sequence[float] | Vec3) -> Vec3:
    """Convert a body-frame point to the leg-local frame (inverse of ``leg_to_body``)."""
    offset = np.asarray(point, dtype=np.float64) - hip_position(leg)
    local: Vec3 = _rotation_z(config.mount_yaw_rad(leg)).T @ offset
    return local


NEUTRAL_FOOT_LOCAL: Vec3 = fk(0.0, 0.0, 0.0)  # (COXA + FEMUR, 0, -TIBIA)


def neutral_foot_body(leg: str) -> Vec3:
    """Neutral foot position (all joints at 0) in the body frame."""
    return leg_to_body(leg, NEUTRAL_FOOT_LOCAL)
