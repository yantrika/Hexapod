"""Dry-run backend: implements ``HexapodBackend`` with no physics and no hardware.

Joint targets are clamped by the base class, logged, and "reached" instantly (the measured angles
equal the last targets, the body never tips). It lets the brain, voice and phone page run on a
machine without PyBullet (the Raspberry Pi before the servos exist). It is NOT servo code: it
touches no bus, pin or device.
"""

from __future__ import annotations

import logging
import time

import numpy as np

import config
from body.backend import BasePose, HexapodBackend, JointArray

logger = logging.getLogger(__name__)


class DryRunBackend(HexapodBackend):
    """Logs joint targets: every one at DEBUG, a summary every ``config.DRYRUN_LOG_PERIOD_S``."""

    def __init__(self, log_period_s: float = config.DRYRUN_LOG_PERIOD_S) -> None:
        super().__init__()
        self._log_period_s = log_period_s
        self._last_log = time.monotonic()
        self._applied = 0
        self._angles: JointArray = np.zeros(config.DOF)
        self.sim_time = 0.0

    @property
    def applied_count(self) -> int:
        """How many target sets were applied (a test hook)."""
        return self._applied

    def _apply(self, angles: JointArray) -> None:
        self._applied += 1
        self._angles = angles.copy()
        logger.debug("dry-run targets (deg): %s", np.round(np.degrees(angles), 1).tolist())
        now = time.monotonic()
        if now - self._last_log >= self._log_period_s:
            self._last_log = now
            logger.info("dry-run: %d target sets so far; last (deg) %s", self._applied,
                        np.round(np.degrees(angles), 1).reshape(len(config.LEG_NAMES), -1).tolist())

    def get_joint_angles(self) -> JointArray:
        return self._angles.copy()

    def get_joint_velocities(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), np.zeros(3))

    def advance(self, dt: float) -> float:
        self.sim_time += dt
        return dt

    def close(self) -> None:
        logger.info("dry-run backend closed after %d target sets", self._applied)
