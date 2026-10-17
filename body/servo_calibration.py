"""The per-joint servo calibration file (``data/calibration/servo_cal.json``).

Pure data and validation: no hardware, no I/O beyond reading and writing that one file. Every
servo-specific number of a joint lives here and nowhere else: which output drives it (a PCA9685
channel or one of the two Pi hardware-PWM pins), the pulse at the centre, the direction sign, the
trim offset and the safe pulse window. ``config.py`` keeps only board-level numbers and the clean
joint frame has no offsets (``docs/ARCHITECTURE.md``).

The file is committed and dated. ``calibrated`` is false until the joint was measured on the bench
(``scripts/servo_jog.py``); ``ServoBackend`` refuses to drive a joint that is not calibrated.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import config

DRIVERS = ("pca", "gpio")
MEASURED_FIELDS = ("centre", "sign", "min", "max")  # all four are needed to call a joint calibrated
FORMAT_VERSION = 1
_PLACEHOLDER_HALF_WINDOW_US = 400.0  # window kept around a centre until min and max are measured


class CalibrationError(ValueError):
    """The calibration file is missing, malformed, inconsistent or not calibrated enough."""


@dataclass(frozen=True)
class JointCal:
    """One joint. ``driver``: ``"pca"`` (channel 0-15) or ``"gpio"`` (0 = GPIO12, 1 = GPIO13)."""

    driver: str
    channel: int
    centre_us: float = 1500.0  # pulse at servo mid travel (measure it; do not assume 1500)
    sign: int = 1  # +1 or -1: sign between the clean angle and the pulse (explicit, per joint)
    offset_deg: float = 0.0  # trim added to the clean angle before converting
    min_us: float = 1000.0  # safe pulse window (no stall against the frame)
    max_us: float = 2000.0
    calibrated: bool = False
    measured: tuple[str, ...] = field(default_factory=tuple)  # which of MEASURED_FIELDS were noted
    calibrated_on: str | None = None

    @property
    def output(self) -> tuple[str, int]:
        return (self.driver, self.channel)

    def window_us(self) -> tuple[float, float]:
        """The pulse window actually allowed: this joint's window inside the absolute one."""
        return (max(self.min_us, float(config.SERVO_PULSE_ABS_MIN_US)),
                min(self.max_us, float(config.SERVO_PULSE_ABS_MAX_US)))

    def pulse_us(self, angle_deg: float) -> float:
        """Pulse for a clean-frame angle, clamped to this joint's window."""
        pulse = self.centre_us + self.sign * (angle_deg + self.offset_deg) * config.SERVO_US_PER_DEG
        low, high = self.window_us()
        return float(min(max(pulse, low), high))

    def angle_deg(self, pulse_us: float) -> float:
        """The clean-frame angle a pulse stands for (the inverse of ``pulse_us``)."""
        return self.sign * (pulse_us - self.centre_us) / config.SERVO_US_PER_DEG - self.offset_deg


@dataclass(frozen=True)
class Calibration:
    """All 18 joints in ``config.JOINT_NAMES`` order, plus the file's date and note."""

    joints: Mapping[str, JointCal]
    date: str = ""
    note: str = ""

    def __getitem__(self, name: str) -> JointCal:
        return self.joints[name]

    def uncalibrated(self, names: Iterable[str] | None = None) -> list[str]:
        chosen = config.JOINT_NAMES if names is None else tuple(names)
        return [name for name in chosen if not self.joints[name].calibrated]

    def require_calibrated(self, names: Iterable[str] | None = None) -> None:
        """Raise ``CalibrationError`` unless every joint in *names* (default all) is calibrated."""
        missing = self.uncalibrated(names)
        if missing:
            raise CalibrationError(
                f"{len(missing)} joint(s) not calibrated: {', '.join(missing)}. "
                "Calibrate them with scripts/servo_jog.py (docs/HARDWARE.md).")

    def with_joint(self, name: str, joint: JointCal, date: str | None = None) -> Calibration:
        joints = dict(self.joints)
        joints[name] = joint
        return Calibration(joints, self.date if date is None else date, self.note)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CalibrationError(f"{label} must be a finite number, got {value!r}")
    return float(value)


def _joint_from_json(name: str, raw: Any) -> JointCal:
    if not isinstance(raw, dict):
        raise CalibrationError(f"{name}: expected an object")
    expected = {"driver", "channel", "centre_us", "sign", "offset_deg", "min_us", "max_us",
                "calibrated", "measured", "calibrated_on"}
    if set(raw) != expected:
        raise CalibrationError(
            f"{name}: fields must be exactly {sorted(expected)}, got {sorted(raw)}")
    driver, channel = raw["driver"], raw["channel"]
    if driver not in DRIVERS:
        raise CalibrationError(f"{name}: driver must be one of {DRIVERS}, got {driver!r}")
    if isinstance(channel, bool) or not isinstance(channel, int):
        raise CalibrationError(f"{name}: channel must be an integer")
    limit = 15 if driver == "pca" else len(config.GPIO_PWM_PINS) - 1
    if not 0 <= channel <= limit:
        raise CalibrationError(f"{name}: {driver} channel must be 0-{limit}, got {channel}")
    sign = raw["sign"]
    if isinstance(sign, bool) or not isinstance(sign, int) or sign not in (1, -1):
        raise CalibrationError(f"{name}: sign must be the integer +1 or -1, got {sign!r}")
    centre = _number(raw["centre_us"], f"{name}.centre_us")
    low = _number(raw["min_us"], f"{name}.min_us")
    high = _number(raw["max_us"], f"{name}.max_us")
    offset = _number(raw["offset_deg"], f"{name}.offset_deg")
    if not config.SERVO_PULSE_ABS_MIN_US <= low <= centre <= high <= config.SERVO_PULSE_ABS_MAX_US:
        raise CalibrationError(
            f"{name}: need {config.SERVO_PULSE_ABS_MIN_US} <= min_us <= centre_us <= max_us <= "
            f"{config.SERVO_PULSE_ABS_MAX_US}, got {low}, {centre}, {high}")
    if abs(offset) > 45.0:
        raise CalibrationError(f"{name}: offset_deg must be within +-45, got {offset}")
    calibrated = raw["calibrated"]
    if not isinstance(calibrated, bool):
        raise CalibrationError(f"{name}: calibrated must be true or false")
    measured = raw["measured"]
    if (not isinstance(measured, list) or any(item not in MEASURED_FIELDS for item in measured)
            or len(set(measured)) != len(measured)):
        raise CalibrationError(f"{name}: measured must list some of {MEASURED_FIELDS}")
    when = raw["calibrated_on"]
    if when is not None and not isinstance(when, str):
        raise CalibrationError(f"{name}: calibrated_on must be a date string or null")
    return JointCal(driver, channel, centre, sign, offset, low, high, calibrated,
                    tuple(measured), when)


