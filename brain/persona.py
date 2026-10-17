"""Arya's character: three talking modes, a small mood, and the system prompt.

The persona changes ONLY how Arya talks. It never touches the router, a command, the body or any
safety rule: ``route()`` runs first and is not told which mode is on. The character sheet (name,
the three modes with a description, a style guide and example exchanges, the mood lines) lives in
``data/persona/arya.toml``; the hard limits are in ``data/persona/limits.toml`` and enforced in
code by ``brain/output_check.py`` as well as written into every prompt here.

- ``Persona.system_prompt(mode, mood, straight)``: the whole system prompt. ``straight`` is true
  when the person said "seriously" / "just tell me" or asked a safety, health or real-help
  question: Arya answers plainly, even in roast mode.
- ``Mood``: neutral, happy (greeted by name), bored (long idle), busy (many commands). It changes
  ONE line of the prompt. Clock injected; the idle state is worked out when it is read.
"""

from __future__ import annotations

import threading
import time
import tomllib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import config
from brain.router import normalize


class PersonaError(ValueError):
    """The character sheet is missing something (the message says what)."""


@dataclass(frozen=True)
class Example:
    user: str
    arya: str


@dataclass(frozen=True)
class ModeSheet:
    name: str
    description: str
    style: str
    examples: tuple[Example, ...]


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {normalize(text)} "
    return any(f" {normalize(phrase)} " in padded for phrase in phrases)


class Persona:
    """The loaded character sheet and the prompt builder. Immutable after loading."""

    def __init__(self, data: dict[str, Any]) -> None:
        try:
            self.name: str = str(data["name"])
            self.character: str = str(data["character"]).strip()
            self.hard_limits: str = str(data["hard_limits"]).strip()
            self.straight_triggers = tuple(str(x) for x in data["straight_triggers"])
            self.help_triggers = tuple(str(x) for x in data["help_triggers"])
            self.mood_lines: dict[str, str] = {k: str(v) for k, v in data["mood"].items()}
            self.modes: dict[str, ModeSheet] = {}
            for mode, sheet in data["modes"].items():
                examples = tuple(Example(str(e["user"]), str(e["arya"]))
                                 for e in sheet.get("examples", []))
                self.modes[mode] = ModeSheet(mode, str(sheet["description"]).strip(),
                                             str(sheet["style"]).strip(), examples)
        except KeyError as error:
            raise PersonaError(f"the character sheet has no {error}") from error
        for mode in config.PERSONA_MODES:
            if mode not in self.modes:
                raise PersonaError(f"the character sheet has no mode {mode!r}")
        for mood in config.MOOD_STATES:
            if mood not in self.mood_lines:
                raise PersonaError(f"the character sheet has no mood line for {mood!r}")

    @classmethod
    def from_file(cls, path: Path = config.PERSONA_FILE) -> Persona:
        with open(path, "rb") as handle:
            return cls(tomllib.load(handle))

    # -- questions about what a person said ---------------------------------------------------
    def wants_straight_answer(self, text: str) -> bool:
        """"seriously", "just tell me" ...: the real answer, even in roast mode."""
        return _contains_any(text, self.straight_triggers)

    def is_help_question(self, text: str) -> bool:
        """A safety, health or real-help question: a straight answer in every mode."""
        return _contains_any(text, self.help_triggers)

    # -- the prompt ---------------------------------------------------------------------------
    def system_prompt(self, mode: str, mood: str = "neutral", straight: bool = False) -> str:
        """The whole system prompt for one reply (kept short: the model is small)."""
        sheet = self.modes.get(mode) or self.modes[config.PERSONA_DEFAULT_MODE]
        lines = [self.character]
        if straight:  # the mode's own style is left out: it would pull the answer the wrong way
            lines.append("Right now the person needs a plain, honest, kind answer: no teasing, "
                         "no jokes, no dodging. Answer the question directly.")
        else:
            lines += [f"Mode: {sheet.name}. {sheet.description}", sheet.style]
        lines.append(self.hard_limits)
        lines.append(self.mood_lines.get(mood, self.mood_lines["neutral"]))
        examples = sheet.examples[:config.PERSONA_EXAMPLES_IN_PROMPT]
        if examples and not straight:
            lines.append("Examples:")
            for example in examples:
                lines.append(f"User: {example.user}")
                lines.append(f"{self.name}: {example.arya}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def default_persona() -> Persona:
    return Persona.from_file()


class Mood:
    """neutral, happy, bored or busy, from simple events. Thread-safe, clock injected.

    Events: ``greeted`` (greeted by name: happy for ``MOOD_HAPPY_S``), ``heard`` (anyone spoke:
    ends boredom and restarts the idle timer), ``command`` (a motion command: busy when there were
    ``MOOD_BUSY_COMMANDS`` within ``MOOD_BUSY_WINDOW_S``). Bored is derived: nothing heard for
    ``MOOD_BORED_AFTER_S``. Priority: busy, happy, bored, neutral.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last_heard = clock()
        self._happy_until = float("-inf")
        self._commands: deque[float] = deque()

    def greeted(self) -> None:
        with self._lock:
            self._happy_until = self._clock() + config.MOOD_HAPPY_S
            self._last_heard = self._clock()

    def heard(self) -> None:
        with self._lock:
            self._last_heard = self._clock()

    def command(self) -> None:
        with self._lock:
            now = self._clock()
            self._commands.append(now)
            self._last_heard = now
            self._trim(now)

    def _trim(self, now: float) -> None:
        while self._commands and now - self._commands[0] > config.MOOD_BUSY_WINDOW_S:
            self._commands.popleft()

    @property
    def state(self) -> str:
        with self._lock:
            now = self._clock()
            self._trim(now)
            if len(self._commands) >= config.MOOD_BUSY_COMMANDS:
                return "busy"
            if now < self._happy_until:
                return "happy"
            if now - self._last_heard >= config.MOOD_BORED_AFTER_S:
                return "bored"
            return "neutral"


def is_greeting_by_name(text: str, name: str = "arya") -> bool:
    """"hello Arya", "hey arya", "good morning Arya": a greeting that uses her name."""
    padded = f" {normalize(text)} "
    if f" {normalize(name)} " not in padded:
        return False
    return any(f" {greeting} " in padded for greeting in
               ("hello", "hi", "hey", "hiya", "morning", "evening", "afternoon", "namaste"))
