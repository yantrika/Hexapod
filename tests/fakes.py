"""Test doubles: a fake clock and a fake backend (no PyBullet, no real time)."""

from __future__ import annotations

import numpy as np

import config
from body.backend import BasePose, HexapodBackend, JointArray
from body.clock import ManualClock


class FakeClock(ManualClock):
    """A clock that only moves when told to; ``sleep`` advances it."""


class FakeBackend(HexapodBackend):
    """Records every target set and advances exactly the requested time."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[JointArray] = []  # each entry has shape (6, 3)
        self.sim_time = 0.0

    def _apply(self, angles: JointArray) -> None:
        self.history.append(angles.reshape(len(config.LEG_NAMES), 3).copy())

    def get_joint_angles(self) -> JointArray:
        return self._joint_targets.copy()

    def get_joint_velocities(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), np.zeros(3))

    def advance(self, dt: float) -> float:
        self.sim_time += dt
        return dt

    def close(self) -> None:
        pass
