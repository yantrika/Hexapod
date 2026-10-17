"""The calibration file: validation (missing, duplicate output, uncalibrated), explicit signs,
the pulse maths round trip for all 18 joints, and the recorded-measurement rules."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import config
from body import servo_calibration as sc
from body.servo_calibration import CalibrationError, JointCal
from tests.servo_fakes import calibrated, good_data


def test_the_committed_file_is_valid_and_lists_the_18_joints_with_unique_outputs() -> None:
    cal = sc.load()
    assert tuple(cal.joints) == config.JOINT_NAMES
    outputs = [joint.output for joint in cal.joints.values()]
    assert len(set(outputs)) == config.DOF
    assert sorted(c for d, c in outputs if d == "pca") == list(range(16))
    assert {n for n, j in cal.joints.items() if j.driver == "gpio"} == {"RR_coxa", "LR_coxa"}
    assert cal.date  # dated


def test_a_missing_file_is_an_error_not_a_default(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="not found"):
        sc.load(tmp_path / "nope.json")


def test_unreadable_json_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "servo_cal.json"
    path.write_text("{not json")
    with pytest.raises(CalibrationError, match="cannot read"):
        sc.load(path)


def test_two_joints_on_one_output_are_refused() -> None:
    data = good_data()
    data["joints"]["RF_femur"]["channel"] = data["joints"]["RF_coxa"]["channel"]
    with pytest.raises(CalibrationError, match="both use"):
        sc.parse(data)


def test_the_file_must_list_exactly_the_18_joints() -> None:
    data = good_data()
    del data["joints"]["LF_tibia"]
    with pytest.raises(CalibrationError, match="missing"):
        sc.parse(data)
    data = good_data()
    data["joints"]["XX_coxa"] = copy.deepcopy(data["joints"]["RF_coxa"])
    with pytest.raises(CalibrationError, match="unknown"):
        sc.parse(data)


@pytest.mark.parametrize("sign", [0, 2, 1.0, "1", True, None])
def test_the_sign_must_be_the_integer_plus_or_minus_one(sign: Any) -> None:
    data = good_data()
    data["joints"]["RF_coxa"]["sign"] = sign
    with pytest.raises(CalibrationError, match="sign"):
        sc.parse(data)


def test_numbers_and_windows_are_checked() -> None:
    cases = [("centre_us", 3000), ("min_us", 1600), ("max_us", 1400), ("min_us", 100),
             ("offset_deg", 60), ("centre_us", float("nan")), ("channel", 16), ("channel", True)]
    for key, value in cases:
        data = good_data()
        data["joints"]["RF_coxa"][key] = value
        with pytest.raises(CalibrationError):
            sc.parse(data)
    data = good_data()
    data["joints"]["RR_coxa"]["channel"] = 2  # gpio has channels 0 and 1 only
    with pytest.raises(CalibrationError, match="channel"):
        sc.parse(data)


def test_unknown_or_missing_fields_are_refused() -> None:
    data = good_data()
    data["joints"]["RF_coxa"]["extra"] = 1
    with pytest.raises(CalibrationError, match="fields"):
        sc.parse(data)
    data = good_data()
    data["date"] = ""
    with pytest.raises(CalibrationError, match="date"):
        sc.parse(data)


def test_require_calibrated_names_the_joints_that_are_not() -> None:
    data = good_data()
    data["joints"]["LM_femur"]["calibrated"] = False
    cal = sc.parse(data)
    with pytest.raises(CalibrationError, match="LM_femur"):
        cal.require_calibrated()
    cal.require_calibrated(["RF_coxa"])  # a subset that is calibrated is fine
    assert cal.uncalibrated() == ["LM_femur"]


def test_save_round_trips_and_never_writes_an_invalid_file(tmp_path: Path) -> None:
    cal = calibrated()
    path = sc.save(cal, tmp_path / "sub" / "servo_cal.json")
    assert sc.load(path).joints == cal.joints
    broken = cal.with_joint("RF_coxa", JointCal("pca", 0, centre_us=1500, min_us=1600,
                                                max_us=1700))
    with pytest.raises(CalibrationError):
        sc.save(broken, tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


@pytest.mark.parametrize("name", config.JOINT_NAMES)
def test_sign_round_trip_for_every_joint(name: str) -> None:
    joint = calibrated()[name]
    assert joint.pulse_us(0.0) == pytest.approx(joint.centre_us + joint.sign * joint.offset_deg
                                                * config.SERVO_US_PER_DEG)
    for angle in (-20.0, -5.0, 0.0, 7.5, 20.0):
        assert joint.angle_deg(joint.pulse_us(angle)) == pytest.approx(angle)
    up = joint.pulse_us(10.0) - joint.pulse_us(0.0)
    assert (up > 0) == (joint.sign == 1)  # the direction comes from the file's sign alone


def test_the_sign_and_offset_live_only_in_the_file() -> None:
    joint = JointCal("pca", 0, centre_us=1500, sign=1, offset_deg=0.0, min_us=1000, max_us=2000)
    flipped = JointCal("pca", 0, centre_us=1500, sign=-1, offset_deg=0.0, min_us=1000, max_us=2000)
    trimmed = JointCal("pca", 0, centre_us=1500, sign=1, offset_deg=3.0, min_us=1000, max_us=2000)
    assert joint.pulse_us(10) - 1500 == pytest.approx(1500 - flipped.pulse_us(10))
    assert trimmed.pulse_us(0) == pytest.approx(joint.pulse_us(3.0))


def test_the_pulse_is_clamped_to_the_joint_window_and_the_absolute_one() -> None:
    joint = JointCal("pca", 0, centre_us=1500, min_us=1200, max_us=1800)
    assert joint.pulse_us(90) == 1800 and joint.pulse_us(-90) == 1200
    wide = JointCal("pca", 0, centre_us=1500, min_us=450, max_us=2550)
    assert wide.pulse_us(1000) == config.SERVO_PULSE_ABS_MAX_US


def test_record_calibrates_only_when_all_four_values_were_measured() -> None:
    joint = sc.load()["RF_coxa"]
    step = sc.record(joint, centre_us=1480, today="2026-11-01")
    assert step.centre_us == 1480 and not step.calibrated and step.measured == ("centre",)
    assert step.min_us < 1480 < step.max_us  # the placeholder window follows the centre
    step = sc.record(step, sign=-1, today="2026-11-01")
    step = sc.record(step, min_us=1100, today="2026-11-02")
    assert not step.calibrated
    done = sc.record(step, max_us=1900, today="2026-11-03")
    assert done.calibrated and done.calibrated_on == "2026-11-03"
    assert done.measured == sc.MEASURED_FIELDS and done.sign == -1


def test_require_measured_names_what_is_missing() -> None:
    cal = sc.load()
    with pytest.raises(CalibrationError, match="centre not measured"):
        sc.require_measured(cal, ["RF_coxa"], "centre")
    sc.require_measured(calibrated(), ["RF_coxa"], "centre")
