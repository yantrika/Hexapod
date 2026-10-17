"""The raw drivers with a fake I2C bus, a fake sysfs tree and a fake gpiod line."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import config
from body.servo_drivers import (
    PCA9685,
    GpiodOutputEnable,
    GpioPwm,
    build_drivers,
    check_pulse,
    release_quietly,
)


class FakeBus:
    def __init__(self) -> None:
        self.blocks: list[tuple[int, int, list[int]]] = []
        self.bytes: list[tuple[int, int, int]] = []
        self.closed = False

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.bytes.append((addr, reg, value))

    def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        self.blocks.append((addr, reg, data))

    def close(self) -> None:
        self.closed = True


def make_pca() -> tuple[PCA9685, FakeBus]:
    bus = FakeBus()
    return PCA9685(0x40, bus=bus, sleep=lambda _s: None), bus


def test_the_pca_starts_with_every_channel_fully_off_before_anything_else() -> None:
    _pca, bus = make_pca()
    assert bus.bytes[0] == (0x40, 0xFD, 0x10)  # ALL_LED_OFF_H = full off, first write
    assert (0x40, 0xFE, 121) in bus.bytes  # 50 Hz prescaler
    assert bus.bytes[-1] == (0x40, 0x00, 0xA0)  # restart + auto-increment, last


def test_a_pulse_writes_the_four_registers_of_its_channel() -> None:
    pca, bus = make_pca()
    pca.set_pulse_us(3, 1500)
    assert bus.blocks[-1] == (0x40, 0x06 + 12, [0, 0, 307 & 0xFF, 307 >> 8])
    pca.set_pulse_us(0, 2550)
    assert bus.blocks[-1][2] == [0, 0, 522 & 0xFF, 522 >> 8]


def test_release_is_the_full_off_bit_on_all_channels_and_on_one_channel() -> None:
    pca, bus = make_pca()
    bus.bytes.clear()
    pca.release()
    assert bus.bytes == [(0x40, 0xFD, 0x10)]  # one write: ALL_LED_OFF_H bit 4
    pca.release_channel(5)
    assert bus.blocks[-1] == (0x40, 0x06 + 20, [0, 0, 0, 0x10])  # LED5_OFF_H bit 4


def test_the_pca_rejects_bad_channels_and_pulses() -> None:
    pca, bus = make_pca()
    for channel in (-1, 16):
        with pytest.raises(ValueError):
            pca.set_pulse_us(channel, 1500)
        with pytest.raises(ValueError):
            pca.release_channel(channel)
    before = len(bus.blocks)
    for pulse in (0.0, 449.9, 2550.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            pca.set_pulse_us(0, pulse)
    assert len(bus.blocks) == before  # nothing was written


def test_a_failing_pca_start_closes_the_bus() -> None:
    class Broken(FakeBus):
        def write_byte_data(self, addr: int, reg: int, value: int) -> None:
            raise OSError("no device")

    bus = Broken()
    with pytest.raises(OSError):
        PCA9685(0x40, bus=bus, sleep=lambda _s: None)
    assert bus.closed


def make_sysfs(tmp_path: Path, exported: tuple[int, ...] = ()) -> Path:
    chip = tmp_path / "pwmchip2"
    chip.mkdir()
    for channel in exported:
        (chip / f"pwm{channel}").mkdir()
    return chip


def test_gpio_pwm_exports_sets_period_duty_and_enables(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    chip = make_sysfs(tmp_path)
    real_write = GpioPwm._write

    def kernel_like_write(path: Path, text: str) -> None:
        real_write(path, text)
        if path.name == "export":  # the kernel creates pwmN after an export
            (path.parent / f"pwm{text}").mkdir()

    monkeypatch.setattr(GpioPwm, "_write", staticmethod(kernel_like_write))
    pwm = GpioPwm(2, root=tmp_path)
    pwm.set_pulse_us(1, 1500)
    assert (chip / "export").read_text() == "1"
    assert (chip / "pwm1" / "period").read_text() == "20000000"  # 50 Hz
    assert (chip / "pwm1" / "duty_cycle").read_text() == "1500000"
    assert (chip / "pwm1" / "enable").read_text() == "1"
    pwm.set_pulse_us(1, 1600)
    assert (chip / "pwm1" / "duty_cycle").read_text() == "1600000"
    pwm.release_channel(1)
    assert (chip / "pwm1" / "enable").read_text() == "0"
    pwm.set_pulse_us(1, 1500)  # re-attach after a release
    assert (chip / "pwm1" / "enable").read_text() == "1"
    pwm.release()
    assert (chip / "pwm1" / "enable").read_text() == "0"


def test_gpio_pwm_switches_off_pwm_left_running_by_an_earlier_process(tmp_path: Path) -> None:
    chip = make_sysfs(tmp_path, exported=(0, 1))
    (chip / "pwm0" / "enable").write_text("1")
    GpioPwm(2, root=tmp_path)
    assert (chip / "pwm0" / "enable").read_text() == "0"


def test_gpio_pwm_errors(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="pwm-2chan"):
        GpioPwm(5, root=tmp_path)
    make_sysfs(tmp_path, exported=(0,))
    pwm = GpioPwm(2, root=tmp_path)
    with pytest.raises(ValueError):
        pwm.set_pulse_us(2, 1500)
    with pytest.raises(ValueError):
        pwm.set_pulse_us(0, 100)


def test_gpio_pwm_permission_error_points_to_the_udev_rule(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    make_sysfs(tmp_path, exported=(0,))
    pwm = GpioPwm(2, root=tmp_path)

    def deny(self: Path, text: str) -> int:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "write_text", deny)
    with pytest.raises(RuntimeError, match="udev"):
        pwm.set_pulse_us(0, 1500)


class FakeLines:
    def __init__(self) -> None:
        self.values: list[tuple[int, bool]] = []
        self.released = False

    def set_value(self, gpio: int, value: bool) -> None:
        self.values.append((gpio, value))

    def release(self) -> None:
        self.released = True


def test_output_enable_drives_the_line_low_to_enable_and_high_to_disable() -> None:
    lines = FakeLines()
    oe = GpiodOutputEnable(17, request=lines)
    oe.enable()
    oe.disable()
    oe.close()
    assert lines.values == [(17, False), (17, True)]  # OE is active low
    assert lines.released


def test_check_pulse_bounds() -> None:
    check_pulse(config.SERVO_PULSE_ABS_MIN_US)
    check_pulse(config.SERVO_PULSE_ABS_MAX_US)
    with pytest.raises(ValueError):
        check_pulse(config.SERVO_PULSE_ABS_MAX_US + 1)


class Recorder:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        pass

    def release_channel(self, channel: int) -> None:
        pass

    def release(self) -> None:
        self.log.append(f"release {self.name}")


class FakeOe:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def enable(self) -> None:
        self.log.append("oe enable")

    def disable(self) -> None:
        self.log.append("oe disable")

    def close(self) -> None:
        self.log.append("oe close")


def test_a_driver_that_fails_to_open_releases_the_ones_already_open() -> None:
    log: list[str] = []

    def broken_gpio() -> Any:
        raise RuntimeError("no pwmchip")

    with pytest.raises(RuntimeError, match="pwmchip"):
        build_drivers({"pca", "gpio"}, 17, pca_factory=lambda: Recorder(log, "pca"),
                      gpio_factory=broken_gpio, oe_factory=lambda _g: FakeOe(log))
    assert log == ["oe disable", "release pca", "oe close"]  # everything that was created


def test_only_the_drivers_in_use_are_opened_and_oe_needs_the_pca() -> None:
    log: list[str] = []
    drivers, oe = build_drivers({"gpio"}, 17, gpio_factory=lambda: Recorder(log, "gpio"),
                                oe_factory=lambda _g: FakeOe(log))
    assert list(drivers) == ["gpio"] and oe is None
    drivers, oe = build_drivers({"pca"}, None, pca_factory=lambda: Recorder(log, "pca"))
    assert list(drivers) == ["pca"] and oe is None


def test_release_quietly_goes_on_after_a_failure() -> None:
    log: list[str] = []

    class Bad(Recorder):
        def release(self) -> None:
            raise OSError("bus gone")

    release_quietly({"pca": Bad(log, "pca"), "gpio": Recorder(log, "gpio")}, FakeOe(log))
    assert log == ["oe disable", "release gpio", "oe close"]
