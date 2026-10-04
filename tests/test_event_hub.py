"""Step 12b: the brain event hub: ordered, bounded, drop-oldest, never blocking a publisher."""

from __future__ import annotations

import threading
import time

from brain.event_hub import EventHub


def test_events_reach_every_subscriber_in_order() -> None:
    hub = EventHub()
    first, second = hub.subscribe("a"), hub.subscribe("b")
    for number in range(5):
        hub.publish({"type": "heard", "text": str(number)})
    expected = [{"type": "heard", "text": str(number)} for number in range(5)]
    assert first.get_all() == expected and second.get_all() == expected
    assert first.get_all() == []  # drained


def test_a_slow_subscriber_loses_the_oldest_events_and_keeps_the_newest_in_order() -> None:
    hub = EventHub()
    slow, fast = hub.subscribe("slow", maxsize=4), hub.subscribe("fast", maxsize=100)
    for number in range(10):
        hub.publish({"n": number})
    assert [event["n"] for event in slow.get_all()] == [6, 7, 8, 9]
    assert slow.dropped == 6
    assert [event["n"] for event in fast.get_all()] == list(range(10))  # others are unaffected
    assert fast.dropped == 0


def test_publishing_never_blocks_even_when_nobody_reads() -> None:
    hub = EventHub()
    stuck = hub.subscribe("stuck", maxsize=8)
    start = time.monotonic()
    for number in range(20000):
        hub.publish({"n": number})
    assert time.monotonic() - start < 2.0
    assert [event["n"] for event in stuck.get_all()] == list(range(19992, 20000))


def test_an_unsubscribed_listener_gets_nothing_more() -> None:
    hub = EventHub()
    subscription = hub.subscribe("x")
    hub.publish({"n": 1})
    hub.unsubscribe(subscription)
    hub.unsubscribe(subscription)  # idempotent
    hub.publish({"n": 2})
    assert [event["n"] for event in subscription.get_all()] == [1]


def test_publishing_from_many_threads_loses_nothing_within_the_bound() -> None:
    hub = EventHub()
    subscription = hub.subscribe("all", maxsize=1000)

    def publish(thread: int) -> None:
        for number in range(100):
            hub.publish({"t": thread, "n": number})

    threads = [threading.Thread(target=publish, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = subscription.get_all()
    assert len(events) == 800
    for index in range(8):  # per publisher the order is kept
        numbers = [event["n"] for event in events if event["t"] == index]
        assert numbers == list(range(100))
