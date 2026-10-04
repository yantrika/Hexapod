"""Step 12a: the PIN guard and the safety logic (one controller, deadman, stop on disconnect).

Everything runs on a fake clock and a recording sender; the last tests use a real local ``Bridge``
to prove ``stop`` takes the ``stop_event`` path even with a walk queued.
"""

from __future__ import annotations

from typing import Any

import config
from bridge import make_local_bridge, new_command
from tests.fakes import FakeClock
from web.protocol import Request
from web.session import PinGuard, WebControl

HOLD = Request("walk", forward=1.0, yaw=1.0)
IDLE = Request("walk")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, params: dict[str, Any]) -> int:
        self.sent.append((action, params))
        return len(self.sent)

    @property
    def actions(self) -> list[str]:
        return [action for action, _ in self.sent]


def make(deadman_s: float = 0.3) -> tuple[WebControl, Recorder, FakeClock]:
    clock, recorder = FakeClock(), Recorder()
    return WebControl(recorder, clock.now, deadman_s=deadman_s), recorder, clock


# --- PIN ---------------------------------------------------------------------------------
def test_right_and_wrong_pin() -> None:
    guard = PinGuard("123456", FakeClock().now)
    assert guard.check("10.0.0.2", "123456") == "ok"
    assert guard.check("10.0.0.2", "000000") == "bad_pin"
    assert guard.check("10.0.0.2", None) == "bad_pin"
    assert guard.check("10.0.0.2", "") == "bad_pin"
    assert guard.check("10.0.0.2", "1234567") == "bad_pin"


def test_lockout_after_repeated_failures_even_for_the_right_pin() -> None:
    clock = FakeClock()
    guard = PinGuard("123456", clock.now, max_failures=3, window_s=60.0, lockout_s=30.0)
    assert [guard.check("a", "x") for _ in range(3)] == ["bad_pin", "bad_pin", "bad_pin"]
    assert guard.check("a", "123456") == "locked"  # a guess cannot be confirmed while locked
    assert guard.check("a", "x") == "locked"
    assert guard.check("b", "123456") == "ok"  # another address is not affected
    clock.sleep(29.0)
    assert guard.check("a", "123456") == "locked"
    clock.sleep(2.0)
    assert guard.check("a", "123456") == "ok"  # the lockout ended


def test_failures_outside_the_window_do_not_add_up_and_success_resets() -> None:
    clock = FakeClock()
    guard = PinGuard("123456", clock.now, max_failures=3, window_s=10.0, lockout_s=30.0)
    guard.check("a", "x")
    guard.check("a", "x")
    clock.sleep(11.0)
    assert guard.check("a", "x") == "bad_pin"  # only one failure inside the window
    assert guard.check("a", "x") == "bad_pin"
    assert guard.check("a", "123456") == "ok"  # success clears the count
    assert [guard.check("a", "x") for _ in range(3)] == ["bad_pin"] * 3  # the 3rd locks
    assert guard.check("a", "x") == "locked"


def test_the_failure_table_stays_bounded() -> None:
    clock = FakeClock()
    guard = PinGuard("123456", clock.now, window_s=1.0)
    for index in range(2000):
        guard.check(f"10.1.{index // 250}.{index % 250}", "x")
        clock.sleep(0.01)
    assert len(guard._failures) < 600


# --- one controller ----------------------------------------------------------------------
def test_only_one_controller_at_a_time() -> None:
    control, recorder, _ = make()
    first, second = object(), object()
    assert control.claim(first)
    assert not control.claim(second)
    assert control.claim(first)  # the same client again is fine
    assert not control.handle(second, HOLD)  # a non-controller is ignored
    assert recorder.sent == []
    assert not control.disconnect(second)  # and its disconnect does not stop anything
    assert recorder.sent == []
    assert control.disconnect(first)
    assert control.claim(second)  # the slot is free again


# --- messages to commands --------------------------------------------------------------------
def test_combined_buttons_make_one_walk_with_direction_and_yaw() -> None:
    control, recorder, _ = make()
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    assert recorder.sent == [("walk", {"speed": config.WEB_WALK_SPEED, "direction": "fwd",
                                       "yaw": 1.0})]


def test_repeats_become_heartbeats_and_a_change_becomes_a_new_walk() -> None:
    control, recorder, clock = make()
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    for _ in range(5):
        clock.sleep(0.1)
        control.handle(client, HOLD)
    assert recorder.actions == ["walk"] + ["heartbeat"] * 5
    control.handle(client, Request("walk", forward=1.0))  # the turn button was released
    assert recorder.actions[-1] == "walk"
    assert "yaw" not in recorder.sent[-1][1]


