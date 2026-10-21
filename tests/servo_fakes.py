"""Test doubles for the servo backend: a world clock, recording drivers, a calibrated table."""

from __future__ import annotations

import json
from typing import Any

import config
from body import servo_calibration as sc
from body.servo_calibration import Calibration


def good_data(calibrated: bool = True) -> dict[str, Any]:
    """A valid calibration file body: the committed layout, every joint (un)calibrated."""
    data = json.loads(config.SERVO_CAL_FILE.read_text())
    for joint in data["joints"].values():
        joint["calibrated"] = calibrated
        joint["measured"] = list(sc.MEASURED_FIELDS) if calibrated else []
        joint["calibrated_on"] = "2026-10-17" if calibrated else None
        if calibrated:  # a measured joint has a real window, wide enough for the sit pose
            joint["min_us"], joint["max_us"] = 600, 2400
    return data


def calibrated() -> Calibration:
    return sc.parse(good_data(True))


class World:
    """A fake clock that ``sleep`` advances, and the log of everything the drivers received."""

    def __init__(self) -> None:
        self.time = 1000.0
        self.events: list[tuple[float, str, str, int, float]] = []  # t, driver, kind, ch, us

    def now(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.time += max(0.0, seconds)

    def tick(self, seconds: float = 0.02) -> None:
        self.time += seconds

    def writes(self, driver: str, channel: int) -> list[tuple[float, float]]:
        return [(t, us) for t, d, kind, ch, us in self.events
                if d == driver and ch == channel and kind == "pulse"]

    def kinds(self, kind: str) -> list[tuple[float, str, int]]:
        return [(t, d, ch) for t, d, k, ch, _ in self.events if k == kind]


class FakeDriver:
    def __init__(self, world: World, name: str) -> None:
        self.world, self.name = world, name
        self.pulses: dict[int, float] = {}
        self.releases = 0
        self.fail_release = False

    def set_pulse_us(self, channel: int, pulse_us: float) -> None:
        self.pulses[channel] = pulse_us
        self.world.events.append((self.world.now(), self.name, "pulse", channel, pulse_us))

    def release_channel(self, channel: int) -> None:
        self.pulses.pop(channel, None)
        self.world.events.append((self.world.now(), self.name, "release_channel", channel, 0.0))

    def release(self) -> None:
        self.releases += 1
        self.pulses.clear()
        self.world.events.append((self.world.now(), self.name, "release", -1, 0.0))
        if self.fail_release:
            raise OSError("bus gone")


class FakeOe:
    def __init__(self, world: World) -> None:
        self.world = world
        self.log: list[str] = []

    def enable(self) -> None:
        self.log.append("enable")
        self.world.events.append((self.world.now(), "oe", "enable", -1, 0.0))

    def disable(self) -> None:
        self.log.append("disable")
        self.world.events.append((self.world.now(), "oe", "disable", -1, 0.0))

    def close(self) -> None:
        self.log.append("close")
