"""The raw pulse drivers of the servo backend: PCA9685 over I2C, the Pi's two hardware-PWM pins
(sysfs) and the optional PCA9685 output-enable (OE) line (libgpiod).

Only ``body/servo_backend.py`` (and its tests) may import this module: it writes pulses with NO
calibration, slew or hard-limit layer above it (``tests/test_servo_paths.py`` enforces that no
script does). Each driver still refuses a pulse outside the absolute window
``config.SERVO_PULSE_ABS_MIN_US`` .. ``SERVO_PULSE_ABS_MAX_US``.

Safe state: after a restart or a killed process the PCA9685 keeps its last pulses and the kernel
keeps the last PWM duty, so every driver puts its outputs OFF when it is created.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import config

logger = logging.getLogger(__name__)


class PulseDriver(Protocol):
    def set_pulse_us(self, channel: int, pulse_us: float) -> None: ...

    def release_channel(self, channel: int) -> None: ...

    def release(self) -> None: ...


class OutputEnable(Protocol):
    """The PCA9685 OE pin (active low): ``enable`` drives it low, ``disable`` lets it go high."""

    def enable(self) -> None: ...

    def disable(self) -> None: ...

    def close(self) -> None: ...


def check_pulse(pulse_us: float) -> None:
    """Raise ``ValueError`` for NaN, infinity or a pulse outside the absolute window."""
    if not (math.isfinite(pulse_us)
            and config.SERVO_PULSE_ABS_MIN_US <= pulse_us <= config.SERVO_PULSE_ABS_MAX_US):
        raise ValueError(
            f"pulse {pulse_us} us is outside {config.SERVO_PULSE_ABS_MIN_US}.."
            f"{config.SERVO_PULSE_ABS_MAX_US} us")


class PCA9685:
    """Minimal PCA9685 driver (the same registers as the PiHexa18 reference)."""

    _MODE1, _PRESCALE, _LED0_ON_L = 0x00, 0xFE, 0x06
    _ALL_LED_OFF_H = 0xFD
    _FULL_OFF = 0x10  # bit 4 of LEDn_OFF_H / ALL_LED_OFF_H: the output is fully off

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
        try:
            # First of all: every channel fully off, so pulses left by an earlier (killed)
            # process never come back when the oscillator wakes up.
            self.release()
            prescale = int(25_000_000.0 / 4096.0 / freq - 1.0 + 0.5)
            self._bus.write_byte_data(address, self._MODE1, 0x10)  # sleep, to set the prescaler
            self._bus.write_byte_data(address, self._PRESCALE, prescale)
            self._bus.write_byte_data(address, self._MODE1, 0x00)
            sleep(0.005)
            self._bus.write_byte_data(address, self._MODE1, 0xA0)  # restart + auto-increment
        except BaseException:
            self.close()
            raise

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        if not 0 <= channel <= 15:
            raise ValueError(f"PCA9685 channel must be 0-15, got {channel}")
        check_pulse(pulse_us)
        off = int(pulse_us * 4096.0 * self._freq / 1_000_000.0)
        self._bus.write_i2c_block_data(
            self._address, self._LED0_ON_L + 4 * channel, [0, 0, off & 0xFF, off >> 8])

    def release_channel(self, channel: int) -> None:
        """One channel fully off (no pulse): that servo goes limp."""
        if not 0 <= channel <= 15:
            raise ValueError(f"PCA9685 channel must be 0-15, got {channel}")
        self._bus.write_i2c_block_data(
            self._address, self._LED0_ON_L + 4 * channel, [0, 0, 0, self._FULL_OFF])

    def release(self) -> None:
        """All 16 channels fully off with one register write: every servo goes limp."""
        self._bus.write_byte_data(self._address, self._ALL_LED_OFF_H, self._FULL_OFF)

    def close(self) -> None:
        close = getattr(self._bus, "close", None)
        if close is not None:
            close()


class GpioPwm:
    """Pi 5 hardware PWM through sysfs: channel 0 = GPIO12, channel 1 = GPIO13.

    Needs ``dtoverlay=pwm-2chan`` in /boot/firmware/config.txt and a udev rule that lets the
    ``hexa`` user write /sys/class/pwm (``docs/HARDWARE.md``; both are owner-run).
    """

    def __init__(self, chip: int = config.GPIO_PWM_CHIP, root: Path = Path("/sys/class/pwm"),
                 freq: float = config.SERVO_PWM_HZ) -> None:
        self._chip = root / f"pwmchip{chip}"
        self._period_ns = int(1e9 / freq)
        self._enabled: set[int] = set()
        if not self._chip.exists():
            raise RuntimeError(
                f"{self._chip} not found: add 'dtoverlay=pwm-2chan' to /boot/firmware/config.txt "
                "and reboot (GPIO12/GPIO13 hardware PWM; docs/HARDWARE.md)")
        for channel in (0, 1):  # a killed process leaves its PWM running: start from "off"
            if self._pwm(channel).exists():
                self._write(self._pwm(channel) / "enable", "0")

    def _pwm(self, channel: int) -> Path:
        return self._chip / f"pwm{channel}"

    @staticmethod
    def _write(path: Path, text: str) -> None:
        try:
            path.write_text(text)
        except PermissionError as error:
            raise RuntimeError(
                f"no permission to write {path}: install the udev rule (docs/HARDWARE.md)"
            ) from error

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        if channel not in (0, 1):
            raise ValueError(f"GPIO PWM channel must be 0 or 1, got {channel}")
        check_pulse(pulse_us)
        duty = str(int(pulse_us * 1000))
        if channel not in self._enabled:
            if not self._pwm(channel).exists():
                self._write(self._chip / "export", str(channel))
            self._write(self._pwm(channel) / "period", str(self._period_ns))
            self._write(self._pwm(channel) / "duty_cycle", duty)
            self._write(self._pwm(channel) / "enable", "1")
            self._enabled.add(channel)
        else:
            self._write(self._pwm(channel) / "duty_cycle", duty)

    def release_channel(self, channel: int) -> None:
        if channel not in (0, 1):
            raise ValueError(f"GPIO PWM channel must be 0 or 1, got {channel}")
        if channel in self._enabled:
            self._write(self._pwm(channel) / "enable", "0")
            self._enabled.discard(channel)

    def release(self) -> None:
        for channel in sorted(self._enabled):
            self.release_channel(channel)


class GpiodOutputEnable:
    """The PCA9685 OE line on a Pi GPIO, through libgpiod (the ``gpiod`` v2 Python package).

    The line is requested as an output driven HIGH (outputs disabled). ``enable`` drives it low.
    When the process dies for any reason the kernel releases the line, the external 10k pull-up
    to 3.3 V takes it high and the PCA9685 outputs go off: this is the only protection that
    survives ``kill -9`` (``docs/HARDWARE.md`` tests it).
    """

    def __init__(self, gpio: int, chip_path: str = config.GPIO_CHIP_PATH,
                 request: Any = None) -> None:
        self._gpio = gpio
        if request is None:
            try:
                import gpiod
                from gpiod.line import Direction, Value
            except ImportError as error:  # pragma: no cover - Pi only
                raise RuntimeError(
                    "gpiod is not installed (requirements-pi-oe.txt): needed because "
                    "config.PCA9685_OE_GPIO is set") from error
            request = gpiod.request_lines(
                chip_path, consumer="hexa-oe",
                config={gpio: gpiod.LineSettings(direction=Direction.OUTPUT,
                                                 output_value=Value.ACTIVE)})
            self._high, self._low = Value.ACTIVE, Value.INACTIVE
        else:
            self._high, self._low = True, False
        self._request: Any = request

    def enable(self) -> None:
        self._request.set_value(self._gpio, self._low)

    def disable(self) -> None:
        self._request.set_value(self._gpio, self._high)

    def close(self) -> None:
        release = getattr(self._request, "release", None)
        if release is not None:
            release()


def build_drivers(
    used: set[str],
    output_enable_gpio: int | None = None,
    *,
    pca_factory: Callable[[], PulseDriver] = PCA9685,
    gpio_factory: Callable[[], PulseDriver] = GpioPwm,
    oe_factory: Callable[[int], OutputEnable] = GpiodOutputEnable,
) -> tuple[dict[str, PulseDriver], OutputEnable | None]:
    """Open only the drivers *used* (``"pca"``, ``"gpio"``), plus OE if its GPIO is configured.

    If one of them fails to open, everything opened so far is released before the error goes on.
    """
    drivers: dict[str, PulseDriver] = {}
    output_enable: OutputEnable | None = None
    try:
        if output_enable_gpio is not None and "pca" in used:
            output_enable = oe_factory(output_enable_gpio)  # starts disabled (high)
        if "pca" in used:
            drivers["pca"] = pca_factory()
        if "gpio" in used:
            drivers["gpio"] = gpio_factory()
    except BaseException:
        release_quietly(drivers, output_enable)
        raise
    return drivers, output_enable


def release_quietly(drivers: dict[str, PulseDriver], output_enable: OutputEnable | None) -> None:
    """Disable OE, then release every driver; one failure never stops the others."""
    if output_enable is not None:
        _try(output_enable.disable, "OE disable")
    for name, driver in drivers.items():
        _try(driver.release, f"{name} release")
        close = getattr(driver, "close", None)
        if close is not None:
            _try(close, f"{name} close")
    if output_enable is not None:
        _try(output_enable.close, "OE close")


def _try(action: Callable[[], object], label: str) -> None:
    try:
        action()
    except Exception:
        logger.exception("servo shutdown: %s failed", label)
