"""Speech recognition: a Vosk wrapper and the self-hearing gate. No microphone code here.

``VoskStt.feed(block)`` returns the partial and final results for one audio block;
``reset()`` clears the recognizer. ``SelfHearingGate`` decides which blocks may be fed at all:
audio captured while hexa speaks must never reach the recognizer, or it would obey itself.

The ``speaking`` Event from ``voice/playback.py`` stays set until the sound ends PLUS
``SPEAK_TAIL_S``, so the tail lives there; the gate does not add a second one. A block is
discarded if speaking was set when it arrived OR when the previous block arrived (a block spans
the moment the Event changed, so its first part may still hold the end of hexa's voice). The
first block after that is accepted, and the recognizer is reset before it so no half-heard
partial from before the speech can leak into the next result.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import config
from voice.audio import Block

logger = logging.getLogger(__name__)


class SttError(RuntimeError):
    """The recognizer could not be created or run (the message says what to do)."""


@dataclass(frozen=True)
class SttEvent:
    """A recognition result: ``partial`` (still being spoken) or ``final`` (utterance ended)."""

    kind: str  # "partial" | "final"
    text: str
    timestamp: float  # clock time when the result was produced


class SttEngine(Protocol):
    def feed(self, block: Block) -> list[SttEvent]: ...

    def reset(self) -> None: ...


def resolve_model_path(choice: str | Path | None = None) -> Path:
    """A model directory from a short name (``us``, ``in``), a directory name or a path."""
    if choice is None:
        return config.VOSK_MODEL_PATH
    text = str(choice)
    if text in config.VOSK_MODELS:
        return config.VOSK_DIR / config.VOSK_MODELS[text]
    path = Path(text)
    return path if path.is_absolute() or path.exists() else config.VOSK_DIR / text


class VoskStt:
    """One Vosk recognizer on one small model. Partials are reported only when they change."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model_path = resolve_model_path(model_path)
        self.sample_rate = sample_rate
        self._clock = clock
        self._last_partial = ""
        if not self.model_path.is_dir():
            raise SttError(
                f"Vosk model not found: {self.model_path}. Run scripts/fetch_models.sh"
            )
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            self._model = Model(str(self.model_path))
            self._recognizer: Any = KaldiRecognizer(self._model, sample_rate)
            self._factory = KaldiRecognizer
        except Exception as error:  # noqa: BLE001 - Vosk raises plain Exception on a bad model
            raise SttError(f"could not load the Vosk model {self.model_path}: {error}") from error

    def feed(self, block: Block) -> list[SttEvent]:
        recognizer = self._recognizer
        events: list[SttEvent] = []
        if recognizer.AcceptWaveform(block.tobytes()):
            text = json.loads(recognizer.Result()).get("text", "")
            self._last_partial = ""
            events.append(SttEvent("final", text, self._clock()))
        else:
            text = json.loads(recognizer.PartialResult()).get("partial", "")
            if text and text != self._last_partial:
                self._last_partial = text
                events.append(SttEvent("partial", text, self._clock()))
        return events

    def reset(self) -> None:
        self._recognizer.Reset()
        self._last_partial = ""

    def flush(self) -> list[SttEvent]:
        """The result for audio fed so far (end of a file or of push-to-talk)."""
        text = json.loads(self._recognizer.FinalResult()).get("text", "")
        self._last_partial = ""
        return [SttEvent("final", text, self._clock())] if text else []


@dataclass
class GateDecision:
    accept: bool
    reset: bool  # reset the recognizer before feeding this block


class SelfHearingGate:
    """Which blocks may reach the recognizer (see the module docstring). One thread uses it."""

    def __init__(self, speaking: threading.Event) -> None:
        self._speaking = speaking
        self._speaking_at_last_block = False
        self._discarding = False

    def check(self) -> GateDecision:
        """Call once per arriving block."""
        speaking = self._speaking.is_set()
        discard = speaking or self._speaking_at_last_block
        reset = self._discarding and not discard  # the first block after a gap
        self._speaking_at_last_block = speaking
        self._discarding = discard
        return GateDecision(accept=not discard, reset=reset)
