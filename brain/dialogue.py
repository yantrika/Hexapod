"""Status-driven speech: what the body answered becomes a pre-rendered phrase.

The robot speaks from the body's STATUS messages, never from assumption: "okay" is said when the
body says ``accepted``, "I'm already sitting" when it says ``rejected(already_in_state)`` in the
sitting state. Only pre-rendered phrases (``config.TTS_PHRASES``) are used, so there is no
synthesis wait. The same phrase is not repeated within ``DIALOGUE_THROTTLE_S``; ``done`` is
silent unless ``DIALOGUE_SPEAK_DONE``. The phrase table is a pure function (``phrase_for``);
``Dialogue`` adds the thread that listens to a ``StatusHub`` subscription.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

import config
from brain.status_hub import Subscription
from bridge import Command, Status
from voice.tts import TtsError

logger = logging.getLogger(__name__)

SILENT_REASONS = ("stale", "superseded")  # the user asked again: nothing to apologise for


class PhrasePlayback(Protocol):
    def say_phrase(self, name: str) -> int: ...


class Dialogue:
    def __init__(
        self,
        subscription: Subscription | None,
        playback: PhrasePlayback,
        clock: Callable[[], float] = time.monotonic,
        speak_done: bool | None = None,
        throttle_s: float | None = None,
    ) -> None:
        self.subscription = subscription
        self.playback = playback
        self._clock = clock
        self.speak_done = config.DIALOGUE_SPEAK_DONE if speak_done is None else speak_done
        self.throttle_s = config.DIALOGUE_THROTTLE_S if throttle_s is None else throttle_s
        self._sent: OrderedDict[int, str] = OrderedDict()  # seq -> action, the last 64 commands
        self._last_said: dict[str, float] = {}
        self._acknowledgements = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the table -----------------------------------------------------------------------------
    def note_sent(self, command: Command) -> None:
        """Remember what a command was, so its ``accepted`` can be told apart (stop vs walk)."""
        self._sent[command.seq] = command.action
        while len(self._sent) > 64:
            self._sent.popitem(last=False)

    def phrase_for(self, status: Status) -> str | None:
        """The phrase name for *status* (None = say nothing). Alternates okay / sure."""
        action = self._sent.get(status.ref_seq) if status.ref_seq is not None else None
        kind = status.status
        detail = status.detail
        if kind == "accepted":
            if action is None:
                return None  # not ours
            if action == "stop":
                return "stopped"
            self._acknowledgements += 1
            return "okay" if self._acknowledgements % 2 else "sure"
        if kind == "rejected":
            reason = detail.get("reason")
            if reason in SILENT_REASONS:
                return None
            if reason == "fallen":
                return "fell_over"
            if reason == "already_in_state":
                state = detail.get("state")
                return {"sitting": "already_sitting", "standing": "already_standing"}.get(
                    str(state), "cant_do_that"
                )
            return "cant_do_that"  # invalid_state, invalid_params, anything else
        if kind == "busy":
            return "one_moment"
        if kind == "fallen":
            return "fell_over"
        if kind == "error":
            return "something_wrong"
        if kind == "done":
            return "done" if self.speak_done and action is not None else None
        return None

    def handle(self, status: Status) -> str | None:
        """Speak for *status* (throttled). Returns the phrase spoken, or None."""
        phrase = self.phrase_for(status)
        if phrase is None:
            return None
        now = self._clock()
        last = self._last_said.get(phrase)
        if last is not None and now - last < self.throttle_s:
            return None  # the same phrase a moment ago
        try:
            self.playback.say_phrase(phrase)
        except TtsError as error:
            logger.error("could not say %r: %s", phrase, error)
            return None
        self._last_said[phrase] = now
        return phrase

    # -- the thread ------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None or self.subscription is None:
            return
        self._thread = threading.Thread(target=self._run, name="dialogue", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self.subscription is not None
        while not self._stop.is_set():
            status = self.subscription.get(timeout=0.1)
            if status is None:
                continue
            try:
                self.handle(status)
            except Exception:  # noqa: BLE001 - speech must never stop the status stream
                logger.exception("dialogue could not handle %r", status)

    def shutdown(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
