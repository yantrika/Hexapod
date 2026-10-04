"""Step 8b: which recognizer to trust. Pure functions; the hypotheses below are the REAL results
the grammar recognizer gave on Piper-rendered speech (see test_voice_loop.py for the live run)."""

from __future__ import annotations

import pytest

import config
from brain.router import route
from brain.stt_decision import (
    PATH_FREE,
    PATH_GRAMMAR_COMMAND,
    PATH_GRAMMAR_STOP,
    decide,
)
from voice.stt import Hypothesis, command_grammar, merge_hypotheses


def hyp(*words: tuple[str, float]) -> Hypothesis:
    return Hypothesis(" ".join(word for word, _ in words), tuple(words))


def test_the_grammar_is_built_from_config_only() -> None:
    grammar = command_grammar()
    assert grammar[-1] == "[unk]"
    for phrase in (*config.ROUTER_PHRASES, *config.ROUTER_ALIASES, *config.STOP_WORDS,
                   *config.ROUTER_FILLERS):
        assert phrase in grammar
    assert len(grammar) == len(set(grammar))  # no duplicates


def test_mean_conf_ignores_unknown_words() -> None:
    assert hyp(("walk", 0.55), ("[unk]", 1.0)).mean_conf == pytest.approx(0.55)
    assert hyp(("[unk]", 1.0)).mean_conf == 0.0
    assert Hypothesis().mean_conf == 0.0


def test_merge_joins_segments_and_skips_empty_ones() -> None:
    merged = merge_hypotheses([hyp(("sit", 0.9)), Hypothesis(), hyp(("down", 0.8))])
    assert merged.text == "sit down" and merged.words == (("sit", 0.9), ("down", 0.8))
    assert merge_hypotheses([]) == Hypothesis()


# --- the grammar rescues what the free-text recognizer mishears --------------------------------
@pytest.mark.parametrize(
    ("free", "grammar", "routed"),
    [
        ("said", hyp(("[unk]", 0.83), ("sit", 0.83)), "sit"),  # US/IN: "sit" heard as "said"
        ("where've", hyp(("wave", 1.0)), "wave"),
        ("thune vast", hyp(("turn", 1.0), ("left", 1.0)), "turn left"),
        ("what forward", hyp(("walk", 0.9), ("forward", 0.95)), "walk forward"),
        ("they don't", hyp(("sit", 0.8), ("down", 0.8)), "sit down"),
        ("hexa please sit down", hyp(("hexa", 1.0), ("please", 1.0), ("sit", 1.0), ("down", 1.0)),
         "sit down"),
    ],
)
def test_a_confident_grammar_command_wins_over_a_mishearing(
    free: str, grammar: Hypothesis, routed: str
) -> None:
    decision = decide(free, grammar)
    assert decision.path == PATH_GRAMMAR_COMMAND and decision.text == routed
    assert route(decision.text).kind == "command"


def test_a_grammar_stop_stops_even_when_the_free_text_misheard_it() -> None:
    decision = decide("start", hyp(("stop", 1.0)))  # the Indian model heard "stop" as "start"
    assert decision.path == PATH_GRAMMAR_STOP and decision.text == "stop"
    assert route(decision.text).kind == "stop"
    assert decide("sob", hyp(("stop", config.STT_STOP_CONF + 0.01))).path == PATH_GRAMMAR_STOP


def test_a_stop_word_in_the_free_text_always_stops() -> None:
    decision = decide("I can't stop laughing", hyp(("[unk]", 1.0)))
    assert route(decision.text).kind == "stop"  # the router's usual safe side
    assert decide("hexa stop", None).text == "hexa stop"


def test_rejecting_a_grammar_stop_never_loses_a_stop_the_free_text_heard() -> None:
    weak = decide("stop", hyp(("stop", config.STT_STOP_CONF - 0.2)))
    assert weak.path == PATH_FREE and route(weak.text).kind == "stop"
    long = decide("please just stop right there now okay", hyp(("stop", 1.0)))
    assert route(long.text).kind == "stop"
    # both recognizers agree: the grammar path is reported
    assert decide("stop", hyp(("stop", 1.0))).path == PATH_GRAMMAR_STOP
    # a stop word in the free text beats a grammar command
    assert route(decide("stop", hyp(("walk", 1.0))).text).kind == "stop"


