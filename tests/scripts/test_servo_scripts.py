"""The pure parts of the servo tools: the calibration key mapping and the teleop key hold."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from body import servo_calibration as sc
from scripts import servo_jog, servo_teleop
from scripts.servo_jog import JogState, apply_key
from tests.servo_fakes import good_data

STEP, COARSE = config.SERVO_JOG_STEP_US, config.SERVO_JOG_COARSE_US


def press(state: JogState, keys: str) -> JogState:
    for key in keys:
        state, _ = apply_key(state, key)
    return state


def test_steps_move_the_aim_by_the_fine_and_coarse_amounts() -> None:
    state = JogState(pulse=1500, start=1500)
    assert press(state, "d").pulse == 1500 + STEP
    assert press(state, "a").pulse == 1500 - STEP
    assert press(state, "c").pulse == 1500 + COARSE
    assert press(state, "z").pulse == 1500 - COARSE
    assert press(state, "ddac").pulse == 1500 + STEP + COARSE


def test_the_aim_never_leaves_the_absolute_window() -> None:
    low = JogState(pulse=config.SERVO_PULSE_ABS_MIN_US + 1, start=1500)
    assert press(low, "zzzz").pulse == config.SERVO_PULSE_ABS_MIN_US
    high = JogState(pulse=config.SERVO_PULSE_ABS_MAX_US - 1, start=1500)
    assert press(high, "cccc").pulse == config.SERVO_PULSE_ABS_MAX_US


def test_zero_returns_to_the_pulse_the_session_started_at() -> None:
    state = press(JogState(pulse=1500, start=1480), "ccc")
    assert apply_key(state, "0")[0].pulse == 1480


def test_notes_record_the_current_aim_and_the_sign_keys_set_the_direction() -> None:
    state = press(JogState(pulse=1500, start=1500), "cn")
    assert state.centre == 1500 + COARSE
    state = press(state, "zzm")
    assert state.min_us == 1500 + COARSE - 2 * COARSE
    state = press(state, "cccM")
    assert state.centre is not None and state.max_us is not None
    assert state.max_us == state.pulse and state.max_us > state.centre
    assert press(state, "+").sign == 1 and press(state, "-").sign == -1
    assert press(state, "+-").sign == -1  # the last answer wins
    assert state.unsaved


def test_actions_and_unknown_keys() -> None:
    state = JogState(pulse=1500, start=1500)
    assert apply_key(state, "w") == (state, "write")
    assert apply_key(state, "q") == (state, "quit")
    assert apply_key(state, "Q") == (state, "force_quit")
    assert apply_key(state, "x") == (state, None)
    assert apply_key(state, "\x1b") == (state, None)


def test_q_refuses_while_notes_are_unsaved() -> None:
    state = JogState(pulse=1500, start=1500)
    assert servo_jog.quit_allowed(state)
    assert not servo_jog.quit_allowed(press(state, "n"))
    assert servo_jog.quit_allowed(servo_jog.after_write(press(state, "n")))


def test_a_two_pass_session_ends_with_a_calibrated_joint_in_the_file(tmp_path: Path) -> None:
    path = tmp_path / "servo_cal.json"
    calibration = sc.parse(good_data(False))
    sc.save(calibration, path)
    name = "RM_femur"

    # pass 1, horn off: only the centre
    joint = calibration[name]
    state = press(JogState(pulse=joint.centre_us, start=joint.centre_us), "ccn")
    joint = servo_jog.apply_notes(joint, state, "2026-11-01")
    calibration = calibration.with_joint(name, joint, "2026-11-01")
    sc.save(calibration, path)
    reloaded = sc.load(path)
    assert reloaded[name].centre_us == 1500 + 2 * COARSE and not reloaded[name].calibrated

    # pass 2, horn on: sign, min, max
    joint = reloaded[name]
    state = JogState(pulse=joint.centre_us, start=joint.centre_us)
    state = press(state, "-" + "zzzzm" + "0" + "ccccM")
    joint = servo_jog.apply_notes(joint, state, "2026-11-02")
    sc.save(reloaded.with_joint(name, joint, "2026-11-02"), path)
    final = sc.load(path)[name]
    assert final.calibrated and final.sign == -1 and final.calibrated_on == "2026-11-02"
    assert final.min_us < final.centre_us < final.max_us
    assert final.measured == sc.MEASURED_FIELDS
    assert sc.load(path).date == "2026-11-02"
    assert sc.load(path).uncalibrated() != []  # the other 17 are untouched


def test_a_window_that_misses_the_centre_is_not_written(tmp_path: Path) -> None:
    calibration = sc.parse(good_data(True))
    joint = calibration["RF_coxa"]
    state = press(JogState(pulse=1500, start=1500), "zzzzzm")  # min 1375
    state = press(state, "0M")  # max 1500
    state = press(state, "ccccn")  # a centre of 1600: outside [min, max]
    updated = servo_jog.apply_notes(joint, state, "2026-11-02")
    with pytest.raises(sc.CalibrationError):
        sc.save(calibration.with_joint("RF_coxa", updated, "2026-11-02"), tmp_path / "x.json")


def test_the_status_line_shows_the_aim_the_real_pulse_and_the_notes() -> None:
    state = press(JogState(pulse=1500, start=1500), "n+m")
    line = servo_jog.status_line("RF_coxa", state, 1490)
    assert "RF_coxa" in line and "1500" in line and "1490" in line
    assert "centre=1500" in line and "sign=+1" in line and "min=1500" in line


def test_teleop_holds_a_key_for_the_hold_time_and_opposing_keys_cancel() -> None:
    vx, vy, yaw = servo_teleop.velocity_from_held({"w": 10.0}, 10.0, 1.0)
    assert vx == pytest.approx(config.GAIT_MAX_SPEED_M_S) and vy == 0 and yaw == 0
    later = 10.0 + config.TERMINAL_KEY_HOLD_S + 0.01
    assert servo_teleop.velocity_from_held({"w": 10.0}, later, 1.0) == (0.0, 0.0, 0.0)
    assert servo_teleop.velocity_from_held({"w": 10.0, "s": 10.0}, 10.0, 1.0)[0] == 0.0
