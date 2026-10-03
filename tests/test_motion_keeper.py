"""Step 6 checks: the motion keeper with a fake clock."""

from __future__ import annotations

import config
from brain.motion_keeper import MotionKeeper
from bridge import Command, Status
from tests.fakes import FakeClock


def walk(seq: int = 1) -> Command:
    return Command("walk", {"direction": "fwd", "speed": 0.5}, seq, 0.0)


def status(kind: str, ref: int | None = None, **detail: object) -> Status:
    return Status(kind, ref, dict(detail), 1, 0.0)


def make(max_walk_s: float = 10.0) -> tuple[MotionKeeper, FakeClock]:
    clock = FakeClock()
    return MotionKeeper(clock.now, max_walk_s), clock


def started(max_walk_s: float = 10.0) -> tuple[MotionKeeper, FakeClock]:
    keeper, clock = make(max_walk_s)
    keeper.on_sent(walk(1))
    keeper.on_status(status("accepted", 1))
    return keeper, clock


def beats_over(keeper: MotionKeeper, clock: FakeClock, seconds: float, step: float = 0.01) -> int:
    count = 0
    for _ in range(round(seconds / step)):
        clock.advance(step)
        count += sum(1 for c in keeper.tick() if c.action == "heartbeat")
    return count


def test_no_heartbeats_before_a_walk_is_accepted() -> None:
    keeper, clock = make()
    assert beats_over(keeper, clock, 2.0) == 0
    keeper.on_sent(walk(1))  # sent but not yet accepted
    assert not keeper.active and beats_over(keeper, clock, 2.0) == 0


def test_heartbeats_at_heartbeat_hz_after_accept() -> None:
    keeper, clock = started()
    assert keeper.active and keeper.held_seq == 1
    beats = beats_over(keeper, clock, 3.0)
    expected = 3.0 * config.HEARTBEAT_HZ
    assert expected - 1 <= beats <= expected + 1


def test_heartbeats_are_faster_than_the_watchdog_needs() -> None:
    assert 1.0 / config.HEARTBEAT_HZ < config.WATCHDOG_TIMEOUT_S / 2


def test_clear_on_stop() -> None:
    keeper, clock = started()
    keeper.on_sent(Command("stop", {}, 2, 0.0))
    assert not keeper.active and beats_over(keeper, clock, 1.0) == 0


def test_clear_when_another_motion_command_is_sent() -> None:
    for action in ("sit", "stand", "turn", "wave"):
        keeper, _ = started()
        keeper.on_sent(Command(action, {}, 2, 0.0))
        assert not keeper.active, action


def test_a_new_walk_replaces_the_held_one() -> None:
    keeper, clock = started()
    keeper.on_sent(walk(2))
    assert keeper.held_seq == 2 and not keeper.active  # waits for its own accept
    keeper.on_status(status("accepted", 1))  # the old walk's answer is ignored
    assert not keeper.active
    keeper.on_status(status("accepted", 2))
    assert keeper.active and beats_over(keeper, clock, 1.0) > 0


def test_clear_on_rejected_error_busy_and_fallen() -> None:
    for incoming in (
        status("rejected", 1, reason="invalid_state"),
        status("rejected", 1, reason="fallen"),
        status("rejected", 1, reason="superseded"),
        status("error", 1, message="boom"),
        status("busy", 1),
        status("fallen"),
    ):
        keeper, clock = started()
        keeper.on_status(incoming)
        assert not keeper.active, incoming
        assert beats_over(keeper, clock, 1.0) == 0


def test_a_rejection_of_some_other_command_does_not_clear() -> None:
    keeper, _ = started()
    keeper.on_status(status("rejected", 99, reason="already_in_state"))
    assert keeper.active


def test_clear_on_done_for_example_the_body_watchdog() -> None:
    keeper, _ = started()
    keeper.on_status(status("done", 1, action="walk", reason="watchdog"))
    assert not keeper.active


def test_a_rejected_walk_is_never_kept_alive() -> None:
    keeper, clock = make()
    keeper.on_sent(walk(1))
    keeper.on_status(status("rejected", 1, reason="invalid_state"))
    keeper.on_status(status("accepted", 1))  # even a late, odd accept does not revive it
    assert not keeper.active and keeper.held_seq is None
    assert beats_over(keeper, clock, 1.0) == 0


def test_max_duration_stops_the_heartbeats() -> None:
    keeper, clock = started(max_walk_s=3.0)
    assert beats_over(keeper, clock, 2.9) > 10
    assert keeper.active
    assert beats_over(keeper, clock, 0.3) <= 1  # crosses 3.0 s
    assert not keeper.active
    assert beats_over(keeper, clock, 5.0) == 0  # the body's watchdog now ends the walk


def test_default_max_duration_comes_from_config() -> None:
    assert config.VOICE_WALK_MAX_S == 10.0
    keeper, clock = started(max_walk_s=config.VOICE_WALK_MAX_S)
    beats_over(keeper, clock, config.VOICE_WALK_MAX_S - 0.5)
    assert keeper.active
    beats_over(keeper, clock, 1.0)
    assert not keeper.active


def test_clear_is_idempotent_and_heartbeat_sends_do_not_matter() -> None:
    keeper, _ = started()
    keeper.on_sent(Command("heartbeat", {}, 5, 0.0))
    assert keeper.active  # a heartbeat is not a motion command
    keeper.clear()
    keeper.clear()
    assert not keeper.active


def test_a_turn_is_kept_alive_until_it_is_done() -> None:
    keeper, clock = make()
    keeper.on_sent(Command("turn", {"direction": "left", "angle_deg": 90.0}, 7, 0.0))
    keeper.on_status(status("accepted", 7))
    assert keeper.active and beats_over(keeper, clock, 3.0) >= 14  # past the 1 s watchdog
    keeper.on_status(status("done", 7, action="turn"))
    assert not keeper.active and beats_over(keeper, clock, 1.0) == 0


def test_a_walk_replaces_a_held_turn_and_the_reverse() -> None:
    keeper, _ = make()
    keeper.on_sent(Command("turn", {}, 1, 0.0))
    keeper.on_sent(walk(2))
    assert keeper.held_seq == 2
    keeper.on_sent(Command("turn", {}, 3, 0.0))
    assert keeper.held_seq == 3
