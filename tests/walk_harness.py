"""Test helper: drive the gait planner in the sim at the control rate and measure the result.

Protocol (sim time, no wall clock): stand for ``SETTLE_S``, then walk with the
command ramped up linearly over ``RAMP_S``, keep going for ``WARMUP_S`` in total
(not measured), then measure for ``seconds``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import config
from body import gait, poses
from body.gait import BodyVelocity
from body.sim_backend import SimBackend

SETTLE_S = 1.0
RAMP_S = 1.0
WARMUP_S = 2.0
CONTROL_DT = 1.0 / config.CONTROL_HZ


@dataclass(frozen=True)
class WalkStats:
    seconds: float
    forward_m: float  # displacement along the initial heading
    lateral_m: float  # displacement to the left of the initial heading
    heading_drift_deg: float
    max_roll_deg: float
    max_pitch_deg: float
    min_height_m: float
    max_height_m: float
    warnings: int  # ticks where the planner had to scale the stride down

    @property
    def speed_m_s(self) -> float:
        return self.forward_m / self.seconds

    @property
    def lateral_speed_m_s(self) -> float:
        return self.lateral_m / self.seconds

    @property
    def yaw_rate_deg_s(self) -> float:
        return self.heading_drift_deg / self.seconds

    @property
    def drift_m(self) -> float:
        return math.hypot(self.forward_m, self.lateral_m)


def _yaw(sim: SimBackend) -> float:
    return float(sim.get_base_pose().rpy[2])


def run_walk(sim: SimBackend, command: BodyVelocity, seconds: float = 10.0) -> WalkStats:
    """Walk with *command* and return statistics for the last *seconds* of sim time."""
    sim.set_joint_targets(poses.STAND_ANGLES)
    for _ in range(round(SETTLE_S / CONTROL_DT)):
        sim.advance(CONTROL_DT)

    phase, t = 0.0, 0.0
    start_xy = start_yaw = None
    unwrapped = 0.0
    last_yaw = 0.0
    rolls, pitches, heights = [], [], []
    scaled_ticks = 0
    total = WARMUP_S + seconds
    while t < total - 1e-9:
        if start_xy is None and t >= WARMUP_S - 1e-9:
            pose = sim.get_base_pose()
            start_xy, start_yaw = pose.position[:2].copy(), _yaw(sim)
            last_yaw, unwrapped = start_yaw, 0.0
        ramp = min(1.0, t / RAMP_S)
        scaled = BodyVelocity(command.vx * ramp, command.vy * ramp, command.yaw_rate * ramp)
        step = gait.plan(phase, scaled)
        scaled_ticks += step.stride_scale < 1.0
        sim.set_joint_targets(step.joint_angles)
        stepped = sim.advance(CONTROL_DT)
        phase = (phase + stepped / config.GAIT_PERIOD_S) % 1.0
        t += stepped
        if start_xy is not None:
            yaw = _yaw(sim)
            unwrapped += (yaw - last_yaw + math.pi) % (2 * math.pi) - math.pi
            last_yaw = yaw
            roll, pitch = sim.tilt_deg()
            rolls.append(abs(roll))
            pitches.append(abs(pitch))
            heights.append(sim.body_height())

    assert start_xy is not None and start_yaw is not None
    delta = sim.get_base_pose().position[:2] - start_xy
    forward = float(delta @ np.array([math.cos(start_yaw), math.sin(start_yaw)]))
    lateral = float(delta @ np.array([-math.sin(start_yaw), math.cos(start_yaw)]))
    return WalkStats(
        seconds=seconds,
        forward_m=forward,
        lateral_m=lateral,
        heading_drift_deg=math.degrees(unwrapped),
        max_roll_deg=max(rolls),
        max_pitch_deg=max(pitches),
        min_height_m=min(heights),
        max_height_m=max(heights),
        warnings=int(scaled_ticks),
    )
