"""The hard limits as code: a small check on every reply sentence before the TTS.

The persona prompt asks a small model to behave, but a small model forgets. This module is the
second line: each sentence the model produced is checked against the word lists in
``data/persona/limits.toml`` and, if it fails, ``ChatResponder`` drops it and speaks a neutral
pre-rendered line instead. The same lists reject unsuitable FACTS before the memory saves them
(``fact_problem``).

What is checked (see the data file for the words):

- ``slur`` and ``threat``: always fail.
- A sensitive topic (looks, body, skin, race, religion, caste, gender, sexuality, disability,
  family, health): ``always`` words fail anywhere; ``directed`` words fail only when the sentence
  is about the listener ("you", "your" ...), so "Diwali is a Hindu festival" is fine and "you
  look so tired" is not; ``patterns`` are regular expressions ("your mother").
- ``third_party``: an insult word aimed at someone who is not the listener (a roast only ever
  targets the person who is talking to Arya).
- A safety, health or real-help question gets a straight answer in every mode, so for such a
  question (``help_context=True``) only slurs, threats and the ``always`` words are enforced.

It fails closed (a false alarm costs one neutral sentence). Pure: no I/O after the file is
loaded, no clock. The log line names the category, never the text.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import config

_LEET = str.maketrans({"@": "a", "$": "s", "0": "o", "1": "i", "3": "e", "5": "s", "7": "t"})
_NON_WORD = re.compile(r"[^a-z\s]")
_SPACES = re.compile(r"\s+")
_REPEATS = re.compile(r"(.)\1{2,}")


def clean(text: str) -> str:
    """Lowercase, letter tricks undone (``sh1t`` -> ``shit``), apostrophes dropped (``you're`` ->
    ``youre``), everything but letters turned into single spaces, three or more of the same letter
    made one (``fuuuck``)."""
    text = text.lower().replace("'", "").replace("’", "").translate(_LEET)
    text = _REPEATS.sub(r"\1", _NON_WORD.sub(" ", text))
    return _SPACES.sub(" ", text).strip()


def _spelled_out(text: str) -> str:
    """Words spelled letter by letter ("b i t c h") glued back together, so they can be matched.
    Every suffix of a run of three or more single letters is added ("a b i t c h" holds the
    article "a" and the word "bitch")."""
    tokens, extra, run = text.split(), [], []
    for token in [*tokens, ""]:
        if len(token) == 1:
            run.append(token)
            continue
        if len(run) >= 3:
            extra += ["".join(run[start:]) for start in range(len(run) - 2)]
        run = []
    return " ".join([text, *extra]) if extra else text


def _word_regex(words: list[str]) -> re.Pattern[str] | None:
    """One regex for whole words and phrases; a plural ``s`` or ``es`` still matches."""
    parts = [re.escape(clean(word)) for word in words if clean(word)]
    if not parts:
        return None
    parts.sort(key=len, reverse=True)
    return re.compile(rf"\b(?:{'|'.join(parts)})(?:s|es)?\b")


@dataclass(frozen=True)
class Verdict:
    """The result of a check. ``category`` is only ever a category name, never the text."""

    ok: bool
    category: str = ""


PASS = Verdict(True)


@dataclass(frozen=True)
class _Topic:
    name: str
    always: re.Pattern[str] | None
    directed: re.Pattern[str] | None
    patterns: tuple[re.Pattern[str], ...]


def _compile_patterns(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in patterns)


class Limits:
    """The word lists of ``data/persona/limits.toml``, compiled."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.listener = _word_regex(list(data["listener_words"]))
        self.third_party = _word_regex(list(data["third_party_words"]))
        self.third_party_nouns = _word_regex(list(data["third_party_nouns"]))
        self.insults = _word_regex(list(data["insult_words"]))
        self.slurs = _word_regex(list(data["slurs"]["words"]))
        self.threats = _compile_patterns(list(data["threats"]["patterns"]))
        self.topics = tuple(
            _Topic(name, _word_regex(list(topic.get("always", []))),
                   _word_regex(list(topic.get("directed", []))),
                   _compile_patterns(list(topic.get("patterns", []))))
            for name, topic in data["topics"].items()
        )
        self.fact_patterns = tuple(
            re.compile(pattern, re.IGNORECASE) for pattern in data["facts"]["patterns"])

    @classmethod
    def from_file(cls, path: Path = config.PERSONA_LIMITS_FILE) -> Limits:
        with open(path, "rb") as handle:
            return cls(tomllib.load(handle))

    @property
    def topic_names(self) -> tuple[str, ...]:
        return tuple(topic.name for topic in self.topics)


@lru_cache(maxsize=1)
def default_limits() -> Limits:
    return Limits.from_file()


class OutputCheck:
    """Check one sentence (``check``) or one fact (``fact_problem``)."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or default_limits()

    def check(self, sentence: str, help_context: bool = False) -> Verdict:
        """``Verdict(ok=False, category=...)`` if *sentence* breaks a hard limit."""
        limits = self.limits
        text = clean(sentence)
        if not text:
            return PASS
        if limits.slurs is not None and limits.slurs.search(_spelled_out(text)):
            return Verdict(False, "slur")
        if any(pattern.search(text) for pattern in limits.threats):
            return Verdict(False, "threat")
        for topic in limits.topics:
            if topic.always is not None and topic.always.search(text):
                return Verdict(False, topic.name)
        about_listener = limits.listener is not None and bool(limits.listener.search(text))
        if help_context:  # a straight answer to a safety, health or real-help question
            return self._third_party(text, about_listener)
        for topic in limits.topics:
            if about_listener and topic.directed is not None and topic.directed.search(text):
                return Verdict(False, topic.name)
            if any(pattern.search(text) for pattern in topic.patterns):
                return Verdict(False, topic.name)
        return self._third_party(text, about_listener)

    def _third_party(self, text: str, about_listener: bool) -> Verdict:
        """An insult aimed at someone who is not the listener (a roast never targets them)."""
        limits = self.limits
        if limits.insults is None or not limits.insults.search(text):
            return PASS
        if limits.third_party_nouns is not None and limits.third_party_nouns.search(text):
            return Verdict(False, "third_party")
        if (limits.third_party is not None and limits.third_party.search(text)
                and not about_listener):
            return Verdict(False, "third_party")
        return PASS

    def fact_problem(self, fact: str) -> str | None:
        """The category that makes *fact* unfit to remember, or None. A fact has no listener, so
        every word of every list counts."""
        limits = self.limits
        text = clean(fact)
        if limits.slurs is not None and limits.slurs.search(text):
            return "slur"
        if any(pattern.search(text) for pattern in limits.threats):
            return "threat"
        for topic in limits.topics:
            for words in (topic.always, topic.directed):
                if words is not None and words.search(text):
                    return topic.name
            if any(pattern.search(text) for pattern in topic.patterns):
                return topic.name
        if any(pattern.search(fact) for pattern in limits.fact_patterns):
            return "private"
        return None
