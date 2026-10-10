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
- ``persona`` (Step 13): "my name is X", "forget me", "what do you remember about me", "roast
  mode", "stop roasting". Code in the brain acts on them, never the LLM, and they are looked at
  only AFTER the stop check, so every phrase above routes exactly as before. The one exception:
  the whole utterance "stop roasting" (``config.PERSONA_STOP_ROAST_PHRASES``) switches to calm
  mode instead of stopping the robot. (The voice loop adds a real stop when the body is moving:
  see ``is_stop_roast``.)

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

    kind: str  # "stop" | "command" | "chat" | "persona"
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


_NAME_WORD = r"[a-z]+"
_NAME_FORMS = (  # (pattern on the filler-free text, how many words the name may have)
    (re.compile(rf"^(?:you can )?call me ({_NAME_WORD}(?: {_NAME_WORD})?)$"), 2),
    (re.compile(rf"^my name(?: is|s) ({_NAME_WORD}(?: {_NAME_WORD})?)$"), 2),
    (re.compile(rf"^(?:i am|im) ({_NAME_WORD})$"), 1),
)
_MODE = re.compile(r"^(?:(?:switch|go|change|set) (?:to|into) )?(?:the )?"
                   r"(calm|come|funny|roast) mode(?: on)?$")


def _name_in(text: str) -> str | None:
    """The name in "my name is X", "call me X" or "I am X", or None ("I am hungry" is chat)."""
    for pattern, max_words in _NAME_FORMS:
        found = pattern.match(text)
        if found is None:
            continue
        words = found.group(1).split()
        if len(words) > max_words or any(
                word in config.ROUTER_NAME_STOPWORDS or word.endswith("ing") for word in words):
            return None
        return " ".join(words)
    return None


def _persona_route(text: str, candidate: str, normalized: str) -> RouteResult | None:
    """A persona request in the filler-free *candidate*, or None."""
    def result(action: str, params: dict[str, Any]) -> RouteResult:
        return RouteResult("persona", text, action, params, candidate, 100.0, candidate)

    if candidate in config.PERSONA_FORGET_PHRASES:
        return result("forget_me", {})
    if candidate in config.PERSONA_RECALL_PHRASES:
        return result("recall_me", {})
    if candidate == "roast me":
        return result("set_mode", {"mode": "roast"})
    found = _MODE.match(candidate)
    if found is not None:
        mode = found.group(1)
        return result("set_mode", {"mode": config.PERSONA_MODE_HEARD_AS.get(mode, mode)})
    name = _name_in(candidate)
    if name is not None:
        return result("set_speaker", {"name": name.title()})
    return None


def is_yes(text: str) -> bool:
    """A "yes" (to "say yes to confirm"). Exact: "yes I did that" is not one."""
    return remove_fillers(normalize(text)) in config.PERSONA_YES_WORDS


def is_no(text: str) -> bool:
    return remove_fillers(normalize(text)) in config.PERSONA_NO_WORDS


def _drop_wake_variant(candidate: str) -> str:
    """A leading "aria" (how Vosk small hears "Arya") is dropped when more words follow."""
    first, _, rest = candidate.partition(" ")
    return rest if rest and first in config.ROUTER_WAKE_VARIANTS else candidate


def is_stop_roast(result: RouteResult) -> bool:
    """True for the whole-utterance "stop roasting" route (a mode switch, not a stop word)."""
    return (result.kind == "persona" and result.action == "set_mode"
            and result.phrase in config.PERSONA_STOP_ROAST_PHRASES)


def route(text: str) -> RouteResult:
    """Decide what an utterance means (see the module docstring)."""
    normalized = normalize(text)
    candidate = _drop_wake_variant(remove_fillers(normalized))
    if candidate in config.PERSONA_STOP_ROAST_PHRASES:  # the one whole-utterance exception
        return RouteResult("persona", text, "set_mode", {"mode": "calm"}, candidate, 100.0,
                           candidate)
    stop = _stop_match(normalized)
    if stop is not None:
        return RouteResult("stop", text, "stop", {}, stop, 100.0, normalized)
    persona = _persona_route(text, candidate, normalized)
    if persona is not None:
        return persona
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
