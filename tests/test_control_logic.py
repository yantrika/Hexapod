"""Control window logic (no Tk, no display): key mapping, debounce, focus, heartbeats."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from bridge import Status
from scripts.control_logic import (
    HEARTBEAT_PERIOD_S,
    RELEASE_DEBOUNCE_S,
    ControlState,
    Send,
    adjust_scale,
    next_state_label,
    walk_params,
)
from tests.fakes import FakeClock

SPEED = 0.5


def keys(*held: str) -> frozenset[str]:
    return frozenset(held)


# --- key state -> command (pure) ---------------------------------------------------
@pytest.mark.parametrize(
    ("held", "expected"),
    [
        (keys("w"), {"speed": SPEED, "direction": "fwd"}),
        (keys("s"), {"speed": SPEED, "direction": "back"}),
        (keys("a"), {"speed": SPEED, "strafe": 1.0}),
        (keys("d"), {"speed": SPEED, "strafe": -1.0}),
        (keys("q"), {"speed": SPEED, "yaw": 1.0}),
        (keys("e"), {"speed": SPEED, "yaw": -1.0}),
        (keys("w", "a"), {"speed": SPEED, "direction": "fwd", "strafe": 1.0}),
        (keys("w", "q"), {"speed": SPEED, "direction": "fwd", "yaw": 1.0}),
        (keys("s", "d", "e"), {"speed": SPEED, "direction": "back", "strafe": -1.0, "yaw": -1.0}),
        (keys("w", "s"), {"speed": SPEED, "strafe": 0.0, "yaw": 0.0}),  # cancel: a zero walk
        (keys("a", "d", "q", "e"), {"speed": SPEED, "strafe": 0.0, "yaw": 0.0}),
        (keys(), {"speed": SPEED, "strafe": 0.0, "yaw": 0.0}),  # nothing held: a zero walk
    ],
)
def test_walk_params(held: frozenset[str], expected: dict) -> None:
    assert walk_params(held, SPEED) == expected


def test_walk_params_clamp_speed_to_the_config_maximum() -> None:
    assert walk_params(keys("w"), 7.0)["speed"] == config.SPEED_MAX
    assert walk_params(keys("w"), -1.0)["speed"] == 0.0


def test_scale_is_capped_by_the_config_range() -> None:
    scale = config.TELEOP_SPEED_SCALE_DEFAULT
    for _ in range(50):
        scale = adjust_scale(scale, "+")
    assert scale == config.SPEED_MAX
    for _ in range(50):
        scale = adjust_scale(scale, "-")
    assert scale == config.TELEOP_SPEED_SCALE_MIN
    assert adjust_scale(0.5, "x") == 0.5


# --- the state machine with a fake clock --------------------------------------------
def make() -> tuple[ControlState, FakeClock]:
    clock = FakeClock()
    return ControlState(clock.now, scale=SPEED), clock


def test_press_sends_one_combined_walk_and_repeat_presses_send_nothing() -> None:
    state, _ = make()
    assert state.key_press("w") == [Send("walk", {"speed": SPEED, "direction": "fwd"})]
    assert state.key_press("w") == []
    assert state.key_press("a") == [
        Send("walk", {"speed": SPEED, "direction": "fwd", "strafe": 1.0})
    ]
    assert state.key_press("W") == []  # shift does not matter


def test_a_real_release_zeroes_that_component_after_the_debounce() -> None:
    state, clock = make()
    state.key_press("w")
    state.key_press("a")
    state.key_release("a")
    clock.advance(RELEASE_DEBOUNCE_S / 2)
    assert [s for s in state.poll() if s.action == "walk"] == []  # still counted as held
    clock.advance(RELEASE_DEBOUNCE_S)
    assert state.poll() == [Send("walk", {"speed": SPEED, "direction": "fwd"})]
    state.key_release("w")
    clock.advance(RELEASE_DEBOUNCE_S * 2)
    assert state.poll() == [Send("walk", {"speed": SPEED, "strafe": 0.0, "yaw": 0.0})]
    assert not state.moving


def test_fake_autorepeat_release_press_pair_changes_nothing() -> None:
    state, clock = make()
    state.key_press("w")
    sent: list[Send] = []
    for _ in range(10):  # X11 holds a key as release+press pairs about every 30 ms
        state.key_release("w")
        clock.advance(0.002)  # the fake press follows almost at once
        sent += state.key_press("w")
        clock.advance(0.03)
        sent += [s for s in state.poll() if s.action == "walk"]
    assert sent == []
    assert state.held == keys("w")


def test_release_without_a_following_press_is_real_even_just_past_the_debounce() -> None:
    state, clock = make()
    state.key_press("w")
    state.key_release("w")
    clock.advance(RELEASE_DEBOUNCE_S + 0.001)
    assert state.poll()[0].params["strafe"] == 0.0


def test_focus_loss_sends_stop_and_forgets_the_keys() -> None:
    state, clock = make()
    state.key_press("w")
    state.key_press("q")
    assert state.focus_lost() == [Send("stop", {})]
    assert not state.moving
    clock.advance(1.0)
    assert state.poll() == []  # no heartbeats, no stale release left over
    assert state.close() == [Send("stop", {})]


def test_entry_focus_blocks_movement_and_posture_keys() -> None:
    state, _ = make()
    assert state.set_entry_focus(True) == []
    for key in "wasdqe 123+-":
        assert state.key_press(key) == []
    assert not state.moving
    state.set_entry_focus(False)
    assert state.key_press("w") != []


def test_taking_the_entry_focus_while_moving_releases_every_key() -> None:
    state, _ = make()
    state.key_press("w")
    zero_walk = Send("walk", {"speed": SPEED, "strafe": 0.0, "yaw": 0.0})
    assert state.set_entry_focus(True) == [zero_walk]
    assert not state.moving


def test_heartbeats_at_10_hz_only_while_a_movement_key_is_held() -> None:
    state, clock = make()
    assert state.poll() == []
    state.key_press("w")
    beats = 0
    for _ in range(100):  # 1 s in 10 ms polls
        clock.advance(0.01)
        beats += sum(1 for s in state.poll() if s.action == "heartbeat")
    assert 9 <= beats <= 11
    assert HEARTBEAT_PERIOD_S == pytest.approx(0.1)
    state.key_release("w")
    clock.advance(0.2)
    state.poll()
    clock.advance(1.0)
    assert state.poll() == []


def test_posture_keys_and_stop_clears_held_movement() -> None:
    state, _ = make()
    assert state.key_press("1") == [Send("stand", {})]
    assert state.key_press("2") == [Send("sit", {})]
    assert state.key_press("3") == [Send("wave", {})]
    state.key_press("w")
    assert state.key_press(" ") == [Send("stop", {})]
    assert not state.moving
    state.key_release("w")  # the late release of a key that stop already cleared
    assert state.poll() == []


def test_speed_keys_resend_the_walk_only_while_moving() -> None:
    state, _ = make()
    assert state.key_press("+") == []  # idle: the scale changes, nothing is sent
    assert state.scale == pytest.approx(SPEED + config.TELEOP_SPEED_SCALE_STEP)
    state.key_press("w")
    (walk,) = state.key_press("-")  # moving: the walk is resent at the new speed
    assert walk.params["speed"] == pytest.approx(SPEED)


def test_unknown_keys_do_nothing() -> None:
    state, _ = make()
    assert state.key_press("z") == [] and state.key_press("") == []


# --- state line -----------------------------------------------------------------
def status(kind: str, ref: int | None = None, **detail: object) -> Status:
    return Status(kind, ref, dict(detail), 1, 0.0)


def test_state_label_follows_the_statuses() -> None:
    sent = {1: "walk", 2: "halt", 3: "sit", 4: "stand", 5: "wave"}
    label = "standing"
    for incoming, expected in [
        (status("accepted", 1), "walking"),
        (status("accepted", 2), "walking"),  # a halt keeps the label until the walk's done
        (status("done", 1, action="stand"), "standing"),
        (status("accepted", 3), "sitting down"),
        (status("done", 3, action="sit"), "sitting"),
        (status("busy", 4, state="sitting_down"), "sitting_down"),
        (status("accepted", 4), "standing up"),
        (status("done", 4, action="stand"), "standing"),
        (status("accepted", 5), "waving"),
        (status("done", 5, action="wave"), "standing"),
        (status("done", 9, action="stop"), "holding"),
        (status("fallen"), "FALLEN"),
    ]:
        label = next_state_label(label, incoming, sent)
        assert label == expected


def test_a_halt_when_already_idle_does_not_stick_on_walking() -> None:
    assert next_state_label("standing", status("accepted", 2), {2: "halt"}) == "standing"


def test_the_window_talks_only_through_the_bridge() -> None:
    source = (Path(__file__).resolve().parent.parent / "scripts" / "control_window.py").read_text()
    for forbidden in ("body.controller", "body.sim_backend", "body.backend", "body.gait",
                      "pybullet", "Controller("):
        assert forbidden not in source
    assert "from body.process import BodyProcess" in source  # the only body import
