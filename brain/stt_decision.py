"""Which recognizer to trust for a final result: the free-text one or the command grammar.

The grammar recognizer is limited to the router's phrases, so it hears "sit" where the free one
says "said". But it also FORCES a match on ordinary speech ("what is the weather today" came
back as "walk forward could hey" with confidence 1.0 on the Indian English model), so a grammar
result is trusted only if it passes every guard below. Pure functions: no I/O, no clock.

Order (first that applies wins):
  1. The grammar says a stop word (after filler removal, an exact stop word or phrase) with mean
     confidence >= ``STT_STOP_CONF`` and the free text is not much longer than it
     (<= ``STT_STOP_EXTRA_WORDS`` more words): stop.
  1b. Otherwise a stop word anywhere in the free text stops (the router's usual safe side), so
     rejecting a grammar stop can never lose a stop the free text heard.
  2. The grammar says exactly one known command phrase or alias (``[unk]`` ignored, at most
     ``STT_GRAMMAR_EXTRA_WORDS`` of them), the free text is short (<= ``ROUTER_MAX_WORDS`` words
     after filler removal and <= ``STT_GRAMMAR_EXTRA_WORDS`` more words than the phrase) and the
     mean word confidence >= ``STT_GRAMMAR_CONF``: that command.
  3. Otherwise the free text goes to the normal router (a command, or usually chat).
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from brain.router import normalize, remove_fillers, route
from voice.stt import UNKNOWN, Hypothesis

PATH_FREE = "free"
PATH_GRAMMAR_STOP = "grammar-stop"
PATH_GRAMMAR_COMMAND = "grammar-command"
PATH_EARLY_STOP = "partial-stop"  # a stop word in a partial result (decided in the voice loop)


@dataclass(frozen=True)
class Decision:
    path: str  # PATH_FREE | PATH_GRAMMAR_STOP | PATH_GRAMMAR_COMMAND
    text: str  # what to hand to ``route()``
    reason: str  # one line saying why (logged)


def _word_count(text: str) -> int:
    return len(remove_fillers(normalize(text)).split())


def decide(free_text: str, grammar: Hypothesis | None) -> Decision:
    """Pick the text to route for a final result (see the module docstring)."""
    free_decision = Decision(PATH_FREE, free_text, "free text")
    free_stop = route(free_text).kind == "stop"
    stop_reason = "stop word in the free text"
    if grammar is None or not grammar.text:
        return Decision(PATH_FREE, free_text, stop_reason) if free_stop else free_decision
    tokens = grammar.text.split()
    unknowns = tokens.count(UNKNOWN)
    phrase = remove_fillers(" ".join(token for token in tokens if token != UNKNOWN))
    if not phrase:
        reason = stop_reason if free_stop else "the grammar heard nothing it knows"
        return Decision(PATH_FREE, free_text, reason)
    phrase_words = len(phrase.split())
    free_words = _word_count(free_text)
    conf = grammar.mean_conf

    if phrase in config.STOP_WORDS:
        if conf < config.STT_STOP_CONF:
            reason = f"grammar stop {phrase!r} too weak ({conf:.2f})"
        elif free_words > phrase_words + config.STT_STOP_EXTRA_WORDS:
            reason = f"grammar stop {phrase!r} does not explain {free_words} free words"
        else:
            return Decision(PATH_GRAMMAR_STOP, phrase, f"grammar stop {phrase!r} conf {conf:.2f}")
        return Decision(PATH_FREE, free_text, stop_reason if free_stop else reason)
    if free_stop:
        return Decision(PATH_FREE, free_text, stop_reason)

    known = phrase in config.ROUTER_PHRASES or phrase in config.ROUTER_ALIASES
    if not known:
        return Decision(PATH_FREE, free_text, f"grammar {phrase!r} is not a command phrase")
    if unknowns > config.STT_GRAMMAR_EXTRA_WORDS:
        return Decision(PATH_FREE, free_text, f"grammar had {unknowns} unknown words")
    if free_words > config.ROUTER_MAX_WORDS:
        return Decision(PATH_FREE, free_text, f"free text too long ({free_words} words)")
    if free_words > phrase_words + config.STT_GRAMMAR_EXTRA_WORDS:
        return Decision(PATH_FREE, free_text,
                        f"grammar {phrase!r} does not explain {free_words} free words")
    if conf < config.STT_GRAMMAR_CONF:
        return Decision(PATH_FREE, free_text, f"grammar {phrase!r} too weak ({conf:.2f})")
    return Decision(PATH_GRAMMAR_COMMAND, phrase, f"grammar command {phrase!r} conf {conf:.2f}")
