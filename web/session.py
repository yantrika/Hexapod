"""Who may drive and when the robot must stop: the safety logic of the phone page.

Pure and clock-injected, so tests need no network and no sleeping. ``PinGuard`` checks the PIN
(constant time) and locks an address out after repeated failures. ``WebControl`` lets ONE client
drive at a time, turns validated requests into bridge commands through an injected ``send``,
and enforces the deadman on the SERVER: a moving robot gets ``stop`` when no move message arrives
within ``deadman_s`` (the page's own stop on blur/hidden/cancel is only a second line), and a
disconnect always sends ``stop``. ``stop`` is never rate-limited or merged: it goes out at once
(the ``Bridge`` puts it on the ``stop_event`` fast path).

Step 12b adds hold-to-talk and typed text. ``ptt_press`` / ``ptt_release`` / ``say`` are NOT
bridge messages: they go to ``VoiceControls`` (``HexaApp.set_listening`` and the same routing as
spoken text). The server owns the safety: a press is forced to release after ``ptt_max_s``; a
disconnect and every ``stop`` message (the page sends one on blur and when hidden) release too;
only the controller may press, and only a press made here is ever released here.
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import config
from web.protocol import POSTURE_ACTIONS, Request, walk_params

Sender = Callable[[str, dict[str, Any]], "int | None"]


class Refused(Exception):
    """A valid request that cannot be done now; ``str(error)`` is the reason shown on the page."""


@dataclass(frozen=True)
class VoiceControls:
    """What the page may do with the voice side (``HexaApp`` provides it).

    ``ptt_unavailable`` / ``say_unavailable`` are the reason it cannot be used (None: it can),
    shown on the page, e.g. "the server runs with --no-mic".
    """

    set_listening: Callable[[bool], None]
    say: Callable[[str], None]
    ptt_unavailable: str | None = None
    say_unavailable: str | None = None


NO_VOICE = VoiceControls(
    lambda on: None, lambda text: None, "voice is not enabled", "voice is not enabled")
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
        voice: VoiceControls = NO_VOICE,
        ptt_max_s: float = config.WEB_PTT_MAX_S,
        say_min_interval_s: float = config.WEB_SAY_MIN_INTERVAL_S,
    ) -> None:
        self._send = send
        self._voice = voice
        self._ptt_max_s = ptt_max_s
        self._say_min_interval_s = say_min_interval_s
        self._ptt_lock = threading.RLock()  # apart from _lock: a slow press never delays a stop
        self._listening_since: float | None = None
        self._last_say_at = float("-inf")
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

    @property
    def listening(self) -> bool:
        """True while a web press is held (and not yet released or forced to release)."""
        return self._listening_since is not None

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
        self.release_listening()
        return True

    def handle(self, client: object, request: Request) -> bool:
        """Act on a validated request. False if *client* is not the controller (ignored). Raises
        ``Refused`` for a hold-to-talk or ``say`` that cannot be done now."""
        with self._lock:
            if self._controller is not client:
                return False
        if request.action in ("ptt_press", "ptt_release", "say"):
            self._voice_request(request)
            return True
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
        if request.action == "stop":
            self.release_listening()  # after the stop is out: STOP also ends listening
        return True

    def tick(self) -> bool:
        """The deadman. True if it just fired (sent ``stop``)."""
        with self._lock:
            if not self._moving or self._clock() - self._last_move_at < self._deadman_s:
                return False
            self._halt()
            return True

    def expire_listening(self) -> bool:
        """The listening limit. True if it just forced a release."""
        with self._ptt_lock:
            since = self._listening_since
            if since is None or self._clock() - since < self._ptt_max_s:
                return False
            self.release_listening()
            return True

    # --- voice (hold-to-talk and typed text) ------------------------------------------
    def _voice_request(self, request: Request) -> None:
        if request.action == "ptt_release":
            self.release_listening()  # always allowed: letting go is never refused
            return
        if request.action == "ptt_press":
            if self._voice.ptt_unavailable:
                raise Refused(self._voice.ptt_unavailable)
            with self._ptt_lock:
                if self._listening_since is None:  # a repeat press must not extend the limit
                    self._listening_since = self._clock()
                    try:
                        self._voice.set_listening(True)
                    except Exception:
                        self._listening_since = None
                        raise
            return
        if self._voice.say_unavailable:
            raise Refused(self._voice.say_unavailable)
        now = self._clock()
        with self._ptt_lock:
            if now - self._last_say_at < self._say_min_interval_s:
                raise Refused("too fast: wait a moment")
            self._last_say_at = now
        self._voice.say(request.text)

    def release_listening(self) -> None:
        """Stop listening if the web started it. Idempotent."""
        with self._ptt_lock:
            if self._listening_since is None:
                return
            self._listening_since = None
            self._voice.set_listening(False)

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
