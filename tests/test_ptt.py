"""Step 10: the push-to-talk state machine (pure, fake clock) and the thread-safe wrapper."""

from __future__ import annotations

import threading

import pytest

import config
from tests.fakes import FakeClock
from voice.ptt import (
    IDLE,
    LISTENING,
    PRESS,
    RELEASE,
    TAIL,
    TICK,
    PttState,
    PushToTalk,
    make_ptt,
    ptt_step,
)

TAIL_S = 0.5


def test_press_from_idle_starts_a_new_utterance() -> None:
    step = ptt_step(PttState(), PRESS, 0.0, TAIL_S)
    assert step.state.phase == LISTENING and step.started and not step.finished


def test_release_starts_the_tail_and_the_tail_ends_the_utterance() -> None:
    state = ptt_step(PttState(), PRESS, 0.0, TAIL_S).state
    state = ptt_step(state, RELEASE, 2.0, TAIL_S).state
    assert state == PttState(TAIL, 2.5)
    assert ptt_step(state, TICK, 2.49, TAIL_S).state.phase == TAIL  # still listening
    done = ptt_step(state, TICK, 2.5, TAIL_S)
    assert done.state.phase == IDLE and done.finished and not done.started


def test_a_double_press_changes_nothing() -> None:
    state = ptt_step(PttState(), PRESS, 0.0, TAIL_S).state
    again = ptt_step(state, PRESS, 0.1, TAIL_S)
    assert again.state == state and not again.started


def test_a_press_during_the_tail_continues_the_same_utterance() -> None:
    state = ptt_step(ptt_step(PttState(), PRESS, 0.0, TAIL_S).state, RELEASE, 1.0, TAIL_S).state
    again = ptt_step(state, PRESS, 1.2, TAIL_S)
    assert again.state.phase == LISTENING and not again.started  # no reset mid-phrase


def test_release_or_tick_while_idle_do_nothing() -> None:
    for event in (RELEASE, TICK):
        step = ptt_step(PttState(), event, 5.0, TAIL_S)
        assert step.state.phase == IDLE and not step.started and not step.finished


def test_the_function_is_pure() -> None:
    state = PttState()
    ptt_step(state, PRESS, 0.0, TAIL_S)
    assert state == PttState()


# --- the wrapper -----------------------------------------------------------------------------
def test_poll_reports_feed_start_and_finish_with_a_fake_clock() -> None:
    clock = FakeClock()
    ptt = PushToTalk(clock.now, tail_s=TAIL_S)
    assert not ptt.poll().feed  # idle: nothing is fed
    ptt.press()
    poll = ptt.poll()
    assert poll.feed and poll.started and not poll.finished
    assert not ptt.poll().started  # reported once
    ptt.release()
    clock.advance(0.3)
    assert ptt.poll().feed and not ptt.active  # in the tail: still fed, no longer "active"
    clock.advance(0.3)
    poll = ptt.poll()
    assert poll.finished and not poll.feed
    assert ptt.phase == IDLE


def test_a_whole_press_and_release_between_two_polls_is_not_lost() -> None:
    clock = FakeClock()
    ptt = PushToTalk(clock.now, tail_s=TAIL_S)
    ptt.press()
    ptt.release()
    clock.advance(1.0)
    poll = ptt.poll()
    assert poll.started and poll.finished and not poll.feed


def test_press_listeners_run_before_listening_starts() -> None:
    ptt = PushToTalk(FakeClock().now)
    seen: list[str] = []
    ptt.add_press_listener(lambda: seen.append(ptt.phase))
    ptt.press()
    assert seen == [IDLE] and ptt.phase == LISTENING  # barge-in is done before the mic opens


def test_a_failing_press_listener_does_not_stop_listening() -> None:
    ptt = PushToTalk(FakeClock().now)

    def boom() -> None:
        raise RuntimeError("barge-in failed")

    ptt.add_press_listener(boom)
    ptt.press()
    assert ptt.active


def test_toggle_and_the_indicator() -> None:
    clock = FakeClock()
    ptt = PushToTalk(clock.now, tail_s=TAIL_S)
    shown: list[str] = []
    ptt.add_change_listener(shown.append)
    assert ptt.toggle() is True and ptt.toggle() is False
    clock.advance(1.0)
    ptt.poll()
    assert shown == ["listening", "idle"]  # idle is shown once the tail is over


def test_thread_safety_under_a_storm_of_presses() -> None:
    ptt = PushToTalk(FakeClock().now)

    def hammer() -> None:
        for _ in range(500):
            ptt.press()
            ptt.release()
            ptt.poll()

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ptt.phase in (IDLE, LISTENING, TAIL)


# --- the mode switch -------------------------------------------------------------------------
def test_the_mode_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.LISTEN_MODE == "ptt"  # the default
    assert isinstance(make_ptt(), PushToTalk)
    monkeypatch.setattr(config, "LISTEN_MODE", "always")
    assert make_ptt() is None
    assert make_ptt("ptt") is not None  # an explicit choice wins


def test_an_unknown_mode_is_an_error() -> None:
    with pytest.raises(ValueError):
        make_ptt("wakeword")
