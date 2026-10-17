"""The persona routes ("my name is X", "forget me", modes) and the regression that
every existing command phrase still routes exactly as before."""

from __future__ import annotations

import pytest

import config
from brain.router import is_no, is_yes, route


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
