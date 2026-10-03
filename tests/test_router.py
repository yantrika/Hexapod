"""Step 6 checks: the router is a pure function (no PyBullet, no processes, no clock)."""

from __future__ import annotations

import statistics
import time

import pytest

import config
from brain.router import normalize, remove_fillers, route


def command_of(text: str) -> tuple[str, dict]:
    result = route(text)
    assert result.kind in ("command", "stop"), f"{text!r} went to chat: {result}"
    assert result.action is not None
    return result.action, result.params


# --- positive ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("stand", "stand"), ("stand up", "stand"), ("hexa please stand up", "stand"),
        ("sit down", "sit"), ("please sit", "sit"), ("Sit.", "sit"),
        ("can you wave", "wave"), ("wave", "wave"), ("could you please wave hello", "wave"),
        ("turn left", "turn"), ("Turn right!", "turn"),
        ("walk forward", "walk"), ("go forward", "walk"), ("hey hexa, walk forward please", "walk"),
        ("walk back", "walk"), ("go backward", "walk"),
        ("hexa stop", "stop"), ("STOP!", "stop"), ("halt", "stop"), ("freeze", "stop"),
        ("stop stop stop", "stop"), ("whoa", "stop"), ("please hold still", "stop"),
    ],
)
def test_positive(text: str, action: str) -> None:
    assert command_of(text)[0] == action


def test_turn_uses_the_default_angle_and_direction() -> None:
    action, params = command_of("turn left")
    expected = {"direction": "left", "angle_deg": config.TURN_DEFAULT_ANGLE_DEG}
    assert (action, params) == ("turn", expected)
    assert command_of("turn right")[1]["direction"] == "right"


def test_walk_params_are_the_original_form_never_strafe_or_yaw() -> None:
    assert command_of("walk forward")[1] == {"direction": "fwd", "speed": 0.5}
    assert command_of("walk back")[1] == {"direction": "back", "speed": 0.5}
    for action, params in config.ROUTER_PHRASES.values():
        assert "strafe" not in params and "yaw" not in params
        assert action in ("stand", "sit", "walk", "turn", "wave")


def test_returned_params_are_copies_not_the_config_dicts() -> None:
    first = route("walk forward")
    first.params["speed"] = 99
    assert route("walk forward").params["speed"] == 0.5


def test_result_says_why() -> None:
    result = route("please sit down")
    assert (result.kind, result.phrase, result.score) == ("command", "sit down", 100.0)
    assert result.normalized == "sit down" and result.text == "please sit down"
    stop = route("hexa STOP")
    assert (stop.kind, stop.phrase) == ("stop", "stop")


# --- negative: must go to chat ---------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "I sat down for lunch",
        "I'll walk you through it",
        "turn up the music",
        "how do I stand out in an interview",
        "what is a wave function",
        "what is a good way to stand out",
        "can you tell me about waves",
        "tell me a joke",
        "",
        "   ",
        "hello there",
        "what time is it",
    ],
)
def test_negative_goes_to_chat(text: str) -> None:
    result = route(text)
    assert result.is_chat and result.action is None and result.text == text


def test_stop_anywhere_is_intentional_and_errs_on_the_safe_side() -> None:
    """Documented tradeoff (plan.md defaults row 6): a stop word in ANY sentence stops the
    robot. A false stop is harmless; a missed stop is not."""
    for sentence in ("do not stop talking", "I can't stop laughing", "never stop believing"):
        result = route(sentence)
        assert result.kind == "stop", sentence


def test_stop_words_match_whole_words_only() -> None:
    assert route("stopwatch").is_chat
    assert route("the freezer is cold").is_chat
    assert route("I halted the car").is_chat


# --- normalisation --------------------------------------------------------------------------
def test_normalize() -> None:
    assert normalize("  Hexa, PLEASE   Sit-Down!! ") == "hexa please sit down"
    assert normalize("I'll") == "ill" and normalize("can’t") == "cant"


def test_remove_fillers_handles_phrases_and_adjacent_fillers() -> None:
    assert remove_fillers("hexa please can you could you wave now") == "wave"
    assert remove_fillers("hexa") == ""
    assert remove_fillers("justice") == "justice"  # whole words only


def test_only_fillers_is_chat() -> None:
    assert route("hey hexa please").is_chat


def test_more_than_the_max_words_is_chat_even_if_it_contains_a_phrase() -> None:
    words = " ".join(["walk forward"] + ["blah"] * config.ROUTER_MAX_WORDS)
    assert route(words).is_chat


# --- thresholds: the near-miss table, so tuning is visible -------------------------------------
NEAR_MISSES = [
    # (utterance, expected kind): scores are printed with -s
    ("walk forwards", "command"),
    ("walking forward", "command"),
    ("turn lef", "command"),
    ("go backwards", "command"),
    ("sat down", "command"),
    ("turn left 45", "command"),  # the number is ignored in v1
    ("waves", "command"),
    ("sand", "chat"),  # a single word must match exactly; ratio 89 against "stand" is ignored
    ("wav", "chat"),
    ("sitt", "chat"),
    ("stan", "chat"),
    ("walks", "chat"),
    ("I sat down", "chat"),  # the "sat down" alias is exact-only
    ("stand out", "chat"),
    ("forward", "chat"),
    ("walk fast", "chat"),
    ("sit up", "chat"),
    ("step back", "chat"),
    ("wave bye", "chat"),
    ("turn", "chat"),
    ("left", "chat"),
    ("turn up the music", "chat"),
]


def test_near_miss_table() -> None:
    print(f"\n{'utterance':<22}{'nearest phrase':<18}{'score':>6}  routed   (threshold "
          f"{config.ROUTER_THRESHOLD})")
    for text, expected in NEAR_MISSES:
        result = route(text)
        print(f"{text:<22}{str(result.phrase):<18}{result.score:>6.0f}  {result.kind}")
        assert result.kind == expected, (text, result)
        if expected == "command":
            assert result.score >= config.ROUTER_THRESHOLD
        elif len(result.normalized.split()) > 1:  # single words are exact-only; score is info
            assert result.score < config.ROUTER_THRESHOLD


@pytest.mark.parametrize("text", ["sand", "wav", "sitt", "sip", "stan", "wave1", "wak"])
def test_single_word_misspellings_never_fuzz_into_a_command(text: str) -> None:
    assert route(text).is_chat


@pytest.mark.parametrize(
    ("text", "action"), [("waves", "wave"), ("sat down", "sit"), ("sit", "sit")]
)
def test_explicit_single_word_aliases_route(text: str, action: str) -> None:
    assert command_of(text)[0] == action


def test_the_threshold_is_read_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert route("walk forwards").kind == "command"  # 96
    monkeypatch.setattr(config, "ROUTER_THRESHOLD", 99)
    assert route("walk forwards").is_chat
    assert route("walk forward").kind == "command"  # exact still matches


# --- speed -------------------------------------------------------------------------------------
@pytest.mark.timing
def test_router_speed() -> None:
    texts = ["walk forward", "I sat down for lunch", "please stand up", "stop", "turn up the music",
             "what is a wave function"]
    samples = []
    for _ in range(200):
        for text in texts:
            started = time.perf_counter()
            route(text)
            samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    print(f"router: median {median * 1e6:.0f} us, worst {max(samples) * 1e6:.0f} us "
          f"over {len(samples)} calls")
    assert median < 1e-3
