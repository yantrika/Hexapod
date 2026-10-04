"""Who may drive and when the robot must stop: the safety logic of the phone page.

Pure and clock-injected, so tests need no network and no sleeping. ``PinGuard`` checks the PIN
(constant time) and locks an address out after repeated failures. ``WebControl`` lets ONE client
drive at a time, turns validated requests into bridge commands through an injected ``send``,
and enforces the deadman on the SERVER: a moving robot gets ``stop`` when no move message arrives
within ``deadman_s`` (the page's own stop on blur/hidden/cancel is only a second line), and a
disconnect always sends ``stop``. ``stop`` is never rate-limited or merged: it goes out at once
(the ``Bridge`` puts it on the ``stop_event`` fast path).
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from typing import Any

import config
from web.protocol import POSTURE_ACTIONS, Request, walk_params

Sender = Callable[[str, dict[str, Any]], "int | None"]
_MAX_TRACKED_HOSTS = 256


class PinGuard:
    """PIN comparison plus a per-address lockout (``max_failures`` wrong tries in ``window_s``)."""

    def __init__(
        self,
        pin: str,
        clock: Callable[[], float],
        max_failures: int = config.WEB_PIN_MAX_FAILURES,
        window_s: float = config.WEB_PIN_WINDOW_S,
        lockout_s: float = config.WEB_PIN_LOCKOUT_S,
    ) -> None:
        self._pin = pin.encode()
        self._clock = clock
        self._max = max_failures
        self._window = window_s
        self._lockout = lockout_s
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, host: str, supplied: str | None) -> str:
        """``"ok"``, ``"bad_pin"`` or ``"locked"`` (a locked address is refused even with the
        right PIN, so a guess cannot be confirmed during the lockout)."""
        now = self._clock()
        with self._lock:
            if self._locked_until.get(host, 0.0) > now:
                return "locked"
            self._locked_until.pop(host, None)
            matches = hmac.compare_digest(self._pin, (supplied or "").encode())
            if matches:
                self._failures.pop(host, None)
                return "ok"
            recent = [t for t in self._failures.get(host, []) if now - t < self._window]
            recent.append(now)
            if len(recent) >= self._max:
                self._locked_until[host] = now + self._lockout
                recent = []
            self._failures[host] = recent
            self._prune(now)
            return "bad_pin"

    def _prune(self, now: float) -> None:
        if len(self._failures) <= _MAX_TRACKED_HOSTS:
            return
        for host in [h for h, times in self._failures.items()
                     if not times or now - times[-1] >= self._window]:
            del self._failures[host]
        for host in [h for h, until in self._locked_until.items() if until <= now]:
            del self._locked_until[host]


class WebControl:
    """One controller at a time, request to command, deadman and stop-on-disconnect."""

    def __init__(
        self,
        send: Sender,
        clock: Callable[[], float],
        deadman_s: float = config.WEB_DEADMAN_S,
        min_forward_s: float = config.WEB_MIN_FORWARD_S,
    ) -> None:
        self._send = send
        self._clock = clock
        self._deadman_s = deadman_s
        self._min_forward_s = min_forward_s
        self._lock = threading.RLock()
        self._controller: object | None = None
        self._moving = False
        self._last_params: dict[str, Any] | None = None
        self._last_move_at = 0.0
        self._last_forward_at = 0.0

    @property
    def controller(self) -> object | None:
        return self._controller

    @property
    def moving(self) -> bool:
        return self._moving

    def claim(self, client: object) -> bool:
        """Take the controller slot; False if another client holds it."""
        with self._lock:
            if self._controller is not None and self._controller is not client:
                return False
            self._controller = client
            return True

    def disconnect(self, client: object) -> bool:
        """The client is gone: free the slot and ALWAYS send ``stop`` if it was in control."""
        with self._lock:
            if self._controller is not client:
                return False
            self._controller = None
            self._halt()
            return True

    def handle(self, client: object, request: Request) -> bool:
        """Act on a validated request. False if *client* is not the controller (ignored)."""
        with self._lock:
            if self._controller is not client:
                return False
            if request.action == "stop":
                self._halt()
            elif request.action in POSTURE_ACTIONS:
                self._moving = False
                self._last_params = None
                self._send(request.action, {})
            else:
                self._walk(request)
            return True

    def tick(self) -> bool:
        """The deadman. True if it just fired (sent ``stop``)."""
        with self._lock:
            if not self._moving or self._clock() - self._last_move_at < self._deadman_s:
                return False
            self._halt()
            return True

    # --- internals (the lock is held) ------------------------------------------------
    def _halt(self) -> None:
        """``stop``, at once, on the ``stop_event`` path; forget any held move."""
        self._moving = False
        self._last_params = None
        self._send("stop", {})

    def _walk(self, request: Request) -> None:
        now = self._clock()
        params = walk_params(request)
        if not request.moving:
            if self._moving:  # every button released: ramp the walk down to a halt
                self._moving = False
                self._last_params = None
                self._send("walk", params)
            return
        self._last_move_at = now  # any move message feeds the deadman, sent on or not
        if not self._moving or params != self._last_params:
            self._moving = True
            self._last_params = params
            self._last_forward_at = now
            self._send("walk", params)
        elif now - self._last_forward_at >= self._min_forward_s:
            self._last_forward_at = now
            self._send("heartbeat", {})