# --- the guards: the grammar FORCES matches on ordinary speech ---------------------------------
FORCED = [  # (what was said, free text, the grammar result it was forced to)
    ("I sat down for lunch", "i sat down for launch",
     hyp(("sat", 1.0), ("down", 1.0), ("forward", 1.0))),          # IN: not a known phrase
    ("what is the weather today", "what is the weather today",
     hyp(("walk", 1.0), ("forward", 1.0), ("could", 0.85), ("hey", 1.0))),  # IN, conf 1.0!
    ("I'll walk you through it", "i'll walk you through it",
     hyp(("walk", 1.0), ("you", 1.0), ("forward", 0.86))),         # IN
    ("what is the weather today", "what is the weather today",
     hyp(("walk", 0.55), ("[unk]", 1.0))),                         # US: weak, long free text
    ("turn up the music", "turn up the music", hyp(("turn", 0.61), ("up", 0.61), ("[unk]", 1.0))),
    ("turn up the music", "turn up the music",
     hyp(("turn", 1.0), ("up", 1.0), ("move", 0.88), ("back", 0.88))),  # IN
    ("turn left and then walk forward for a while", "turn left and then walk forward for a while",
     hyp(("turn", 1.0), ("left", 1.0), ("[unk]", 1.0), ("walk", 1.0), ("forward", 1.0))),
    ("can I walk now", "can i walk now", hyp(("[unk]", 1.0), ("[unk]", 1.0), ("walk", 1.0))),
]


@pytest.mark.parametrize(("said", "free", "grammar"), FORCED)
def test_a_forced_grammar_match_on_ordinary_speech_does_not_become_a_command(
    said: str, free: str, grammar: Hypothesis
) -> None:
    decision = decide(free, grammar)
    print(f"{said!r}: grammar was forced to {grammar.text!r} -> path {decision.path} "
          f"({decision.reason})")
    assert decision.path == PATH_FREE and decision.text == free
    assert route(decision.text).kind == "chat"  # and the router agrees it is chat


def test_a_forced_whoa_on_chat_is_not_a_stop() -> None:
    """IN model: "tell me a joke" was forced to "whoa" at 0.71. Not enough to explain 4 words."""
    decision = decide("tell me a joke", hyp(("whoa", 0.71)))
    assert decision.path == PATH_FREE and route(decision.text).is_chat


def test_a_weak_grammar_command_is_ignored() -> None:
    weak = config.STT_GRAMMAR_CONF - 0.1
    decision = decide("what", hyp(("walk", weak)))
    assert decision.path == PATH_FREE and "too weak" in decision.reason
    assert decide("what", hyp(("walk", config.STT_GRAMMAR_CONF + 0.05))).path == (
        PATH_GRAMMAR_COMMAND)


def test_a_weak_grammar_stop_is_ignored() -> None:
    decision = decide("tap", hyp(("stop", config.STT_STOP_CONF - 0.1)))
    assert decision.path == PATH_FREE and "too weak" in decision.reason


def test_too_many_unknown_words_means_not_a_command() -> None:
    decision = decide("so I", hyp(("[unk]", 1.0), ("[unk]", 1.0), ("sit", 1.0)))
    assert decision.path == PATH_FREE and "unknown" in decision.reason


def test_a_long_free_text_is_never_overridden_by_a_command() -> None:
    long_free = "please would you walk forward for me " + "and so on " * 3
    decision = decide(long_free, hyp(("walk", 1.0), ("forward", 1.0)))
    assert decision.path == PATH_FREE and decision.text == long_free


def test_without_a_grammar_result_the_free_text_is_used() -> None:
    assert decide("sit down", None).path == PATH_FREE
    assert decide("sit down", Hypothesis()).path == PATH_FREE
    assert decide("sit down", hyp(("[unk]", 1.0))).path == PATH_FREE


def test_the_thresholds_come_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STT_GRAMMAR_CONF", 0.99)
    assert decide("said", hyp(("sit", 0.9))).path == PATH_FREE
    monkeypatch.setattr(config, "STT_GRAMMAR_CONF", 0.5)
    assert decide("said", hyp(("sit", 0.9))).path == PATH_GRAMMAR_COMMAND
