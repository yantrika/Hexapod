"""Step 4 checks: teleop's key mapping is pure and tested without a GUI."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("pybullet")  # the simulator is not installed on the Pi

import config  # noqa: E402
from body.controller import Controller, State  # noqa: E402
from body.gait import BodyVelocity  # noqa: E402
from scripts import teleop  # noqa: E402
from tests.fakes import FakeBackend, FakeClock  # noqa: E402

MAX_V = config.GAIT_MAX_SPEED_M_S
MAX_W = math.radians(config.TURN_RATE_MAX_DEG_S)


def test_no_keys_means_zero_command() -> None:
    assert teleop.command_from_keys({}, 1.0) == BodyVelocity(0.0, 0.0, 0.0)
    assert teleop.command_from_keys({"w": False, "a": False}, 1.0) == BodyVelocity()


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("w", BodyVelocity(MAX_V, 0, 0)),
        ("s", BodyVelocity(-MAX_V, 0, 0)),
        ("a", BodyVelocity(0, MAX_V, 0)),
        ("d", BodyVelocity(0, -MAX_V, 0)),
        ("q", BodyVelocity(0, 0, MAX_W)),
        ("e", BodyVelocity(0, 0, -MAX_W)),
    ],
)
def test_each_movement_key_drives_one_component(key: str, expected: BodyVelocity) -> None:
    assert teleop.command_from_keys({key: True}, 1.0) == expected


def test_opposing_keys_cancel_and_combinations_add() -> None:
    assert teleop.command_from_keys({"w": True, "s": True}, 1.0) == BodyVelocity()
    both = teleop.command_from_keys({"w": True, "a": True, "e": True}, 1.0)
    assert both == BodyVelocity(MAX_V, MAX_V, -MAX_W)


def test_speed_scale_scales_the_command_and_is_capped_by_the_config_maximum() -> None:
    half = teleop.command_from_keys({"w": True, "q": True}, 0.5)
    assert half == BodyVelocity(0.5 * MAX_V, 0, 0.5 * MAX_W)
    assert teleop.command_from_keys({"w": True}, 5.0).vx == pytest.approx(MAX_V)  # capped
    assert teleop.command_from_keys({"w": True}, -1.0).vx == 0.0


def test_releasing_a_key_zeroes_that_component_only() -> None:
    held = {"w": True, "a": True}
    assert teleop.command_from_keys(held, 1.0) == BodyVelocity(MAX_V, MAX_V, 0)
    assert teleop.command_from_keys({**held, "w": False}, 1.0) == BodyVelocity(0, MAX_V, 0)
    assert teleop.command_from_keys({**held, "w": False, "a": False}, 1.0) == BodyVelocity()


def test_the_mapping_does_not_mutate_its_input() -> None:
    keys = {"w": True}
    teleop.command_from_keys(keys, 1.0)
    assert keys == {"w": True}


def test_posture_keys_map_to_controller_actions_with_stop_first() -> None:
    assert teleop.actions_from_presses([" "]) == ["stop"]
    assert teleop.actions_from_presses(["1"]) == ["stand"]
    assert teleop.actions_from_presses(["2"]) == ["sit"]
    assert teleop.actions_from_presses(["3"]) == ["wave"]
    assert teleop.actions_from_presses(["3", "1", " "]) == ["stop", "wave", "stand"]
    assert teleop.actions_from_presses(["x", "w"]) == []  # movement keys are not actions


def test_speed_keys_step_the_scale_within_the_allowed_range() -> None:
    step = config.TELEOP_SPEED_SCALE_STEP
    assert teleop.adjust_scale(0.5, ["+"]) == pytest.approx(0.5 + step)
    assert teleop.adjust_scale(0.5, ["="]) == pytest.approx(0.5 + step)  # "+" without shift
    assert teleop.adjust_scale(0.5, ["-"]) == pytest.approx(0.5 - step)
    assert teleop.adjust_scale(0.5, ["+", "-"]) == pytest.approx(0.5)
    assert teleop.adjust_scale(config.SPEED_MAX, ["+"]) == config.SPEED_MAX  # capped
    lowest = config.TELEOP_SPEED_SCALE_MIN
    assert teleop.adjust_scale(lowest, ["-"]) == lowest
    assert teleop.adjust_scale(0.5, ["w", "x"]) == 0.5


def test_key_state_follows_pybullet_keyboard_events() -> None:
    first_frame = {ord("w"): teleop.KEY_IS_DOWN | teleop.KEY_WAS_TRIGGERED}
    down, pressed = teleop.update_key_state({}, first_frame)
    assert down == {"w": True} and pressed == ["w"]
    down, pressed = teleop.update_key_state(down, {ord("w"): teleop.KEY_IS_DOWN})  # still held
    assert down == {"w": True} and pressed == []
    down, pressed = teleop.update_key_state(down, {ord("w"): teleop.KEY_WAS_RELEASED})
    assert down == {"w": False} and pressed == []
    down, _ = teleop.update_key_state(down, {})  # no events: nothing changes
    assert down == {"w": False}


def test_key_state_handles_case_other_keys_and_quick_taps() -> None:
    shifted = {ord("W"): teleop.KEY_IS_DOWN | teleop.KEY_WAS_TRIGGERED}
    down, pressed = teleop.update_key_state({}, shifted)
    assert down == {"w": True} and pressed == ["w"]  # caps lock or shift
    tap = teleop.KEY_WAS_TRIGGERED | teleop.KEY_WAS_RELEASED
    down, pressed = teleop.update_key_state({}, {ord(" "): tap})
    assert pressed == [" "] and down == {" ": False}  # a tap still counts as a press
    nonsense = {-1: teleop.KEY_IS_DOWN, 0x7FFFFFFF: teleop.KEY_IS_DOWN}
    down, pressed = teleop.update_key_state({}, nonsense)
    assert down == {} and pressed == []  # nonsense key codes are ignored


def test_teleop_keys_drive_the_controller_through_its_api_with_a_deadman() -> None:
    backend, clock = FakeBackend(), FakeClock()
    controller = Controller(backend, clock)
    dt = 1.0 / config.CONTROL_HZ
    held: dict[str, bool] = {}
    for tick in range(round(3.0 / dt)):  # hold W for 1.5 s, then release
        held["w"] = tick < round(1.5 / dt)
        command = teleop.command_from_keys(held, 0.5)
        if any(held.values()) or controller.state is State.MOVING:
            controller.set_velocity(command.vx, command.vy, command.yaw_rate)
        clock.advance(dt)
        controller.tick(dt)
        if tick == round(1.4 / dt):
            assert controller.velocity.vx == pytest.approx(0.5 * MAX_V)
    assert controller.velocity.vx == 0.0  # released: ramped to a halt
    assert controller.state is State.STANDING


def test_teleop_and_joint_jog_respect_their_api_boundaries() -> None:
    scripts = Path(config.PROJECT_ROOT) / "scripts"
    teleop_source = (scripts / "teleop.py").read_text()
    jog_source = (scripts / "joint_jog.py").read_text()
    # teleop commands motion only through the controller API ...
    for forbidden in ("set_joint_targets", "._backend", "gait.plan", "poses."):
        assert forbidden not in teleop_source
    assert "Controller" in teleop_source
    # ... and joint_jog only through the backend API.
    for forbidden in ("Controller", "gait", "poses", "bridge"):
        assert forbidden not in jog_source.split('"""', 2)[2]
    assert "set_joint_targets" in jog_source
