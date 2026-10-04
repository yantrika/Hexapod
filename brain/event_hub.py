"""Brain events (what hexa heard, decided and said) for any number of listeners.

Like ``StatusHub`` but for plain dicts and without a reader thread: any thread calls
``publish(event)`` and the event is copied to every subscriber's own bounded queue. A full queue
drops its OLDEST event (``publish`` never blocks), so a slow phone can never stall the voice loop,
the playback or the router. The phone page's server is the subscriber today.

Events are small JSON-ready dicts with a ``type``: ``listening`` (``on``), ``heard`` (``text``),
``route`` (``route``, ``action``, ``text``, ``early_stop``) and ``said`` (``text``).
"""

from __future__ import annotations

import collections
import threading
from typing import Any

import config

Event = dict[str, Any]


class EventSubscription:
    """A subscriber's private, bounded, drop-oldest queue of events."""

    def __init__(self, name: str, maxsize: int = config.WEB_EVENT_QUEUE_SIZE) -> None:
        self.name = name
        self.dropped = 0
        self._events: collections.deque[Event] = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()

    def put(self, event: Event) -> None:
        with self._lock:
            if len(self._events) == self._events.maxlen:
                self.dropped += 1  # the deque discards the oldest on append
            self._events.append(event)

    def get_all(self) -> list[Event]:
        """Every event that is ready now, oldest first."""
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[EventSubscription] = []

    def subscribe(
        self, name: str, maxsize: int = config.WEB_EVENT_QUEUE_SIZE
    ) -> EventSubscription:
        subscription = EventSubscription(name, maxsize)
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def publish(self, event: Event) -> None:
        """Copy *event* to every subscriber. Any thread; never blocks."""
        with self._lock:
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            subscription.put(event)
