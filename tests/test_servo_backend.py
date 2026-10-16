"""Servo backend with fake drivers: calibration table, pulse maths, slew limit, release."""

from __future__ import annotations

import numpy as np
import pytest

import config
from body.servo_backend import (
    CALIBRATION,
    PCA9685,
    GpioPwm,
    JointCal,
    PulseDriver,
    ServoBackend,
)


class FakeDriver:
    def __init__(self) -> None:
        self.pulses: dict[int, float] = {}
        self.released = False

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        self.pulses[channel] = pulse_us

    def release(self) -> None:
        self.released = True


class FakeBus:
    def __init__(self) -> None:
        self.blocks: list[tuple[int, int, list[int]]] = []
        self.bytes: list[tuple[int, int, int]] = []

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.bytes.append((addr, reg, value))

    def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        self.blocks.append((addr, reg, data))


def make() -> tuple[ServoBackend, dict[str, PulseDriver], list[float]]:
    drivers: dict[str, PulseDriver] = {"pca": FakeDriver(), "gpio": FakeDriver()}
    now = [0.0]
    return ServoBackend(drivers, clock=lambda: now[0]), drivers, now


def test_table_covers_every_joint_once_without_clashes() -> None:
    assert tuple(CALIBRATION) == config.JOINT_NAMES
    slots = [(c.driver, c.channel) for c in CALIBRATION.values()]
    assert len(set(slots)) == config.DOF
    assert sorted(c.channel for c in CALIBRATION.values() if c.driver == "pca") == list(range(16))
    assert {n for n, c in CALIBRATION.items() if c.driver == "gpio"} == {"RR_coxa", "LR_coxa"}


def test_pulse_maths_and_windows() -> None:
    joint = JointCal("pca", 0, center_us=1500, direction=-1, min_us=900, max_us=2100)
    assert joint.pulse_us(0) == 1500
    assert joint.pulse_us(10) == pytest.approx(1500 - 10 * config.SERVO_US_PER_DEG)
    assert joint.pulse_us(90) == 900  # clamped to the joint window
    assert joint.pulse_us(-90) == 2100


def test_first_apply_centres_then_slews_not_jumps() -> None:
    backend, drivers, now = make()
    backend.set_joint_targets(np.full(config.DOF, np.radians(60.0)))
    first = drivers["pca"].pulses[0]  # type: ignore[attr-defined]
    step_us = config.SERVO_SLEW_DEG_S / config.CONTROL_HZ * config.SERVO_US_PER_DEG
    assert abs(first - 1500) <= step_us + 1
    for _ in range(100):
        now[0] += 0.02
        backend.set_joint_targets(np.full(config.DOF, np.radians(60.0)))
    assert backend.get_joint_angles()[0] == pytest.approx(np.radians(60.0))


def test_hard_limits_still_clamp_and_close_releases() -> None:
    backend, drivers, _ = make()
    applied = backend.set_joint_targets(np.full(config.DOF, 10.0))
    assert np.all(np.abs(applied) <= np.radians(90.0) + 1e-12)
    backend.close()
    assert all(d.released for d in drivers.values())  # type: ignore[attr-defined]


def test_pca9685_register_writes() -> None:
    bus = FakeBus()
    pca = PCA9685(0x40, bus=bus, sleep=lambda _s: None)
    assert (0x40, 0xFE, 121) in bus.bytes  # 50 Hz prescaler
    pca.set_pulse_us(3, 1500)
    assert bus.blocks[-1] == (0x40, 0x06 + 12, [0, 0, 307 & 0xFF, 307 >> 8])
    with pytest.raises(ValueError):
        pca.set_pulse_us(16, 1500)


def test_gpio_pwm_sysfs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    chip = tmp_path / "pwmchip2"
    (chip / "pwm0").mkdir(parents=True)
    pwm = GpioPwm(2, root=tmp_path)
    pwm.set_pulse_us(0, 1500)
    assert (chip / "pwm0" / "duty_cycle").read_text() == "1500000"
    assert (chip / "pwm0" / "period").read_text() == "20000000"
    pwm.release()
    assert (chip / "pwm0" / "enable").read_text() == "0"
    with pytest.raises(RuntimeError):
        GpioPwm(5, root=tmp_path)
