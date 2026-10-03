"""Tripod gait planner: pure functions from phase and body velocity to foot targets.

No simulator, no clock. The caller advances ``phase`` (0 <= phase < 1) and
supplies a body velocity in the body frame (+X forward, +Y left, yaw
counter-clockwise, rotation about the body centre). Forward, strafe and turn
all come from the same function.

Phase layout. Tripod A (RF, RR, LM) swings during ``[0, swing_fraction)`` of
its cycle and tripod B (RM, LR, LF) is half a cycle behind, so the groups are
never in swing together (``swing_fraction <= 0.5``).

Foot paths, per leg, in the body frame:
- A foot's required velocity relative to the body is ``u = -(v + yaw_rate x p)``
  where ``p`` is its neutral position. Over the stance time it travels
  ``u * stance_time``, centred on the neutral position.
- Stance: straight line from the front end to the back end at ground height.
- Swing: from the back end to the front end. Horizontal progress uses a
  cycloid ``s - sin(2 pi s) / (2 pi)`` and height ``H sin^2(pi s)``, so foot
  velocity is zero at lift-off and touch-down. Positions are continuous
  across the phase wrap (the speed jumps between swing and stance).
- The swing height scales with the stride when the stride is below
  ``full_lift_stride_m``, so targets stay continuous as a command ramps to zero.
  A zero command puts every foot at its neutral position on the ground.

Every foot target goes through ``kinematics.ik`` and must respect
``config.GAIT_SOFT_LIMITS_DEG``. If a target does not, ``plan`` scales the
stride down (largest feasible scale, found by bisection) and logs a warning.
Over-limit commands are clamped to the max speed, max yaw rate and max stride.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

import config
from body import kinematics

logger = logging.getLogger(__name__)

FootArray = NDArray[np.float64]  # shape (6, 3), config.LEG_NAMES order
_SCALE_SEARCH_ITERATIONS = 12
_ZERO = 1e-9


@dataclass(frozen=True)
class GaitParams:
    """Gait parameters; defaults come from ``config.py``."""

    period_s: float = config.GAIT_PERIOD_S
    swing_fraction: float = config.GAIT_SWING_FRACTION
    swing_height_m: float = config.STEP_HEIGHT_M
    body_height_m: float = config.BODY_HEIGHT_STAND
    max_stride_m: float = config.STEP_LENGTH_MAX_M
    max_speed_m_s: float = config.GAIT_MAX_SPEED_M_S
    max_yaw_rate_rad_s: float = math.radians(config.TURN_RATE_MAX_DEG_S)
    full_lift_stride_m: float = config.GAIT_FULL_LIFT_STRIDE_M

    def __post_init__(self) -> None:
        if self.period_s <= 0:
            raise ValueError("period_s must be positive")
        if not 0.0 < self.swing_fraction <= 0.5:
            raise ValueError("swing_fraction must be in (0, 0.5]; groups must not overlap")

    @property
    def stance_time_s(self) -> float:
        return (1.0 - self.swing_fraction) * self.period_s


@dataclass(frozen=True)
class BodyVelocity:
    """Body-frame velocity command: m/s forward, m/s left, rad/s counter-clockwise."""

    vx: float = 0.0
    vy: float = 0.0
    yaw_rate: float = 0.0


@dataclass(frozen=True)
class GaitStep:
    """Planner output for one tick."""

    foot_targets: FootArray  # (6, 3), body frame
    joint_angles: FootArray  # (6, 3), radians, within the soft limits
    stride_scale: float  # 1.0 unless the stride had to be reduced


DEFAULT_PARAMS = GaitParams()

# Tripod A at group phase == cycle phase, tripod B half a cycle behind.
_GROUP_OFFSET = {leg: (0.0 if leg in config.TRIPOD_A else 0.5) for leg in config.LEG_NAMES}
_NEUTRAL_XY = {
    leg: (float(kinematics.neutral_foot_body(leg)[0]), float(kinematics.neutral_foot_body(leg)[1]))
    for leg in config.LEG_NAMES
}
_SOFT_LOW = np.radians([config.GAIT_SOFT_LIMITS_DEG[j][0] for j in config.JOINTS_PER_LEG])
_SOFT_HIGH = np.radians([config.GAIT_SOFT_LIMITS_DEG[j][1] for j in config.JOINTS_PER_LEG])


def group_phase(leg: str, phase: float) -> float:
    """Phase of *leg*'s tripod group in ``[0, 1)``."""
    return (phase + _GROUP_OFFSET[leg]) % 1.0


def is_swing(leg: str, phase: float, params: GaitParams = DEFAULT_PARAMS) -> bool:
    """True while *leg* is in the swing part of its cycle."""
    return group_phase(leg, phase) < params.swing_fraction


def _foot_velocity(leg: str, command: BodyVelocity) -> tuple[float, float]:
    """Velocity of the foot relative to the body while it stays fixed on the ground."""
    x, y = _NEUTRAL_XY[leg]
    return -(command.vx - command.yaw_rate * y), -(command.vy + command.yaw_rate * x)


