"""Real-servo backend for the Raspberry Pi 5: one PCA9685 (I2C) plus two Pi hardware-PWM pins.

This file owns every servo-specific number (``docs/HARDWARE.md``): which driver and channel each
of the 18 joints is on, its centre pulse, direction, offset and pulse window. Nothing else in the
project knows which servo is which. ``config.py`` only holds the clean robot.

Wiring (owner built; one PCA9685 at 0x40 has 16 channels, 18 joints are needed):
  * PCA9685 channels 0-15: every joint except the two rear coxas.
  * RR_coxa -> Pi GPIO12 (hardware PWM0, pin 32); LR_coxa -> Pi GPIO13 (PWM1, pin 33).
    The GPIO pins carry the signal only; the servo power is the external supply.

The numbers below are PLACEHOLDERS taken from the MG995 datasheet range and the PiHexa18 reference
(500-2500 us over 180 deg, femur reversed). Measure each servo with ``scripts/servo_jog.py`` and
replace them before trusting the robot on the ground.

Safety: the base class clamps to the hard joint limits; this backend then limits the pulse to the
joint's window and slews each output at most ``config.SERVO_SLEW_DEG_S``. ``close()`` stops all PWM
(the servos go limp), so the physical kill switch stays the real emergency stop.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

import config
from body.backend import BasePose, HexapodBackend, JointArray

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JointCal:
    """One joint. ``driver``: ``"pca"`` (channel 0-15) or ``"gpio"`` (0 = GPIO12, 1 = GPIO13)."""

    driver: str
    channel: int
    center_us: float = 1500.0  # pulse at clean angle 0 (measure it; do not assume 1500)
    direction: int = 1  # +1 or -1: sign between the clean angle and the pulse
    offset_deg: float = 0.0  # trim added to the clean angle before converting
    min_us: float = 600.0  # safe pulse window (no stall against the frame)
    max_us: float = 2400.0

    def pulse_us(self, angle_deg: float) -> float:
        """Pulse for a clean-frame angle, clamped to this joint's window."""
        shifted = angle_deg + self.offset_deg
        pulse = self.center_us + self.direction * shifted * config.SERVO_US_PER_DEG
        low = max(self.min_us, config.SERVO_PULSE_ABS_MIN_US)
        high = min(self.max_us, config.SERVO_PULSE_ABS_MAX_US)
        return float(min(max(pulse, low), high))


def _default_cal() -> dict[str, JointCal]:
    """PCA channels in joint order, skipping the two rear coxas which sit on the Pi's PWM pins."""
    cal: dict[str, JointCal] = {}
    channel = 0
    gpio = {"RR_coxa": 0, "LR_coxa": 1}
    for name in config.JOINT_NAMES:
        direction = -1 if name.endswith("_femur") else 1  # PiHexa18: the femur servo is reversed
        if name.startswith("L") and not name.endswith("_coxa"):
            direction = -direction  # left servos are mirrored (LF sat opposite)
        if name in gpio:
            cal[name] = JointCal("gpio", gpio[name], direction=direction)
        else:
            cal[name] = JointCal("pca", channel, direction=direction)
            channel += 1
    return cal


CALIBRATION: dict[str, JointCal] = _default_cal()  # the one place for servo numbers


class PulseDriver(Protocol):
    def set_pulse_us(self, channel: int, pulse_us: float) -> None: ...

    def release(self) -> None: ...


