"""Real-servo backend for the Raspberry Pi 5: one PCA9685 (I2C) plus two Pi hardware-PWM pins.

Wiring (owner built; docs/HARDWARE.md): one PCA9685 at 0x40 carries 16 joints; the two rear coxas
RR_coxa and LR_coxa sit on Pi GPIO12 (hardware PWM0, pin 32) and GPIO13 (PWM1, pin 33). The servo
power is an external supply; the Pi pins carry the signal only.

Every per-joint number comes from the calibration file (``body/servo_calibration.py``); the
backend refuses to start without it and refuses to drive a joint that is not calibrated. The
single path to a servo is:

    Controller -> HexapodBackend.set_joint_targets (hard angle limits) -> ServoBackend._apply
    -> per-joint pulse window -> slew limit (``config.SERVO_SLEW_DEG_S``, wall-clock) -> driver

Nothing writes a pulse around it: ``body/servo_drivers.py`` is private to this module and the
scripts (also the calibration tools) use ``ServoBackend`` only (``tests/test_servo_paths.py``).

Safety behaviour:
  * Outputs start released. The first command attaches the servos one LEG at a time
    (``config.SERVO_STAGGER_S`` apart), each at its calibrated centre (clean angle 0), before any
    motion; then the first pose, sit, is reached by the slew limit.
  * Sitting with no command for ``config.SERVO_IDLE_RELEASE_S`` releases every channel (no stall
    heating); the next command that moves anything re-attaches, staggered, at the held sit pose.
  * ``close()`` releases everything (the servos go limp). A signal shutdown sits first
    (``body/servo_safety.py``). SIGKILL and power loss cannot be handled in software: the PCA9685
    and the kernel PWM keep their last pulse. The optional OE line (``config.PCA9685_OE_GPIO``)
    covers the PCA9685 channels in that case (docs/HARDWARE.md); the physical kill switch stays
    the real emergency stop.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Collection
from typing import Final

import numpy as np

import config
from body import poses, servo_calibration
from body.backend import BasePose, HexapodBackend, JointArray
from body.servo_calibration import Calibration, CalibrationError
from body.servo_drivers import (
    OutputEnable,
    PulseDriver,
    build_drivers,
    check_pulse,
    release_quietly,
)

logger = logging.getLogger(__name__)

_CHANGE_RAD: Final = 1e-3  # a requested pose that moved less than this is "no new command"
_ATTACHED_TOLERANCE_DEG: Final = 0.5  # a re-attach is due when the target is further than this


class ServoBackend(HexapodBackend):
    """Drives the 18 servos (or the selected ones) open loop with a slew limit."""

    is_physical = True

    def __init__(
        self,
        calibration: Calibration | None = None,
        *,
        joints: Collection[str] | None = None,
        allow_uncalibrated: bool = False,
        calibration_joint: str | None = None,
        drivers: dict[str, PulseDriver] | None = None,
        output_enable: OutputEnable | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        stagger_s: float = config.SERVO_STAGGER_S,
        idle_release_s: float | None = config.SERVO_IDLE_RELEASE_S,
    ) -> None:
        """*joints* drives only those joints (the others stay released); None means all 18.

        *allow_uncalibrated* (calibration tools only) needs *joints* and lets a joint be driven
        before it is calibrated. *calibration_joint* is the one-joint calibration mode used by
        ``scripts/servo_jog.py``: only ``set_raw_pulse`` moves it, from its centre.
        """
        super().__init__()
        self._calibration = (calibration if calibration is not None
                             else servo_calibration.load())
        self._names = self._choose_joints(joints, calibration_joint, allow_uncalibrated)
        self._calibration_joint = calibration_joint
        if calibration_joint is None and not allow_uncalibrated:
            self._calibration.require_calibrated(self._names)
        self._joint = [self._calibration[name] for name in config.JOINT_NAMES]
        self._active = [i for i, name in enumerate(config.JOINT_NAMES) if name in self._names]
        self._clock = clock
        self._sleep = sleep
        self._stagger_s = stagger_s
        self._idle_release_s = None if calibration_joint else idle_release_s
        self._closed = False

        self._drivers: dict[str, PulseDriver]
        self._oe: OutputEnable | None
        if drivers is None:
            used = {self._joint[i].driver for i in self._active}
            self._drivers, self._oe = build_drivers(used, config.PCA9685_OE_GPIO)
        else:
            self._drivers, self._oe = drivers, output_enable
        try:
            self._start_state(calibration_joint)
            if self._oe is not None:
                self._oe.enable()  # every channel is still off, so nothing moves yet
        except BaseException:
            release_quietly(self._drivers, self._oe)
            raise
        logger.info("servo backend ready (%d of %d joints, released)", len(self._active),
                    config.DOF)

    def _choose_joints(self, joints: Collection[str] | None, calibration_joint: str | None,
                       allow_uncalibrated: bool) -> tuple[str, ...]:
        if calibration_joint is not None:
            if calibration_joint not in config.JOINT_NAMES:
                raise ValueError(f"unknown joint {calibration_joint!r}")
            if joints is not None and set(joints) != {calibration_joint}:
                raise ValueError("calibration mode drives exactly the calibration joint")
            return (calibration_joint,)
        if joints is None:
            if allow_uncalibrated:
                raise CalibrationError("driving uncalibrated joints needs an explicit joint list")
            return config.JOINT_NAMES
        unknown = sorted(set(joints) - set(config.JOINT_NAMES))
        if unknown or not joints:
            raise ValueError(f"joints must be a non-empty subset of the 18 names (unknown: "
                             f"{unknown})")
        return tuple(name for name in config.JOINT_NAMES if name in set(joints))

    def _start_state(self, calibration_joint: str | None) -> None:
        # Unknown start pose: the first attach puts every servo at its calibrated centre.
        self._pulse = np.array([joint.pulse_us(0.0) for joint in self._joint])
        if calibration_joint is not None:
            index = config.JOINT_NAMES.index(calibration_joint)
            self._pulse[index] = self._joint[index].centre_us
        self._target = self._pulse.copy()
        self._written = np.full(config.DOF, np.nan)
        self._requested: JointArray = np.zeros(config.DOF)
        self._released = True
        self._ever_attached = False
        now = self._clock()
        self._last_step = now
        self._last_change = now
        self.sim_time = 0.0

    # --- properties used by tools and tests ---------------------------------------------------
    @property
    def released(self) -> bool:
        return self._released

    @property
    def joint_names(self) -> tuple[str, ...]:
        return self._names

    def pulse_of(self, name: str) -> float:
        """The pulse the joint is being told right now (before it is attached: its start)."""
        return float(self._pulse[config.JOINT_NAMES.index(name)])

    # --- HexapodBackend ------------------------------------------------------------------------
    def _apply(self, angles: JointArray) -> None:
        if self._calibration_joint is not None:
            raise RuntimeError("calibration mode moves its joint with set_raw_pulse only")
        now = self._clock()
        for i in self._active:
            self._target[i] = self._joint[i].pulse_us(math.degrees(float(angles[i])))
        if float(np.max(np.abs(angles - self._requested))) > _CHANGE_RAD:
            self._last_change = now
            self._requested = angles.copy()
        self._update(now)

    def get_joint_angles(self) -> JointArray:
        angles = np.zeros(config.DOF)
        for i in self._active:
            angles[i] = math.radians(self._joint[i].angle_deg(float(self._pulse[i])))
        return angles

    def get_joint_velocities(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), np.zeros(3))

    def advance(self, dt: float) -> float:
        self.sim_time += dt
        return dt

    # --- calibration mode ----------------------------------------------------------------------
    def set_raw_pulse(self, name: str, pulse_us: float) -> None:
        """Calibration mode only: aim the joint at a raw pulse (slewed like everything else)."""
        if self._calibration_joint is None or name != self._calibration_joint:
            raise RuntimeError("set_raw_pulse needs calibration mode for that joint")
        check_pulse(pulse_us)
        self._target[config.JOINT_NAMES.index(name)] = pulse_us
        self._update(self._clock())

    def refresh(self) -> None:
        """Advance the slew towards the current targets (the calibration tools call this)."""
        self._update(self._clock())

    # --- attach, slew, release -----------------------------------------------------------------
    def attach(self) -> None:
        """Enable the servos one leg at a time, each at its current pulse; no-op when attached."""
        if self._closed:
            raise RuntimeError("servo backend is closed")
        if not self._released:
            return
        legs = [leg for leg in config.LEG_NAMES
                if any(config.JOINT_NAMES[i].startswith(f"{leg}_") for i in self._active)]
        for position, leg in enumerate(legs):
            if position:
                self._sleep(self._stagger_s)
            for i in self._active:
                if config.JOINT_NAMES[i].startswith(f"{leg}_"):
                    self._write(i, float(self._pulse[i]))
        self._released = False
        self._ever_attached = True
        now = self._clock()
        self._last_step = now  # slewing starts now: nothing moved while attaching
        self._last_change = now
        logger.info("servos attached (%d legs, %.2f s apart)", len(legs), self._stagger_s)

    def _wants_attach(self) -> bool:
        if not self._ever_attached:
            return True
        gap = np.abs(self._target - self._pulse)[self._active]
        return bool(np.max(gap) > _ATTACHED_TOLERANCE_DEG * config.SERVO_US_PER_DEG)

    def _update(self, now: float) -> None:
        if self._closed:
            raise RuntimeError("servo backend is closed")
        if self._released:
            if not self._wants_attach():
                self._last_step = now
                return
            self.attach()
            now = self._clock()
        # Never faster than SERVO_SLEW_DEG_S in wall time: the step is the rate times the time
        # that really passed (capped, so a stalled loop cannot make one huge jump).
        elapsed = min(max(now - self._last_step, 0.0), config.SERVO_SLEW_MAX_DT_S)
        self._last_step = now
        max_step = config.SERVO_SLEW_DEG_S * config.SERVO_US_PER_DEG * elapsed
        for i in self._active:
            step = min(max(float(self._target[i] - self._pulse[i]), -max_step), max_step)
            self._pulse[i] += step
            if self._written[i] != self._pulse[i]:  # also true while still NaN (not written yet)
                self._write(i, float(self._pulse[i]))
        self._maybe_release_idle(now)

    def _write(self, index: int, pulse_us: float) -> None:
        joint = self._joint[index]
        self._drivers[joint.driver].set_pulse_us(joint.channel, pulse_us)
        self._written[index] = pulse_us

    def _maybe_release_idle(self, now: float) -> None:
        if self._idle_release_s is None or now - self._last_change < self._idle_release_s:
            return
        if float(np.max(np.abs(self._target - self._pulse)[self._active])) > 1e-6:
            return  # still moving
        sit = poses.SIT_ANGLES.reshape(-1)
        away = np.abs(self._requested - sit)[self._active]
        if float(np.max(away)) > math.radians(config.SERVO_SIT_TOLERANCE_DEG):
            return  # only a sitting robot is let go: a standing one would fall
        logger.info("idle for %.0f s while sitting: releasing every channel", self._idle_release_s)
        self.release_outputs()

    def release_outputs(self) -> None:
        """Stop every pulse (the servos go limp). The next command re-attaches, staggered."""
        failures: list[Exception] = []
        for name, driver in self._drivers.items():
            try:
                driver.release()
            except Exception as error:
                logger.exception("releasing %s failed", name)
                failures.append(error)
        self._written[:] = np.nan
        self._released = True
        if failures:
            raise RuntimeError(f"{len(failures)} driver(s) failed to release") from failures[0]

    def close(self) -> None:
        """Release everything, OE first (the quickest way to limp), and never raise."""
        if self._closed:
            return
        self._closed = True
        self._released = True
        release_quietly(self._drivers, self._oe)
        logger.info("servo backend closed: all PWM off")
