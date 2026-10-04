"""Speech recognition: a Vosk wrapper and the self-hearing gate. No microphone code here.

``VoskStt.feed(block)`` returns the partial and final results for one audio block;
``reset()`` clears the recognizers. ``SelfHearingGate`` decides which blocks may be fed at all:
audio captured while hexa speaks must never reach the recognizer, or it would obey itself.

``VoskStt`` loads ONE model and runs TWO recognizers on the same blocks: a free-text one (partials,
chat) and a grammar one limited to the router's phrases (``command_grammar()``, built from
config). A final event carries both hypotheses with word confidences; ``brain/stt_decision.py``
decides which to trust.

The ``speaking`` Event from ``voice/playback.py`` stays set until the sound ends PLUS
``SPEAK_TAIL_S``, so the tail lives there; the gate does not add a second one. A block is
discarded if speaking was set when it arrived OR when the previous block arrived (a block spans
the moment the Event changed, so its first part may still hold the end of hexa's voice). The
first block after that is accepted, and the recognizer is reset before it so no half-heard
partial from before the speech can leak into the next result.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import config
from voice.audio import Block

logger = logging.getLogger(__name__)


class SttError(RuntimeError):
    """The recognizer could not be created or run (the message says what to do)."""


UNKNOWN = "[unk]"  # what a grammar recognizer says for a word outside its grammar


@dataclass(frozen=True)
class Hypothesis:
    """One recognizer's text for an utterance, with a confidence for each word."""

    text: str = ""
    words: tuple[tuple[str, float], ...] = field(default_factory=tuple)

    @property
    def mean_conf(self) -> float:
        """Mean confidence of the real words (``[unk]`` is not a word we claim to know)."""
        confs = [conf for word, conf in self.words if word != UNKNOWN]
        return sum(confs) / len(confs) if confs else 0.0


def parse_hypothesis(raw: str) -> Hypothesis:
    """A ``Hypothesis`` from Vosk's JSON (``SetWords(True)`` puts the confidences in it)."""
    data = json.loads(raw)
    items = data.get("result", [])
    words = tuple((str(item["word"]), float(item.get("conf", 0.0))) for item in items)
    return Hypothesis(str(data.get("text", "")), words)


def merge_hypotheses(parts: list[Hypothesis]) -> Hypothesis:
    parts = [part for part in parts if part.text]
    if not parts:
        return Hypothesis()
    return Hypothesis(" ".join(p.text for p in parts), tuple(w for p in parts for w in p.words))


def command_grammar() -> list[str]:
    """The grammar phrases, from config only: router phrases, aliases, stop words, fillers."""
    entries = {*config.ROUTER_PHRASES, *config.ROUTER_ALIASES, *config.STOP_WORDS,
               *config.ROUTER_FILLERS}
    return sorted(entries) + [UNKNOWN]


@contextlib.contextmanager
def _quiet_stderr() -> Iterator[None]:
    """Kaldi prints warnings (a grammar word missing from a model) straight to fd 2."""
    sys.stderr.flush()
    saved = os.dup(2)
    try:
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


@dataclass(frozen=True)
class SttEvent:
    """A recognition result: ``partial`` (still being spoken) or ``final`` (utterance ended).

    ``text`` is always the free-text recognizer's. A final also carries both hypotheses with
    confidences (``grammar`` is None when the grammar recognizer is off)."""

    kind: str  # "partial" | "final" | "reset"
    text: str
    timestamp: float  # clock time when the result was produced
    free: Hypothesis | None = None
    grammar: Hypothesis | None = None


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
    """One Vosk model, a free-text recognizer and (optionally) a grammar recognizer on the same
    audio. Partials come from the free recognizer and are reported only when they change."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
        grammar: bool | None = None,
    ) -> None:
        self.model_path = resolve_model_path(model_path)
        self.sample_rate = sample_rate
        self.use_grammar = config.STT_USE_GRAMMAR if grammar is None else grammar
        self._clock = clock
        self._last_partial = ""
        self._grammar_segments: list[Hypothesis] = []
        self._grammar_recognizer: Any = None
        if not self.model_path.is_dir():
            raise SttError(
                f"Vosk model not found: {self.model_path}. Run scripts/fetch_models.sh"
            )
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            self._model = Model(str(self.model_path))
            self._recognizer: Any = KaldiRecognizer(self._model, sample_rate)
            self._recognizer.SetWords(True)
            if self.use_grammar:
                with _quiet_stderr():
                    self._grammar_recognizer = KaldiRecognizer(
                        self._model, sample_rate, json.dumps(command_grammar())
                    )
                self._grammar_recognizer.SetWords(True)
        except Exception as error:  # noqa: BLE001 - Vosk raises plain Exception on a bad model
            raise SttError(f"could not load the Vosk model {self.model_path}: {error}") from error

    def feed(self, block: Block) -> list[SttEvent]:
        data = block.tobytes()
        events: list[SttEvent] = []
        grammar = self._grammar_recognizer
        if grammar is not None and grammar.AcceptWaveform(data):  # its own endpoint came first
            segment = parse_hypothesis(grammar.Result())
            if segment.text:
                self._grammar_segments.append(segment)
        if self._recognizer.AcceptWaveform(data):
            free = parse_hypothesis(self._recognizer.Result())
            self._last_partial = ""
            events.append(SttEvent("final", free.text, self._clock(), free, self._take_grammar()))
        else:
            text = json.loads(self._recognizer.PartialResult()).get("partial", "")
            if text and text != self._last_partial:
                self._last_partial = text
                events.append(SttEvent("partial", text, self._clock()))
        return events

    def _take_grammar(self) -> Hypothesis | None:
        """The grammar recognizer's text for the utterance the free recognizer just ended."""
        grammar = self._grammar_recognizer
        if grammar is None:
            return None
        segment = parse_hypothesis(grammar.FinalResult())  # flush what it has not ended yet
        merged = merge_hypotheses([*self._grammar_segments, segment])
        self._grammar_segments = []
        return merged

    def reset(self) -> None:
        """Clear BOTH recognizers: nothing heard before a gap may leak into the next result."""
        self._recognizer.Reset()
        if self._grammar_recognizer is not None:
            self._grammar_recognizer.Reset()
        self._grammar_segments = []
        self._last_partial = ""

    def flush(self) -> list[SttEvent]:
        """The result for audio fed so far (end of a file or of push-to-talk)."""
        free = parse_hypothesis(self._recognizer.FinalResult())
        grammar = self._take_grammar()
        self._last_partial = ""
        return [SttEvent("final", free.text, self._clock(), free, grammar)] if free.text else []


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

    def forget(self) -> None:
        """A new push-to-talk utterance starts: what was heard before it does not count."""
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
