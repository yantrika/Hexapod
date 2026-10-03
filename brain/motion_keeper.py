"""Keeps a text- or voice-started walk or turn alive: the brain's half of the watchdog contract.

A ``walk`` is continuous, and a ``turn`` runs on the same velocity until it reaches its
angle, so the body stops either after ``WATCHDOG_TIMEOUT_S`` without a command or
heartbeat. A front end that sends one ``walk`` or ``turn`` therefore needs something
sending heartbeats. ``MotionKeeper`` is that something: it follows what was sent and what
the body answered, and while the motion is accepted and running it says when a heartbeat
is due. It does no I/O and has no clock of its own (the clock is injected), so it is unit
tested with a fake clock. ``stop`` never goes through it: front ends send stop immediately.

It sends ``heartbeat`` messages rather than repeating the ``walk``: a repeat would be
answered ``accepted`` five times a second, and would restart a walk the body had already
ended (watchdog, fall).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import config
from bridge import Command, Status, new_command

logger = logging.getLogger(__name__)

HELD_ACTIONS = ("walk", "turn")  # actions the body watchdog stops without heartbeats


class MotionKeeper:
    """Tracks one held walk; ``tick()`` returns the heartbeats that are due."""

    def __init__(
        self,
        clock: Callable[[], float],
        max_walk_s: float = config.VOICE_WALK_MAX_S,
        heartbeat_hz: float = config.HEARTBEAT_HZ,
    ) -> None:
        self._clock = clock
        self._max_walk_s = max_walk_s
        self._period = 1.0 / heartbeat_hz
        self._seq: int | None = None  # the walk being held (sent, maybe not yet accepted)
        self._accepted_at: float | None = None
        self._next_beat = 0.0

    @property
    def active(self) -> bool:
        """True while heartbeats are being sent (the walk was accepted and is still held)."""
        return self._seq is not None and self._accepted_at is not None

    @property
    def held_seq(self) -> int | None:
        return self._seq

    def on_sent(self, command: Command) -> None:
        """A command went to the body: a walk becomes the held one, anything else ends it."""
        if command.action in HELD_ACTIONS:  # a turn is velocity-driven too: the watchdog applies
            self._seq, self._accepted_at = command.seq, None
        elif command.action != "heartbeat":
            self.clear("superseded by " + command.action)

    def on_status(self, status: Status) -> None:
        """Follow the body's answer to the held walk (or a fall, which is about the body)."""
        if status.status == "fallen":
            self.clear("fallen")
        elif status.status in ("rejected", "error", "busy") and status.ref_seq in (self._seq, None):
            self.clear(status.status)
        elif status.status == "accepted" and status.ref_seq == self._seq and self._seq is not None:
            now = self._clock()
            self._accepted_at, self._next_beat = now, now + self._period
        elif status.status == "done":
            self.clear("done")

    def tick(self) -> list[Command]:
        """Heartbeats due now (at most one); clears the walk when it has run too long."""
        if not self.active:
            return []
        assert self._accepted_at is not None
        now = self._clock()
        if now - self._accepted_at >= self._max_walk_s:
            self.clear(f"walk reached {self._max_walk_s:.0f} s")  # the body's watchdog stops it
            return []
        if now >= self._next_beat:
            self._next_beat = now + self._period
            return [new_command("heartbeat")]
        return []

    def clear(self, reason: str = "") -> None:
        if self._seq is not None:
            logger.info("motion keeper released the walk (%s)", reason)
        self._seq = self._accepted_at = None
