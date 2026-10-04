"""Dry-run backend: implements ``HexapodBackend`` with no physics and no hardware.

Joint targets are clamped by the base class, logged, and "reached" instantly (the measured angles
equal the last targets, the body never tips). It lets the brain, voice and phone page run on a
machine without PyBullet (the Raspberry Pi before the servos exist). It is NOT servo code: it
touches no bus, pin or device.

What the gait would send to the servos goes to its own file, ``config.DRYRUN_LOG_FILE``
(``logs/dryrun.log``, rotated), as per-leg angles in degrees (coxa, femur, tibia), so
``tail -f logs/dryrun.log`` shows it. One line is written
  * for the first targets,
  * at most ``DRYRUN_LOG_HZ`` times a second while the targets keep changing (``MOVING``),
  * once, ``REST``, when they stopped changing for ``DRYRUN_REST_S`` (the new pose).
Nothing is written while the robot stands still.
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

import config
from body.backend import BasePose, HexapodBackend, JointArray

logger = logging.getLogger(__name__)


def _file_logger(path: Path) -> logging.Logger:
    """A logger of its own that writes only to *path* (it never reaches the console or hexa.log)."""
    log = logging.getLogger(f"hexa.dryrun.{path}")
    if not log.handlers:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=config.DRYRUN_LOG_MAX_BYTES, backupCount=config.DRYRUN_LOG_BACKUPS)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


class DryRunBackend(HexapodBackend):
    """Logs the joint targets to ``logs/dryrun.log``; reaches every target instantly."""

    def __init__(
        self,
        log_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._clock = clock
        self._path = Path(log_path) if log_path is not None else config.DRYRUN_LOG_FILE
        self._log = _file_logger(self._path)
        self._applied = 0
        self._angles: JointArray = np.zeros(config.DOF)
        self.sim_time = 0.0
        self._t0 = clock()
        self._logged: JointArray | None = None  # the targets of the last line written
        self._last_change = self._t0  # when the targets last moved by more than the epsilon
        self._last_line = float("-inf")
        self._resting = True  # the REST line for the current pose is already out (or none needed)
        logger.info("dry-run backend: joint targets are logged to %s", self._path)

    @property
    def applied_count(self) -> int:
        """How many target sets were applied (a test hook)."""
        return self._applied

    @property
    def log_path(self) -> Path:
        return self._path

    def _apply(self, angles: JointArray) -> None:
        self._applied += 1
        previous, self._angles = self._angles, angles.copy()
        now = self._clock()
        if self._logged is None:  # the very first targets
            self._write("FIRST ", angles, now)
            self._logged = angles.copy()
            self._last_change = now
            return
        if np.max(np.abs(np.degrees(angles - previous))) > config.DRYRUN_LOG_EPS_DEG:
            self._last_change = now
            self._resting = False
            if now - self._last_line >= 1.0 / config.DRYRUN_LOG_HZ:
                self._write("MOVING", angles, now)
                self._logged = angles.copy()
        elif not self._resting and now - self._last_change >= config.DRYRUN_REST_S:
            self._resting = True
            self._write("REST  ", angles, now)  # the pose it settled in
            self._logged = angles.copy()

    def _write(self, label: str, angles: JointArray, now: float) -> None:
        self._last_line = now
        degrees = np.degrees(angles).reshape(len(config.LEG_NAMES), len(config.JOINTS_PER_LEG))
        legs = "  ".join(
            f"{leg}[{c:+6.1f} {f:+6.1f} {t:+6.1f}]"
            for leg, (c, f, t) in zip(config.LEG_NAMES, degrees, strict=True))
        self._log.info("t=%7.2f %s %s", now - self._t0, label, legs)

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
