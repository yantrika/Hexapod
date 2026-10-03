"""Step 8: one reader of the status queue, many subscribers, a stuck one blocks nobody."""

from __future__ import annotations

import threading
import time

import config
from brain.brain_loop import BrainLoop
from brain.status_hub import StatusHub
from bridge import Status, make_local_bridge
from tests.fakes import wait_until


def status(seq: int, kind: str = "accepted") -> Status:
    return Status(status=kind, ref_seq=seq, detail={}, seq=seq, timestamp=time.time())


def test_two_subscribers_both_receive_everything_in_order() -> None:
    hub = StatusHub(make_local_bridge())
    first, second = hub.subscribe("first"), hub.subscribe("second")
    for n in range(5):
        hub.publish(status(n))
    assert [s.seq for s in first.get_all()] == [0, 1, 2, 3, 4]
    assert [s.seq for s in second.get_all()] == [0, 1, 2, 3, 4]


def test_the_hub_thread_is_the_only_reader_of_the_bridge() -> None:
    bridge = make_local_bridge()
    hub = StatusHub(bridge)
    a, b = hub.subscribe("a"), hub.subscribe("b")
    hub.start()
    try:
        for n in range(6):
            bridge.report(status(n))
        wait_until(lambda: b._queue.qsize() == 6, what="the hub to publish")
        assert [s.seq for s in a.get_all()] == list(range(6))
        assert [s.seq for s in b.get_all()] == list(range(6))
        assert bridge.receive() is None  # nothing is left for a second reader
    finally:
        hub.stop()


def test_a_stuck_subscriber_blocks_neither_the_hub_nor_the_others() -> None:
    hub = StatusHub(make_local_bridge())
    stuck = hub.subscribe("stuck", maxsize=2)  # nobody ever reads it
    healthy = hub.subscribe("healthy", maxsize=500)
    started = time.perf_counter()
    for n in range(300):
        hub.publish(status(n))
    assert time.perf_counter() - started < 1.0
    assert [s.seq for s in healthy.get_all()] == list(range(300))
    assert [s.seq for s in stuck.get_all()] == [298, 299]  # the newest survive
    assert stuck.dropped == 298


def test_unsubscribe_stops_delivery() -> None:
    hub = StatusHub(make_local_bridge())
    sub = hub.subscribe("gone")
    hub.unsubscribe(sub)
    hub.publish(status(1))
    assert sub.get_all() == []


def test_stop_leaves_no_thread_and_is_repeatable() -> None:
    hub = StatusHub(make_local_bridge())
    hub.start()
    hub.stop()
    hub.stop()
    assert not [t for t in threading.enumerate() if t.name == "status-hub"]


def test_a_broken_queue_does_not_kill_the_hub_thread() -> None:
    bridge = make_local_bridge()
    hub = StatusHub(bridge, poll_s=0.01)
    sub = hub.subscribe("s")
    original = bridge.receive
    calls = {"n": 0}

    def flaky(timeout: float | None = None) -> Status | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("synthetic queue failure")
        return original(timeout)

    object.__setattr__(bridge, "receive", flaky)
    hub.start()
    try:
        bridge.report(status(7))
        wait_until(lambda: sub._queue.qsize() == 1, what="delivery after the error")
    finally:
        hub.stop()


def test_brain_loop_shares_a_hub_with_other_subscribers() -> None:
    bridge = make_local_bridge()
    hub = StatusHub(bridge)
    hub.start()
    printer = hub.subscribe("printer")
    brain = BrainLoop(bridge, hub=hub)
    try:
        brain.handle_text("walk forward")
        command = bridge.drain()[0]
        bridge.report(Status("accepted", command.seq, {}, 1, time.time()))
        wait_until(lambda: brain.keeper.held_seq is not None and printer._queue.qsize() == 1)
        deadline = time.monotonic() + 2
        while not brain.keeper.active and time.monotonic() < deadline:
            brain.pump()
            time.sleep(0.01)
        assert brain.keeper.active  # the keeper saw the status ...
        assert [s.status for s in printer.get_all()] == ["accepted"]  # ... and so did the printer
    finally:
        brain.close()
        hub.stop()
    assert config.STATUS_SUBSCRIBER_MAXSIZE > 0