def limit_command(command: BodyVelocity, params: GaitParams = DEFAULT_PARAMS) -> BodyVelocity:
    """Clamp *command* to the max speed, max yaw rate and max stride."""
    speed = math.hypot(command.vx, command.vy)
    speed_scale = 1.0 if speed <= params.max_speed_m_s else params.max_speed_m_s / speed
    yaw = config.clamp(command.yaw_rate, -params.max_yaw_rate_rad_s, params.max_yaw_rate_rad_s)
    limited = BodyVelocity(command.vx * speed_scale, command.vy * speed_scale, yaw)
    worst = max(
        math.hypot(*_foot_velocity(leg, limited)) * params.stance_time_s
        for leg in config.LEG_NAMES
    )
    if worst > params.max_stride_m:
        factor = params.max_stride_m / worst
        limited = BodyVelocity(limited.vx * factor, limited.vy * factor, limited.yaw_rate * factor)
    return limited


def foot_targets(
    phase: float,
    command: BodyVelocity,
    params: GaitParams = DEFAULT_PARAMS,
    stride_scale: float = 1.0,
) -> FootArray:
    """Six foot targets in the body frame, shape ``(6, 3)``, ``config.LEG_NAMES`` order.

    *command* is clamped by ``limit_command``; *stride_scale* (0..1) shrinks
    the stride further and is used by ``plan`` when a pose is unreachable.
    """
    command = limit_command(command, params)
    ground = -params.body_height_m
    stance_time = params.stance_time_s
    beta = params.swing_fraction
    still = (abs(command.vx) + abs(command.vy) + abs(command.yaw_rate)) * stride_scale < _ZERO
    worst_stride = stride_scale * stance_time * max(
        math.hypot(*_foot_velocity(leg, command)) for leg in config.LEG_NAMES
    )
    lift = min(1.0, worst_stride / params.full_lift_stride_m)

    targets = np.zeros((len(config.LEG_NAMES), 3))
    for i, leg in enumerate(config.LEG_NAMES):
        neutral = _NEUTRAL_XY[leg]
        if still:
            targets[i] = (neutral[0], neutral[1], ground)
            continue
        ux, uy = _foot_velocity(leg, command)
        half = 0.5 * stride_scale * stance_time
        half_x, half_y = half * ux, half * uy
        front_x, front_y = neutral[0] - half_x, neutral[1] - half_y  # stance: front -> back
        back_x, back_y = neutral[0] + half_x, neutral[1] + half_y
        gp = group_phase(leg, phase)
        if gp < beta:  # swing: back -> front, cycloid progress, sin^2 lift
            s = gp / beta
            progress = s - math.sin(2.0 * math.pi * s) / (2.0 * math.pi)
            x = back_x + (front_x - back_x) * progress
            y = back_y + (front_y - back_y) * progress
            z = ground + lift * params.swing_height_m * math.sin(math.pi * s) ** 2
        else:  # stance: straight line at ground height
            s = (gp - beta) / (1.0 - beta)
            x = front_x + (back_x - front_x) * s
            y = front_y + (back_y - front_y) * s
            z = ground
        targets[i] = (x, y, z)
    return targets


def _solve(targets: FootArray) -> FootArray | None:
    """IK for all legs; ``None`` if any is unreachable or outside the soft limits."""
    angles = np.zeros((len(config.LEG_NAMES), 3))
    for i, leg in enumerate(config.LEG_NAMES):
        solution = kinematics.ik(kinematics.body_to_leg(leg, targets[i]))
        if solution is None:
            return None
        row = np.array(solution)
        if np.any(row < _SOFT_LOW - 1e-9) or np.any(row > _SOFT_HIGH + 1e-9):
            return None
        angles[i] = row
    return angles


def plan(
    phase: float, command: BodyVelocity, params: GaitParams = DEFAULT_PARAMS
) -> GaitStep:
    """Foot targets plus joint angles for one tick; never returns an out-of-limit pose.

    If the full stride is unreachable the stride is scaled down to the largest
    feasible fraction and a warning is logged. Raises ``ValueError`` only if
    even the neutral stance is outside the soft limits (a bad configuration).
    """
    targets = foot_targets(phase, command, params)
    angles = _solve(targets)
    if angles is not None:
        return GaitStep(targets, angles, 1.0)

    low, high = 0.0, 1.0  # scale 0 is the neutral stance
    best_targets = foot_targets(phase, BodyVelocity(), params)
    best_angles = _solve(best_targets)
    if best_angles is None:
        raise ValueError("neutral stance is outside GAIT_SOFT_LIMITS_DEG; check the gait config")
    best_scale = 0.0
    for _ in range(_SCALE_SEARCH_ITERATIONS):
        mid = 0.5 * (low + high)
        candidate = foot_targets(phase, command, params, mid)
        solved = _solve(candidate)
        if solved is None:
            high = mid
        else:
            low, best_scale, best_targets, best_angles = mid, mid, candidate, solved
    logger.warning(
        "gait target unreachable at phase %.3f; stride scaled to %.2f", phase % 1.0, best_scale
    )
    return GaitStep(best_targets, best_angles, best_scale)