def parse(data: Any) -> Calibration:
    """Validate parsed JSON: exactly the 18 joints, unique outputs, sane numbers."""
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise CalibrationError(f"expected an object with version {FORMAT_VERSION}")
    raw_joints = data.get("joints")
    if not isinstance(raw_joints, dict):
        raise CalibrationError("missing 'joints' object")
    names = set(raw_joints)
    wanted = set(config.JOINT_NAMES)
    if names != wanted:
        missing, extra = sorted(wanted - names), sorted(names - wanted)
        raise CalibrationError(f"joints must be exactly the 18 names (missing {missing}, "
                               f"unknown {extra})")
    joints = {name: _joint_from_json(name, raw_joints[name]) for name in config.JOINT_NAMES}
    seen: dict[tuple[str, int], str] = {}
    for name, joint in joints.items():
        if joint.output in seen:
            raise CalibrationError(
                f"{name} and {seen[joint.output]} both use {joint.driver} channel {joint.channel}")
        seen[joint.output] = name
    date = data.get("date")
    if not isinstance(date, str) or not date:
        raise CalibrationError("missing 'date' (the day the file was last written)")
    return Calibration(joints, date, str(data.get("note", "")))


def load(path: Path | str | None = None) -> Calibration:
    """Read and validate the calibration file. A missing file is an error, never a default."""
    target = Path(config.SERVO_CAL_FILE if path is None else path)
    if not target.is_file():
        raise CalibrationError(
            f"calibration file {target} not found: the servo backend does not start without it")
    try:
        data = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationError(f"cannot read {target}: {error}") from error
    return parse(data)


def to_json(calibration: Calibration) -> dict[str, Any]:
    joints: dict[str, Any] = {}
    for name in config.JOINT_NAMES:
        joint = calibration[name]
        joints[name] = {
            "driver": joint.driver, "channel": joint.channel, "centre_us": joint.centre_us,
            "sign": joint.sign, "offset_deg": joint.offset_deg, "min_us": joint.min_us,
            "max_us": joint.max_us, "calibrated": joint.calibrated,
            "measured": list(joint.measured), "calibrated_on": joint.calibrated_on,
        }
    return {"version": FORMAT_VERSION, "date": calibration.date, "note": calibration.note,
            "joints": joints}


def save(calibration: Calibration, path: Path | str | None = None) -> Path:
    """Validate, then write the file atomically (a temp file renamed over the old one)."""
    parse(to_json(calibration))  # never write a file load() would refuse
    target = Path(config.SERVO_CAL_FILE if path is None else path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(to_json(calibration), indent=2) + "\n")
    os.replace(temporary, target)
    return target


def record(joint: JointCal, *, centre_us: float | None = None, sign: int | None = None,
           min_us: float | None = None, max_us: float | None = None, today: str) -> JointCal:
    """The joint with the newly measured values; calibrated once all four were measured."""
    measured = set(joint.measured)
    changes: dict[str, Any] = {}
    for key, value, label in (("centre_us", centre_us, "centre"), ("sign", sign, "sign"),
                              ("min_us", min_us, "min"), ("max_us", max_us, "max")):
        if value is not None:
            changes[key] = value
            measured.add(label)
    updated = replace(joint, **changes)
    if centre_us is not None:  # keep the placeholder window around a newly measured centre
        if "min" not in measured:
            updated = replace(updated, min_us=max(float(config.SERVO_PULSE_ABS_MIN_US),
                                                  centre_us - _PLACEHOLDER_HALF_WINDOW_US))
        if "max" not in measured:
            updated = replace(updated, max_us=min(float(config.SERVO_PULSE_ABS_MAX_US),
                                                  centre_us + _PLACEHOLDER_HALF_WINDOW_US))
    ordered = tuple(label for label in MEASURED_FIELDS if label in measured)
    done = len(ordered) == len(MEASURED_FIELDS)
    return replace(updated, measured=ordered, calibrated=done,
                   calibrated_on=today if done else updated.calibrated_on)


def require_measured(calibration: Calibration, names: Collection[str], label: str) -> None:
    """Raise unless each joint in *names* has *label* (e.g. ``"centre"``) in ``measured``."""
    missing = [name for name in names if label not in calibration[name].measured]
    if missing:
        raise CalibrationError(
            f"{label} not measured yet for: {', '.join(missing)} (run scripts/servo_jog.py)")
