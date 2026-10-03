"""One reader of the body's ``status_queue``, any number of subscribers.

The hub thread is the only thing that reads ``Bridge.status_queue``. Each status it reads is
copied to every subscriber's own bounded queue. A full queue drops its OLDEST status (never
blocks), so a stuck subscriber cannot stall the hub, the other subscribers or the body.
Subscribers: the motion keeper / speech worker, the CLIs and control window, the web UI later.
"""

from __future__ import annotations

import logging
import queue
import threading

import config
from bridge import Bridge, Status

logger = logging.getLogger(__name__)


class Subscription:
    """A subscriber's private, bounded, drop-oldest queue of statuses."""

    def __init__(self, name: str, maxsize: int = config.STATUS_SUBSCRIBER_MAXSIZE) -> None:
        self.name = name
        self.dropped = 0
        self._queue: queue.Queue[Status] = queue.Queue(maxsize=maxsize)

    def put(self, status: Status) -> None:
        """Add *status*, dropping the oldest one if full. Never blocks."""
        while True:
            try:
                self._queue.put_nowait(status)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass

    def get(self, timeout: float | None = None) -> Status | None:
        """The next status, waiting up to *timeout* seconds (None: do not wait)."""
        try:
            if timeout is None:
                return self._queue.get_nowait()
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_all(self) -> list[Status]:
        """Every status that is ready now."""
        out = []
        while (status := self.get()) is not None:
            out.append(status)
        return out


class StatusHub:
    """Reads the bridge's statuses on one thread and publishes them to every subscriber."""

    def __init__(self, bridge: Bridge, poll_s: float = config.STATUS_HUB_POLL_S) -> None:
        self._bridge = bridge
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._subscribers: list[Subscription] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def subscribe(
        self, name: str, maxsize: int = config.STATUS_SUBSCRIBER_MAXSIZE
    ) -> Subscription:
        subscription = Subscription(name, maxsize)
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)

    def publish(self, status: Status) -> None:
        """Copy *status* to every subscriber (also used directly by tests)."""
        with self._lock:
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            subscription.put(status)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="status-hub", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("status hub did not stop within %.1f s", timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                status = self._bridge.receive(timeout=self._poll_s)
            except Exception:  # noqa: BLE001 - a broken queue must not kill the hub thread
                logger.exception("status hub could not read the status queue")
                self._stop.wait(self._poll_s)
                continue
            if status is not None:
                self.publish(status)