class PCA9685:
    """Minimal PCA9685 driver over I2C (the same registers as the PiHexa18 reference)."""

    _MODE1, _PRESCALE, _LED0_ON_L = 0x00, 0xFE, 0x06

    def __init__(self, address: int = config.PCA9685_ADDRESS, bus: Any = None,
                 freq: float = config.SERVO_PWM_HZ,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if bus is None:
            try:
                from smbus2 import SMBus
            except ImportError as error:  # pragma: no cover - Pi only
                raise RuntimeError("smbus2 is not installed: pip install smbus2") from error
            bus = SMBus(config.PCA9685_I2C_BUS)
        self._bus = bus
        self._address = address
        self._freq = freq
        self._bus.write_byte_data(address, self._MODE1, 0x00)
        prescale = int(25_000_000.0 / 4096.0 / freq - 1.0 + 0.5)
        self._bus.write_byte_data(address, self._MODE1, 0x10)  # sleep, to set the prescaler
        self._bus.write_byte_data(address, self._PRESCALE, prescale)
        self._bus.write_byte_data(address, self._MODE1, 0x00)
        sleep(0.005)
        self._bus.write_byte_data(address, self._MODE1, 0xA0)  # restart + register auto-increment

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        if not 0 <= channel <= 15:
            raise ValueError(f"PCA9685 channel must be 0-15, got {channel}")
        off = int(pulse_us * 4096.0 * self._freq / 1_000_000.0)
        self._bus.write_i2c_block_data(
            self._address, self._LED0_ON_L + 4 * channel, [0, 0, off & 0xFF, off >> 8])

    def release(self) -> None:
        """All 16 channels fully off (no pulse): the servos go limp."""
        for channel in range(16):
            self._bus.write_i2c_block_data(
                self._address, self._LED0_ON_L + 4 * channel, [0, 0, 0, 0x10])

    def close(self) -> None:
        close = getattr(self._bus, "close", None)
        if close is not None:
            close()


class GpioPwm:
    """Pi 5 hardware PWM through sysfs: channel 0 = GPIO12, channel 1 = GPIO13.

    Needs ``dtoverlay=pwm-2chan`` in /boot/firmware/config.txt (owner edits, then reboots).
    """

    def __init__(self, chip: int = config.GPIO_PWM_CHIP, root: Path = Path("/sys/class/pwm"),
                 freq: float = config.SERVO_PWM_HZ) -> None:
        self._chip = root / f"pwmchip{chip}"
        self._period_ns = int(1e9 / freq)
        self._enabled: set[int] = set()
        if not self._chip.exists():
            raise RuntimeError(
                f"{self._chip} not found: add 'dtoverlay=pwm-2chan' to /boot/firmware/config.txt "
                "and reboot (GPIO12/GPIO13 hardware PWM)")

    def _pwm(self, channel: int) -> Path:
        return self._chip / f"pwm{channel}"

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        if channel not in (0, 1):
            raise ValueError(f"GPIO PWM channel must be 0 or 1, got {channel}")
        if channel not in self._enabled:
            if not self._pwm(channel).exists():
                (self._chip / "export").write_text(str(channel))
            (self._pwm(channel) / "period").write_text(str(self._period_ns))
            (self._pwm(channel) / "duty_cycle").write_text(str(int(pulse_us * 1000)))
            (self._pwm(channel) / "enable").write_text("1")
            self._enabled.add(channel)
        else:
            (self._pwm(channel) / "duty_cycle").write_text(str(int(pulse_us * 1000)))

    def release(self) -> None:
        for channel in sorted(self._enabled):
            (self._pwm(channel) / "enable").write_text("0")
        self._enabled.clear()


def make_drivers(
    cal: dict[str, JointCal] | None = None,
) -> dict[str, PulseDriver]:
    """Open only the drivers the table uses."""
    used = {c.driver for c in (cal or CALIBRATION).values()}
    drivers: dict[str, PulseDriver] = {}
    if "pca" in used:
        drivers["pca"] = PCA9685()
    if "gpio" in used:
        drivers["gpio"] = GpioPwm()
    return drivers


class ServoBackend(HexapodBackend):
    """Drives the 18 servos open loop: measured angles are the last (slewed) targets."""

    def __init__(
        self,
        drivers: dict[str, PulseDriver] | None = None,
        cal: dict[str, JointCal] | None = None,
        clock: Callable[[], float] = time.monotonic,
        pca_only: bool = False,
    ) -> None:
        """*pca_only* skips the joints on the Pi's own PWM pins (bench test before the overlay)."""
        super().__init__()
        self._cal = dict(cal if cal is not None else CALIBRATION)
        if tuple(self._cal) != config.JOINT_NAMES:
            raise ValueError("calibration must list the 18 joints in config.JOINT_NAMES order")
        if drivers is None:
            active = {n: c for n, c in self._cal.items() if not (pca_only and c.driver == "gpio")}
            drivers = make_drivers(active)
        self._drivers = drivers
        self._clock = clock
        self._angles: JointArray = np.zeros(config.DOF)  # what the servos are told (slewed)
        self._last = clock()
        self._first = True
        self.sim_time = 0.0
        logger.info("servo backend ready (%d joints)", config.DOF)

    def _apply(self, angles: JointArray) -> None:
        now = self._clock()
        if self._first:  # unknown start pose: begin from the centre, then slew to the target
            self._first = False
            dt = 0.0
        else:
            dt = max(now - self._last, 0.0)
        self._last = now
        max_step = np.radians(config.SERVO_SLEW_DEG_S) * max(dt, 1.0 / config.CONTROL_HZ)
        self._angles = self._angles + np.clip(angles - self._angles, -max_step, max_step)
        for name, angle in zip(config.JOINT_NAMES, np.degrees(self._angles), strict=True):
            joint = self._cal[name]
            if joint.driver not in self._drivers:  # skipped (pca_only)
                continue
            self._drivers[joint.driver].set_pulse_us(joint.channel, joint.pulse_us(float(angle)))

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
        for driver in self._drivers.values():
            driver.release()
            close = getattr(driver, "close", None)
            if close is not None:
                close()
        logger.info("servo backend closed: all PWM off")
