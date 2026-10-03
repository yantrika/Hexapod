"""Step 5 checks: messages, validation and the bridge queues."""

from __future__ import annotations

import multiprocessing as mp
import time

import pytest

import config
from bridge import (
    Bridge,
    Command,
    Status,
    make_bridge,
    make_local_bridge,
    new_command,
    validate_command,
)


def command(action: str, seq: int = 1, **params: object) -> Command:
    return Command(action, dict(params), seq, time.monotonic())


def test_new_command_sequence_increases_and_is_stamped() -> None:
    first, second = new_command("stand"), new_command("walk", {"direction": "fwd"})
    assert second.seq > first.seq
    assert abs(time.monotonic() - second.timestamp) < 1.0


def _echo(bridge: Bridge) -> None:  # runs in a spawned child
    for item in bridge.drain():
        bridge.report(Status("accepted", item.seq, dict(item.params), 1, item.timestamp))


def test_messages_round_trip_through_a_real_mp_queue() -> None:
    bridge = make_bridge()
    sent = Command("walk", {"direction": "fwd", "speed": 0.5}, 41, 812.304)
    assert bridge.send(sent)
    process = mp.get_context("spawn").Process(target=_echo, args=(bridge,))
    process.start()
    reply = bridge.receive(timeout=20.0)
    process.join(5.0)
    assert reply == Status("accepted", 41, {"direction": "fwd", "speed": 0.5}, 1, 812.304)


@pytest.mark.parametrize(
    ("action", "params", "expected"),
    [
        ("walk", {"direction": "fwd", "speed": 0.5}, None),
        ("walk", {"direction": "back"}, None),
        ("walk", {"direction": "up", "speed": 0.5}, "invalid_params"),
        ("walk", {"direction": "fwd", "speed": "fast"}, "invalid_params"),
        ("walk", {"direction": "fwd", "speed": float("nan")}, "invalid_params"),
        ("walk", {"direction": "fwd", "speed": True}, "invalid_params"),
        ("turn", {"direction": "left", "angle_deg": 90}, None),
        ("turn", {"direction": "left"}, "invalid_params"),
        ("turn", {"direction": "around", "angle_deg": 90}, "invalid_params"),
        ("stand", {}, None),
        ("stop", {}, None),
        ("heartbeat", {}, None),
        ("dance", {}, "unknown_action"),
    ],
)
def test_validate_command(action: str, params: dict, expected: str | None) -> None:
    assert validate_command(Command(action, params, 1, 0.0)) == expected


def test_full_command_queue_drops_the_oldest_and_never_blocks() -> None:
    bridge = make_local_bridge()
    for seq in range(config.COMMAND_QUEUE_MAXSIZE + 3):
        assert bridge.send(command("stand", seq))
    assert [c.seq for c in bridge.drain()] == list(range(3, config.COMMAND_QUEUE_MAXSIZE + 3))


def test_heartbeat_is_skipped_not_queued_when_full() -> None:
    bridge = make_local_bridge()
    for seq in range(config.COMMAND_QUEUE_MAXSIZE):
        bridge.send(command("walk", seq, direction="fwd", speed=0.5))
    assert not bridge.send(command("heartbeat", 99))
    assert [c.seq for c in bridge.drain()] == list(range(config.COMMAND_QUEUE_MAXSIZE))


def test_stop_is_delivered_when_the_queue_is_full() -> None:
    bridge = make_local_bridge()
    for seq in range(config.COMMAND_QUEUE_MAXSIZE):
        bridge.send(command("walk", seq, direction="fwd", speed=0.5))
    assert bridge.send(command("stop", 100))
    assert bridge.stop_event.is_set()
    assert bridge.drain()[-1].action == "stop"
    assert bridge.take_stop() == 100
    assert bridge.take_stop() is None  # taking clears the event


def test_non_stop_commands_do_not_touch_the_stop_event() -> None:
    bridge = make_local_bridge()
    bridge.send(command("sit"))
    assert bridge.take_stop() is None


def test_report_drops_the_oldest_status_and_never_blocks() -> None:
    bridge = make_local_bridge()
    for seq in range(config.STATUS_QUEUE_MAXSIZE + 5):
        bridge.report(Status("accepted", None, {}, seq, 0.0))
    received = bridge.receive_all()
    assert len(received) == config.STATUS_QUEUE_MAXSIZE
    assert received[0].seq == 5 and received[-1].seq == config.STATUS_QUEUE_MAXSIZE + 4


def test_drain_is_bounded_so_a_flood_cannot_eat_a_tick() -> None:
    bridge = make_local_bridge()
    for seq in range(config.COMMAND_QUEUE_MAXSIZE):
        bridge.send(command("stand", seq))
    assert len(bridge.drain(limit=3)) == 3
    assert len(bridge.drain()) == config.COMMAND_QUEUE_MAXSIZE - 3


def test_garbage_in_the_queue_is_ignored() -> None:
    bridge = make_local_bridge()
    bridge.command_queue.put_nowait("not a command")
    bridge.send(command("stand"))
    assert [c.action for c in bridge.drain()] == ["stand"]
