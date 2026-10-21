"""The router: command phrases, stop words and fuzzy matching (a pure function: no PyBullet, no
processes, no clock), and the persona routes ("my name is X", "forget me", modes) with the
regression that every existing command routes as before."""

from __future__ import annotations

import statistics
import time

import pytest

import config
from brain.router import is_no, is_yes, normalize, remove_fillers, route


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
    """Documented tradeoff (a stop word anywhere stops): a stop word in ANY sentence stops the
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


# --- router persona ------------------------------------------------------------

def persona(text: str) -> tuple[str | None, dict[str, object]]:
    result = route(text)
    assert result.kind == "persona", (text, result)
    return result.action, result.params


@pytest.mark.parametrize("text,name", [
    ("my name is siddharth", "Siddharth"),
    ("My name is Priya.", "Priya"),
    ("Arya, my name is Anna", "Anna"),
    ("call me Sid", "Sid"),
    ("you can call me Mary Ann", "Mary Ann"),
    ("please call me bob", "Bob"),
    ("I am Rahul", "Rahul"),
    ("I'm Anna", "Anna"),
    ("hey arya i am dev", "Dev"),
    ("my name's Tom", "Tom"),
])
def test_naming_yourself_sets_the_speaker(text: str, name: str) -> None:
    assert persona(text) == ("set_speaker", {"name": name})


@pytest.mark.parametrize("text", [
    "I am hungry", "I'm tired", "I am fine", "I am not sure", "I am walking", "I am sorry",
    "I am going home", "i am back", "I'm here", "I am ready", "I am so happy", "I am an engineer",
    "call me later", "call me back", "call me tomorrow", "my name is not important",
    "my name is a secret", "what is my name", "I am listening", "I am the boss",
    "call me maybe", "my name is", "I am",
])
def test_everyday_sentences_with_i_am_are_not_names(text: str) -> None:
    assert route(text).kind != "persona", text


@pytest.mark.parametrize("text", ["forget me", "Forget me.", "Arya forget me", "please forget me",
                                  "forget about me"])
def test_forget_me(text: str) -> None:
    assert persona(text) == ("forget_me", {})


@pytest.mark.parametrize("text", [
    "what do you remember about me", "What do you remember about me?",
    "Arya, what do you know about me", "do you remember me", "what do you remember",
])
def test_recall(text: str) -> None:
    assert persona(text) == ("recall_me", {})


@pytest.mark.parametrize("text,mode", [
    ("calm mode", "calm"), ("funny mode", "funny"), ("roast mode", "roast"),
    ("Arya, roast mode", "roast"), ("Arya, calm mode please", "calm"),
    ("switch to funny mode", "funny"), ("go into roast mode", "roast"), ("roast me", "roast"),
    ("Hey Arya funny mode on", "funny"),
])
def test_mode_switches(text: str, mode: str) -> None:
    assert persona(text) == ("set_mode", {"mode": mode})


@pytest.mark.parametrize("text", ["stop roasting", "Arya, stop roasting", "stop the roast",
                                  "no more roasting", "stop roasting me"])
def test_stop_roasting_goes_back_to_calm(text: str) -> None:
    assert persona(text) == ("set_mode", {"mode": "calm"})


@pytest.mark.parametrize("text", [
    "stop", "stop now", "halt", "freeze", "Arya stop", "stop roasting and stop walking",
    "stop talking about roast", "I can't stop laughing", "stop the robot",
])
def test_every_other_stop_still_stops(text: str) -> None:
    result = route(text)
    assert result.kind == "stop", (text, result)


@pytest.mark.parametrize("text", [
    "what is a roast", "I like roast chicken", "the weather is calm today", "that is funny",
    "tell me something funny", "I will forget my keys", "do you remember the song",
    "what do you remember from yesterday", "the mode of transport",
])
def test_ordinary_chat_that_mentions_the_words_is_still_chat(text: str) -> None:
    assert route(text).kind == "chat", text


# -- the regression: the commands as they were before the persona routes -----------------------
@pytest.mark.parametrize("phrase,expected", sorted(config.ROUTER_PHRASES.items()))
def test_every_command_phrase_routes_as_before(phrase: str, expected: tuple[str, dict]) -> None:
    for text in (phrase, f"please {phrase}", f"hexa {phrase}", f"Arya, {phrase}"):
        result = route(text)
        assert result.kind == "command" and result.action == expected[0], text
        assert result.params == dict(expected[1]), text


@pytest.mark.parametrize("alias,expected", sorted(config.ROUTER_ALIASES.items()))
def test_every_alias_routes_as_before(alias: str, expected: tuple[str, dict]) -> None:
    result = route(alias)
    assert result.kind == "command" and result.action == expected[0]


@pytest.mark.parametrize("word", config.STOP_WORDS)
def test_every_stop_word_stops_anywhere(word: str) -> None:
    for text in (word, f"please {word}", f"Arya {word}", f"I said {word} right now"):
        assert route(text).kind == "stop", text


@pytest.mark.parametrize("text", [
    "I sat down for lunch", "I'll walk you through it", "turn up the music",
    "what is a good way to stand out", "can you tell me about waves", "what is the weather today",
    "tell me a joke", "who are you",
])
def test_the_old_negative_cases_still_go_to_chat(text: str) -> None:
    assert route(text).kind == "chat", text


def test_a_stop_word_is_never_a_name() -> None:
    assert route("my name is stop").kind == "stop"  # the stop check comes first
    assert route("I am walk").kind != "persona"  # command words are not names


@pytest.mark.parametrize("text,action", [
    ("aria sit down", "sit"), ("aria stand up", "stand"), ("hey aria walk forward", "walk"),
    ("aria stop", "stop"), ("aria roast mode", "set_mode"), ("aria come mode", "set_mode"),
    ("come mode", "set_mode"),
])
def test_how_vosk_hears_arya_still_routes(text: str, action: str) -> None:
    result = route(text)
    assert result.action == action, (text, result)
    if text.endswith("come mode"):
        assert result.params == {"mode": "calm"}


def test_a_person_called_aria_keeps_the_name() -> None:
    assert persona("my name is aria") == ("set_speaker", {"name": "Aria"})
    assert route("aria").kind == "chat"  # alone it is not dropped: nothing would be left
    assert route("area sit down").kind == "chat"


def test_arya_is_a_filler_and_in_the_grammar() -> None:
    from voice.stt import command_grammar

    assert "arya" in config.ROUTER_FILLERS and "arya" in command_grammar()
    assert route("arya sit down").action == "sit"


@pytest.mark.parametrize("text,yes,no", [
    ("yes", True, False), ("Yes.", True, False), ("yeah", True, False), ("yes please", True, False),
    ("yes forget me", True, False), ("confirm", True, False), ("no", False, True),
    ("never mind", False, True), ("cancel", False, True), ("yes I did that", False, False),
    ("maybe", False, False), ("what", False, False),
])
def test_yes_and_no(text: str, yes: bool, no: bool) -> None:
    assert is_yes(text) is yes and is_no(text) is no


def test_face_and_tracking_requests_are_persona_routes() -> None:
    for text, action, params in (
            ("remember my face", "remember_face", {}), ("Arya, learn my face", "remember_face", {}),
            ("forget my face", "forget_face", {}), ("track me", "tracking", {"on": True}),
            ("look at me", "tracking", {"on": True}),
            ("don't track me", "tracking", {"on": False})):
        result = route(text)
        assert (result.kind, result.action, result.params) == ("persona", action, params), text


def test_face_phrases_never_hide_a_stop_or_a_command() -> None:
    assert route("stop tracking me").kind == "stop"
    assert route("stop remembering my face").kind == "stop"
    assert route("walk forward").kind == "command"
    assert route("remember my face and walk forward").kind == "chat"
