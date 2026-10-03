"""Utterance to command: keyword and fuzzy matching. No LLM, no I/O, no clock.

``route(text)`` returns a ``RouteResult``:

- ``stop``: a stop word anywhere in the utterance (highest priority; deliberately
  trigger-happy, "do not stop talking" still stops the robot).
- ``command``: the utterance, after filler removal, is at most ``ROUTER_MAX_WORDS``
  words and matches a phrase in ``ROUTER_PHRASES``: a single word must match a phrase
  EXACTLY (fuzzing one short word turns "sand" into "stand"); two or more words need a
  ``fuzz.ratio`` of at least ``ROUTER_THRESHOLD``. (``ratio``, not ``partial_ratio``:
  partial matching fires on ordinary sentences.) Natural near-forms of single words
  ("waves") are explicit aliases in the table.
- ``chat``: everything else; the original text goes to the chat model.

Numbers are not parsed in v1 and the router never produces strafe or yaw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

import config

_NON_WORD = re.compile(r"[^a-z0-9\s]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class RouteResult:
    """What ``route`` decided, and why."""

    kind: str  # "stop" | "command" | "chat"
    text: str  # the original utterance
    action: str | None = None  # command action (stop included)
    params: dict[str, Any] = field(default_factory=dict)
    phrase: str | None = None  # the stop word or phrase that matched
    score: float = 0.0  # fuzz.ratio of the match (100 for a stop word)
    normalized: str = ""  # lowercased, punctuation and fillers removed

    @property
    def is_chat(self) -> bool:
        return self.kind == "chat"


def normalize(text: str) -> str:
    """Lowercase, drop punctuation (apostrophes vanish: ``can't`` -> ``cant``), single spaces."""
    text = _NON_WORD.sub(" ", text.lower().replace("'", "").replace("’", ""))
    return _SPACES.sub(" ", text).strip()


def remove_fillers(normalized: str, fillers: tuple[str, ...] = config.ROUTER_FILLERS) -> str:
    """Remove whole-word filler words and phrases (``can you``, ``please``, ...)."""
    padded = f" {normalized} "
    for filler in sorted(fillers, key=len, reverse=True):  # longest first
        padded = re.sub(rf" {re.escape(filler)} ", " ", padded)
        padded = re.sub(rf" {re.escape(filler)} ", " ", padded)  # adjacent fillers share a space
    return _SPACES.sub(" ", padded).strip()


def _stop_match(normalized: str) -> str | None:
    padded = f" {normalized} "
    for word in config.STOP_WORDS:
        if f" {word} " in padded:
            return word
    return None


def best_phrase(candidate: str) -> tuple[str, float]:
    """The phrase in the table closest to *candidate* and its ``fuzz.ratio`` score."""
    best, best_score = "", 0.0
    for phrase in config.ROUTER_PHRASES:
        score = float(fuzz.ratio(candidate, phrase))
        if score > best_score:
            best, best_score = phrase, score
    return best, best_score


def route(text: str) -> RouteResult:
    """Decide what an utterance means (see the module docstring)."""
    normalized = normalize(text)
    stop = _stop_match(normalized)
    if stop is not None:
        return RouteResult("stop", text, "stop", {}, stop, 100.0, normalized)
    candidate = remove_fillers(normalized)
    words = candidate.split()
    if not words or len(words) > config.ROUTER_MAX_WORDS:
        return RouteResult("chat", text, normalized=candidate)
    alias = config.ROUTER_ALIASES.get(candidate)
    if alias is not None:
        return RouteResult("command", text, alias[0], dict(alias[1]), candidate, 100.0, candidate)
    phrase, score = best_phrase(candidate)
    if len(words) == 1:
        matched = candidate in config.ROUTER_PHRASES
    else:
        matched = score >= config.ROUTER_THRESHOLD
    if matched:
        action, params = config.ROUTER_PHRASES[phrase]
        return RouteResult("command", text, action, dict(params), phrase, score, candidate)
    return RouteResult("chat", text, phrase=phrase, score=score, normalized=candidate)
