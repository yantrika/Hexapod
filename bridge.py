"""Typed messages and the ``Bridge`` handle joining the brain and body processes.

The brain calls ``bridge.send(command)`` and ``bridge.receive()``. The body calls
``bridge.drain()`` and ``bridge.report(status)``. Neither side ever blocks on the
other: both queues are small and bounded, a full queue drops its oldest message,
and ``stop`` additionally travels on a shared event (plus its sequence number) so it
cannot be lost to a full or backed-up queue.

Timestamps are ``time.monotonic()``, which is system-wide on Linux, so they compare
across the two processes on one host.
"""

from __future__ import annotations

import itertools
import logging
import math
import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)

MOTION_ACTIONS = ("stand", "sit", "walk", "turn", "wave")
ACTIONS = (*MOTION_ACTIONS, "stop", "heartbeat")
STATUS_KINDS = ("accepted", "rejected", "done", "busy", "fallen", "error")
REJECT_REASONS = (
    "stale", "superseded", "already_in_state", "invalid_params",
    "invalid_state", "unknown_action", "fallen",
)
_PUT_ATTEMPTS = 3
_PUT_RETRY_S = 0.0005


@dataclass(frozen=True)
class Command:
    """Brain to body. ``seq`` increases strictly; ``timestamp`` is ``time.monotonic()``."""

    action: str
    params: dict[str, Any]
    seq: int
    timestamp: float


@dataclass(frozen=True)
class Status:
    """Body to brain. ``ref_seq`` is the command this answers (None if unsolicited)."""

    status: str
    ref_seq: int | None
    detail: dict[str, Any]
    seq: int
    timestamp: float


_command_seq = itertools.count(1)  # next() is atomic under the GIL: safe from several threads


def new_command(action: str, params: dict[str, Any] | None = None) -> Command:
    """Build a command with the next sequence number and the current timestamp."""
    return Command(action, dict(params or {}), next(_command_seq), time.monotonic())


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _walk_params_valid(params: dict[str, Any]) -> bool:
    """``direction`` + ``speed`` as before; optional ``strafe`` / ``yaw`` in [-1, 1].

    ``direction`` is only optional when ``strafe`` or ``yaw`` is given.
    """
    extras = [params[key] for key in ("strafe", "yaw") if key in params]
    if not all(_is_number(value) and abs(value) <= 1.0 for value in extras):
        return False
    if "direction" in params or not extras:
        if params.get("direction") not in ("fwd", "back"):
            return False
    return _is_number(params.get("speed", 0.5))


def validate_command(command: Command) -> str | None:
    """Return a rejection reason (``unknown_action`` / ``invalid_params``) or None if valid.

    Out-of-range numbers are valid here; the controller clamps them.
    """
    if command.action not in ACTIONS:
        return "unknown_action"
    params = command.params
    if not isinstance(params, dict):
        return "invalid_params"
    if command.action == "walk":
        ok = _walk_params_valid(params)
    elif command.action == "turn":
        ok = params.get("direction") in ("left", "right") and _is_number(params.get("angle_deg"))
    else:
        ok = True
    return None if ok else "invalid_params"


def _put_drop_oldest(target: Any, item: object, *, skip_if_full: bool = False) -> bool:
    """``put_nowait``; when full, drop the oldest item and retry. Never blocks.

    With ``skip_if_full`` the new item is the one dropped (heartbeats). Returns
    whether *item* was queued.
    """
    for attempt in range(_PUT_ATTEMPTS):
        try:
            target.put_nowait(item)
            return True
        except queue.Full:
            if skip_if_full:
                return False
            try:
                target.get_nowait()
                logger.debug("queue full: dropped the oldest message")
            except queue.Empty:  # the consumer just emptied it, or a put is still in flight
                pass
            if attempt + 1 < _PUT_ATTEMPTS:
                time.sleep(_PUT_RETRY_S)
    logger.warning("queue stayed full: message dropped")
    return False


@dataclass(frozen=True)
class Bridge:
    """The only thing the two processes share. Picklable, so it can be handed to a spawn."""

    command_queue: Any
    status_queue: Any
    stop_event: Any
    stop_seq: Any  # shared int: seq of the latest stop, so the fast path can answer it
    shutdown_event: Any
    ready_event: Any  # set by the body once its backend is up

    # --- brain side ---------------------------------------------------------
    def send(self, command: Command) -> bool:
        """Queue *command*; for ``stop`` also set ``stop_event``. Returns False if dropped."""
        if command.action == "stop":
            with self.stop_seq.get_lock():
                self.stop_seq.value = command.seq
            self.stop_event.set()
        return _put_drop_oldest(
            self.command_queue, command, skip_if_full=command.action == "heartbeat"
        )

    def receive(self, timeout: float | None = None) -> Status | None:
        """The next status, waiting up to *timeout* seconds (None: do not wait)."""
        try:
            if timeout is None:
                item = self.status_queue.get_nowait()
            else:
                item = self.status_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return item if isinstance(item, Status) else None

    def receive_all(self) -> list[Status]:
        """Every status that is ready now."""
        out = []
        while (status := self.receive()) is not None:
            out.append(status)
        return out

    def request_shutdown(self) -> None:
        self.shutdown_event.set()

    def wait_ready(self, timeout: float = config.BODY_START_TIMEOUT_S) -> bool:
        return bool(self.ready_event.wait(timeout))

    # --- body side ----------------------------------------------------------
    def drain(self, limit: int = config.BRIDGE_DRAIN_LIMIT) -> list[Command]:
        """Every queued command (at most *limit*, so a flood cannot eat a whole tick)."""
        out: list[Command] = []
        while len(out) < limit:
            try:
                item = self.command_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, Command):
                out.append(item)
            else:
                logger.warning("ignoring a non-Command message: %r", type(item))
        return out

    def report(self, status: Status) -> None:
        """Queue *status* for the brain; drops the oldest status when full, never blocks."""
        _put_drop_oldest(self.status_queue, status)

    def take_stop(self) -> int | None:
        """The stop fast path: if ``stop_event`` is set, clear it and return the stop's seq (0
        if unknown); otherwise None."""
        if not self.stop_event.is_set():
            return None
        self.stop_event.clear()
        return int(self.stop_seq.value)

    def shutdown_requested(self) -> bool:
        return bool(self.shutdown_event.is_set())

    def mark_ready(self) -> None:
        self.ready_event.set()


def make_bridge(context: Any = None) -> Bridge:
    """A bridge on ``multiprocessing`` objects (spawn context by default)."""
    ctx = context if context is not None else mp.get_context("spawn")
    return Bridge(
        command_queue=ctx.Queue(maxsize=config.COMMAND_QUEUE_MAXSIZE),
        status_queue=ctx.Queue(maxsize=config.STATUS_QUEUE_MAXSIZE),
        stop_event=ctx.Event(),
        stop_seq=ctx.Value("q", 0),
        shutdown_event=ctx.Event(),
        ready_event=ctx.Event(),
    )


class _LocalValue:
    """A ``multiprocessing.Value`` look-alike for single-process tests."""

    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def get_lock(self) -> threading.Lock:
        return self._lock


def make_local_bridge() -> Bridge:
    """A bridge on ``queue.Queue`` and ``threading.Event``: deterministic, for in-process tests."""
    return Bridge(
        command_queue=queue.Queue(maxsize=config.COMMAND_QUEUE_MAXSIZE),
        status_queue=queue.Queue(maxsize=config.STATUS_QUEUE_MAXSIZE),
        stop_event=threading.Event(),
        stop_seq=_LocalValue(),
        shutdown_event=threading.Event(),
        ready_event=threading.Event(),
    )