def test_a_flood_of_unchanged_moves_is_forwarded_at_a_bounded_rate() -> None:
    control, recorder, clock = make()
    client = object()
    control.claim(client)
    for _ in range(100):  # 100 messages in 0.1 s
        control.handle(client, HOLD)
        clock.sleep(0.001)
    assert recorder.actions.count("heartbeat") <= 3


def test_release_ramps_down_with_a_zero_walk_only_after_a_move() -> None:
    control, recorder, _ = make()
    client = object()
    control.claim(client)
    control.handle(client, IDLE)
    assert recorder.sent == []  # nothing was moving: nothing to send
    control.handle(client, HOLD)
    control.handle(client, IDLE)
    assert recorder.sent[-1] == (
        "walk", {"strafe": 0.0, "yaw": 0.0, "speed": config.WEB_WALK_SPEED})
    assert not control.moving


def test_postures_are_forwarded_and_end_a_move() -> None:
    control, recorder, _ = make()
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    for action in ("stand", "sit", "wave"):
        control.handle(client, Request(action))
    assert recorder.actions == ["walk", "stand", "sit", "wave"]
    assert not control.moving


# --- the server deadman --------------------------------------------------------------------
def test_deadman_stops_a_moving_robot_when_messages_stop() -> None:
    control, recorder, clock = make(deadman_s=0.3)
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    clock.sleep(0.29)
    assert not control.tick()
    clock.sleep(0.02)
    assert control.tick()  # 0.31 s of silence
    assert recorder.actions == ["walk", "stop"]
    assert not control.moving
    clock.sleep(5.0)
    assert not control.tick()  # fires once, then the robot is already stopped
    assert recorder.actions == ["walk", "stop"]


def test_repeated_messages_keep_the_deadman_quiet_for_as_long_as_they_come() -> None:
    control, recorder, clock = make(deadman_s=0.3)
    client = object()
    control.claim(client)
    for _ in range(100):  # 10 s at 10 Hz
        control.handle(client, HOLD)
        assert not control.tick()
        clock.sleep(0.1)
    assert "stop" not in recorder.actions


def test_a_burst_of_flooded_messages_still_refreshes_the_deadman() -> None:
    control, recorder, clock = make(deadman_s=0.3)
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    clock.sleep(0.25)
    control.handle(client, HOLD)  # throttled as a heartbeat but it feeds the deadman
    clock.sleep(0.25)
    assert not control.tick()
    assert "stop" not in recorder.actions


def test_no_deadman_when_nothing_moves() -> None:
    control, recorder, clock = make()
    control.claim(object())
    clock.sleep(60.0)
    assert not control.tick()
    assert recorder.sent == []


# --- stop ------------------------------------------------------------------------------------
def test_disconnect_always_sends_stop_even_when_idle() -> None:
    control, recorder, _ = make()
    client = object()
    control.claim(client)
    control.disconnect(client)
    assert recorder.actions == ["stop"]


def test_disconnect_while_moving_stops() -> None:
    control, recorder, _ = make()
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    control.disconnect(client)
    assert recorder.actions == ["walk", "stop"]
    assert not control.moving


def test_page_hidden_blur_or_cancel_sends_stop_and_the_server_forwards_it_at_once() -> None:
    """The page sends {"action": "stop"} on visibilitychange, blur and pointercancel."""
    control, recorder, clock = make()
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    clock.sleep(0.001)  # no throttle applies to stop
    control.handle(client, Request("stop"))
    control.handle(client, Request("stop"))
    assert recorder.actions == ["walk", "stop", "stop"]


def test_stop_uses_the_stop_event_path_with_a_walk_queued() -> None:
    bridge = make_local_bridge()

    def send(action: str, params: dict[str, Any]) -> int:
        command = new_command(action, params)
        bridge.send(command)
        return command.seq

    control = WebControl(send, FakeClock().now)
    client = object()
    control.claim(client)
    control.handle(client, HOLD)
    assert bridge.command_queue.qsize() == 1 and not bridge.stop_event.is_set()
    control.handle(client, Request("stop"))
    assert bridge.stop_event.is_set()  # the fast path, set at once
    assert bridge.take_stop() == bridge.stop_seq.value
    queued = bridge.drain()
    assert [command.action for command in queued] == ["walk", "stop"]  # and it is queued too


def test_stop_survives_a_full_command_queue() -> None:
    bridge = make_local_bridge()
    control = WebControl(lambda a, p: bridge.send(new_command(a, p)) and 0, FakeClock().now)
    client = object()
    control.claim(client)
    for index in range(config.COMMAND_QUEUE_MAXSIZE * 3):  # a walk flood fills the queue
        control.handle(client, Request("walk", forward=1.0, yaw=(index % 2) or -1.0))
    control.handle(client, Request("stop"))
    assert bridge.stop_event.is_set()
