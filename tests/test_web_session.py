"""Step 12a: the PIN guard and the safety logic (one controller, deadman, stop on disconnect).

Everything runs on a fake clock and a recording sender; the last tests use a real local ``Bridge``
to prove ``stop`` takes the ``stop_event`` path even with a walk queued.
"""

from __future__ import annotations

from typing import Any

import pytest

import config
from bridge import make_local_bridge, new_command
from tests.fakes import FakeClock
from web.protocol import Request
from web.session import PinGuard, Refused, VoiceControls, WebControl

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


# --- Step 12b: hold-to-talk and typed text ---------------------------------------------------
class FakeVoice:
    """The voice side: records listening changes and typed text, in order."""

    def __init__(self, ptt_unavailable: str | None = None,
                 say_unavailable: str | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.controls = VoiceControls(self.set_listening, self.say, ptt_unavailable,
                                      say_unavailable)

    def set_listening(self, on: bool) -> None:
        self.calls.append(("listening", on))

    def say(self, text: str) -> None:
        self.calls.append(("say", text))

    @property
    def listening_changes(self) -> list[bool]:
        return [value for name, value in self.calls if name == "listening"]  # type: ignore[misc]


PRESS, RELEASE = Request("ptt_press"), Request("ptt_release")


def make_voice(**options: Any) -> tuple[WebControl, Recorder, FakeClock, FakeVoice, object]:
    clock, recorder = FakeClock(), Recorder()
    voice = FakeVoice(options.pop("ptt_unavailable", None), options.pop("say_unavailable", None))
    control = WebControl(recorder, clock.now, voice=voice.controls, **options)
    client = object()
    control.claim(client)
    return control, recorder, clock, voice, client


def test_press_and_release_drive_listening_and_never_touch_the_bridge() -> None:
    control, recorder, _, voice, client = make_voice()
    assert control.handle(client, PRESS) and control.listening
    assert control.handle(client, RELEASE) and not control.listening
    assert voice.listening_changes == [True, False]
    assert recorder.sent == []  # not a body message: the body schema is untouched


def test_a_repeated_press_changes_nothing_and_a_stray_release_is_harmless() -> None:
    control, _, clock, voice, client = make_voice(ptt_max_s=10.0)
    control.handle(client, RELEASE)  # nothing was pressed
    control.handle(client, PRESS)
    clock.sleep(6.0)
    control.handle(client, PRESS)  # a repeat must not extend the limit
    assert voice.listening_changes == [True]
    clock.sleep(4.1)
    assert control.expire_listening()  # 10 s after the FIRST press
    assert voice.listening_changes == [True, False]


def test_listening_is_forced_to_release_at_the_maximum_duration() -> None:
    control, recorder, clock, voice, client = make_voice(ptt_max_s=10.0)
    control.handle(client, PRESS)
    clock.sleep(9.9)
    assert not control.expire_listening() and control.listening
    clock.sleep(0.2)
    assert control.expire_listening() and not control.listening
    assert voice.listening_changes == [True, False]
    assert not control.expire_listening()  # once only
    assert recorder.sent == []


def test_the_default_maximum_is_the_configured_one() -> None:
    control, _, clock, voice, client = make_voice()
    control.handle(client, PRESS)
    clock.sleep(config.WEB_PTT_MAX_S - 0.1)
    assert not control.expire_listening()
    clock.sleep(0.2)
    assert control.expire_listening()
    assert config.WEB_PTT_MAX_S <= 12.0  # about 10 s


def test_a_disconnect_releases_listening_and_stops_the_robot() -> None:
    control, recorder, _, voice, client = make_voice()
    control.handle(client, PRESS)
    assert control.disconnect(client)
    assert voice.listening_changes == [True, False] and recorder.actions == ["stop"]
    assert control.controller is None


def test_a_hidden_page_or_blur_sends_stop_which_releases_listening() -> None:
    # the page sends `stop` on visibilitychange / blur / pagehide (and ptt_release first)
    control, recorder, _, voice, client = make_voice()
    control.handle(client, PRESS)
    control.handle(client, Request("stop"))
    assert voice.listening_changes == [True, False] and recorder.actions == ["stop"]
    assert not control.listening


def test_stop_is_sent_before_listening_is_released_and_a_slow_release_cannot_delay_it() -> None:
    order: list[str] = []
    clock = FakeClock()
    voice = VoiceControls(lambda on: order.append(f"listening {on}"), lambda text: None)
    control = WebControl(lambda action, params: order.append(action), clock.now, voice=voice)
    client = object()
    control.claim(client)
    control.handle(client, PRESS)
    control.handle(client, Request("stop"))
    assert order == ["listening True", "stop", "listening False"]


def test_the_stop_button_works_while_listening_even_when_the_voice_side_fails() -> None:
    clock, recorder = FakeClock(), Recorder()

    def broken(on: bool) -> None:
        if not on:
            raise RuntimeError("voice is stuck")

    control = WebControl(recorder, clock.now, voice=VoiceControls(broken, lambda text: None))
    client = object()
    control.claim(client)
    control.handle(client, PRESS)
    with pytest.raises(RuntimeError):
        control.handle(client, Request("stop"))
    assert recorder.actions == ["stop"]  # the robot was stopped regardless
    assert not control.listening


def test_a_failed_press_does_not_leave_listening_marked_on() -> None:
    clock = FakeClock()

    def broken(on: bool) -> None:
        raise RuntimeError("no microphone")

    control = WebControl(Recorder(), clock.now, voice=VoiceControls(broken, lambda text: None))
    client = object()
    control.claim(client)
    with pytest.raises(RuntimeError):
        control.handle(client, PRESS)
    assert not control.listening and not control.expire_listening()


def test_only_the_controller_may_press_or_say() -> None:
    control, _, _, voice, client = make_voice()
    stranger = object()
    assert not control.handle(stranger, PRESS)
    assert not control.handle(stranger, Request("say", text="stop"))
    assert not control.handle(stranger, RELEASE)
    assert voice.calls == []
    control.handle(client, PRESS)
    assert not control.handle(stranger, RELEASE)  # a stranger cannot end the controller's press
    assert control.listening
    assert not control.disconnect(stranger)  # nor does its disconnect release it
    assert control.listening


def test_without_a_microphone_a_press_is_refused_with_the_reason_and_release_is_harmless() -> None:
    reason = "the robot runs with --no-mic"
    control, recorder, _, voice, client = make_voice(ptt_unavailable=reason)
    with pytest.raises(Refused, match="--no-mic"):
        control.handle(client, PRESS)
    assert not control.listening and voice.calls == []
    assert control.handle(client, RELEASE)  # letting go is never refused
    control.handle(client, Request("say", text="hello"))  # typing still works
    assert voice.calls == [("say", "hello")]
    assert recorder.sent == []


def test_say_goes_to_the_voice_side_not_the_bridge() -> None:
    control, recorder, _, voice, client = make_voice()
    control.handle(client, Request("say", text="walk forward"))
    assert voice.calls == [("say", "walk forward")] and recorder.sent == []


def test_say_is_refused_when_voice_is_off_and_a_flood_is_rate_limited() -> None:
    off, _, _, voice, client = make_voice(say_unavailable="voice is not running")
    with pytest.raises(Refused, match="not running"):
        off.handle(client, Request("say", text="hi"))
    assert voice.calls == []

    control, _, clock, voice, client = make_voice(say_min_interval_s=0.3)
    control.handle(client, Request("say", text="one"))
    with pytest.raises(Refused, match="too fast"):
        control.handle(client, Request("say", text="two"))
    clock.sleep(0.31)
    control.handle(client, Request("say", text="three"))
    assert [text for name, text in voice.calls if name == "say"] == ["one", "three"]


def test_the_default_controls_refuse_everything_voice() -> None:
    clock = FakeClock()
    control = WebControl(Recorder(), clock.now)  # a 12a-style server: no voice at all
    client = object()
    control.claim(client)
    for request in (PRESS, Request("say", text="hi")):
        with pytest.raises(Refused):
            control.handle(client, request)
