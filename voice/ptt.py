"""Push-to-talk: the user decides when the recognizers listen.

``ptt_step`` is the state machine as a pure function (no clock, no threads): a state, an event and
the time in, the next state out. ``PushToTalk`` wraps it with a lock and an injected clock so any
thread may call ``press()`` / ``release()`` (a terminal key, the control window, later the phone
page of Step 12).

    idle --press--> listening --release--> tail --(PTT_TAIL_S passes)--> idle
                        ^                    |
                        +------ press -------+     (the same utterance goes on)

Audio is fed to the recognizers while LISTENING and during the TAIL; in IDLE it is dropped, so
Vosk spends no CPU. ``PushToTalk.poll()`` is for the one thread that reads the microphone: it
reports whether to feed the next block, whether a new utterance just started (reset the
recognizer first) and whether one just ended (flush the recognizer).

A press calls the ``on_press`` listeners BEFORE the state changes, so barge-in (silence hexa,
cancel the chat reply) is finished by the time the STT thread sees ``started``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

IDLE, LISTENING, TAIL = "idle", "listening", "tail"
PRESS, RELEASE, TICK = "press", "release", "tick"


@dataclass(frozen=True)
class PttState:
    phase: str = IDLE
    tail_until: float = 0.0  # only meaningful in TAIL


@dataclass(frozen=True)
class PttStep:
    state: PttState
    started: bool = False  # a new utterance begins (reset the recognizer)
    finished: bool = False  # the utterance is over (flush the recognizer)


def ptt_step(state: PttState, event: str, now: float, tail_s: float) -> PttStep:
    """The next state. ``press``/``release`` are the user; ``tick`` just lets time pass."""
    if event == PRESS:
        if state.phase == IDLE:
            return PttStep(PttState(LISTENING), started=True)
        if state.phase == TAIL:  # pressed again before the tail ended: one utterance
            return PttStep(PttState(LISTENING))
        return PttStep(state)  # already listening: a double press changes nothing
    if event == RELEASE:
        if state.phase == LISTENING:
            return PttStep(PttState(TAIL, now + tail_s))
        return PttStep(state)
    if state.phase == TAIL and now >= state.tail_until:
        return PttStep(PttState(IDLE), finished=True)
    return PttStep(state)


def feeding(state: PttState) -> bool:
    return state.phase != IDLE


@dataclass(frozen=True)
class PttPoll:
    feed: bool
    started: bool
    finished: bool


class PushToTalk:
    """Thread-safe push-to-talk (see the module docstring)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        tail_s: float = config.PTT_TAIL_S,
    ) -> None:
        self._clock = clock
        self.tail_s = tail_s
        self._lock = threading.Lock()
        self._state = PttState()
        self._started = False  # a start not yet reported by poll()
        self._finished = False
        self._press_listeners: list[Callable[[], None]] = []
        self._change_listeners: list[Callable[[str], None]] = []
        self.presses = 0

    # -- listeners -----------------------------------------------------------------------------
    def add_press_listener(self, listener: Callable[[], None]) -> None:
        """Call *listener* on every press, before listening starts (barge-in)."""
        self._press_listeners.append(listener)

    def add_change_listener(self, listener: Callable[[str], None]) -> None:
        """Call *listener* with ``"listening"`` / ``"idle"`` when the indicator should change."""
        self._change_listeners.append(listener)

    # -- the user's side -----------------------------------------------------------------------
    def press(self) -> None:
        self.presses += 1
        for listener in self._press_listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - a failing barge-in must not stop listening
                logger.exception("press listener failed")
        self._apply(PRESS)

    def release(self) -> None:
        self._apply(RELEASE)

    def toggle(self) -> bool:
        """For a terminal (no key-release events): listening on or off. True if now listening."""
        if self.active:
            self.release()
            return False
        self.press()
        return True

    @property
    def active(self) -> bool:
        """True while the user is holding / has toggled listening on (not during the tail)."""
        with self._lock:
            return self._state.phase == LISTENING

    @property
    def phase(self) -> str:
        with self._lock:
            return self._state.phase

    # -- the microphone thread's side ----------------------------------------------------------
    def poll(self) -> PttPoll:
        """Call once per mic block: whether to feed it, and any start / end of an utterance."""
        self._apply(TICK)
        with self._lock:
            started, self._started = self._started, False
            finished, self._finished = self._finished, False
            return PttPoll(feeding(self._state), started, finished)

    # -- internals -----------------------------------------------------------------------------
    def _apply(self, event: str) -> None:
        with self._lock:
            before = self._state.phase
            step = ptt_step(self._state, event, self._clock(), self.tail_s)
            self._state = step.state
            self._started = self._started or step.started
            self._finished = self._finished or step.finished
            after = step.state.phase
        shown_before, shown_after = before != IDLE, after != IDLE
        if shown_before != shown_after:
            for listener in self._change_listeners:
                try:
                    listener(LISTENING if shown_after else IDLE)
                except Exception:  # noqa: BLE001
                    logger.exception("change listener failed")


def make_ptt(
    mode: str | None = None, clock: Callable[[], float] = time.monotonic
) -> PushToTalk | None:
    """A ``PushToTalk`` for ``"ptt"``, None for ``"always"`` (default: ``config.LISTEN_MODE``)."""
    mode = config.LISTEN_MODE if mode is None else mode
    if mode == "ptt":
        return PushToTalk(clock)
    if mode == "always":
        return None
    raise ValueError(f"LISTEN_MODE must be 'ptt' or 'always', not {mode!r}")
