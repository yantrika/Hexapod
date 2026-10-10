"""Step 13 (P1): the persona prompt per mode, the straight-answer triggers and the mood."""

from __future__ import annotations

import pytest

import config
from brain.persona import Mood, Persona, PersonaError, is_greeting_by_name
from tests.fakes import FakeClock

PERSONA = Persona.from_file()


@pytest.mark.parametrize("mode", config.PERSONA_MODES)
def test_every_prompt_has_the_character_and_the_hard_limits(mode: str) -> None:
    prompt = PERSONA.system_prompt(mode)
    assert "Arya" in prompt and "English" in prompt
    for word in ("looks", "religion", "caste", "disability", "family", "health", "slurs",
                 "threats"):
        assert word in prompt, (mode, word)
    assert "safety, health or real help" in prompt


def test_each_mode_has_its_own_style_and_examples() -> None:
    prompts = {mode: PERSONA.system_prompt(mode) for mode in config.PERSONA_MODES}
    assert len(set(prompts.values())) == 3
    assert "Mode: calm" in prompts["calm"] and "No sarcasm" in prompts["calm"]
    assert "Mode: funny" in prompts["funny"] and "joke" in prompts["funny"]
    assert "Mode: roast" in prompts["roast"] and "seriously" in prompts["roast"]
    for mode in config.PERSONA_MODES:
        sheet = PERSONA.modes[mode]
        assert sheet.description and sheet.style and len(sheet.examples) >= 2
        assert "Examples:" in prompts[mode]


def test_the_prompt_stays_short_for_a_small_model() -> None:
    for mode in config.PERSONA_MODES:
        assert len(PERSONA.system_prompt(mode)) < 2200


def test_an_unknown_mode_falls_back_to_calm() -> None:
    assert PERSONA.system_prompt("shouting") == PERSONA.system_prompt("calm")


def test_straight_mode_drops_the_mode_style_even_in_roast() -> None:
    prompt = PERSONA.system_prompt("roast", straight=True)
    assert "plain, honest, kind answer" in prompt
    assert "sarcastic" not in prompt.lower() and "Examples:" not in prompt
    assert "Hard rules" in prompt  # the limits stay


def test_seriously_and_help_questions_are_recognised() -> None:
    assert PERSONA.wants_straight_answer("Seriously, what is a hexapod?")
    assert PERSONA.wants_straight_answer("just tell me")
    assert not PERSONA.wants_straight_answer("what is a hexapod")
    assert PERSONA.is_help_question("I need help, I am bleeding")
    assert PERSONA.is_help_question("is it safe to touch this?")
    assert not PERSONA.is_help_question("tell me a joke")


@pytest.mark.parametrize("mood", config.MOOD_STATES)
def test_the_mood_changes_exactly_one_line(mood: str) -> None:
    neutral = PERSONA.system_prompt("calm", "neutral").splitlines()
    moody = PERSONA.system_prompt("calm", mood).splitlines()
    assert len(neutral) == len(moody)
    differing = [i for i, (a, b) in enumerate(zip(neutral, moody, strict=True)) if a != b]
    assert len(differing) == (0 if mood == "neutral" else 1)


def test_a_sheet_without_a_mode_is_refused() -> None:
    data = {"name": "A", "character": "c", "hard_limits": "h", "straight_triggers": [],
            "help_triggers": [], "mood": {m: "x" for m in config.MOOD_STATES},
            "modes": {"calm": {"description": "d", "style": "s"}}}
    with pytest.raises(PersonaError, match="funny"):
        Persona(data)


# -- the mood ------------------------------------------------------------------------------------
def test_the_mood_starts_neutral_and_gets_bored_when_nobody_speaks() -> None:
    clock = FakeClock()
    mood = Mood(clock.now)
    assert mood.state == "neutral"
    clock.advance(config.MOOD_BORED_AFTER_S - 1)
    assert mood.state == "neutral"
    clock.advance(2)
    assert mood.state == "bored"
    mood.heard()
    assert mood.state == "neutral"


def test_a_greeting_by_name_makes_her_happy_for_a_while() -> None:
    clock = FakeClock()
    mood = Mood(clock.now)
    mood.greeted()
    assert mood.state == "happy"
    clock.advance(config.MOOD_HAPPY_S + 1)
    assert mood.state == "neutral"


def test_many_commands_make_her_busy_then_it_passes() -> None:
    clock = FakeClock()
    mood = Mood(clock.now)
    for _ in range(config.MOOD_BUSY_COMMANDS - 1):
        mood.command()
        clock.advance(1)
    assert mood.state == "neutral"
    mood.command()
    assert mood.state == "busy"
    clock.advance(config.MOOD_BUSY_WINDOW_S + 1)
    assert mood.state == "neutral"


def test_busy_beats_happy() -> None:
    clock = FakeClock()
    mood = Mood(clock.now)
    mood.greeted()
    for _ in range(config.MOOD_BUSY_COMMANDS):
        mood.command()
    assert mood.state == "busy"


@pytest.mark.parametrize("text,expected", [
    ("hello Arya", True), ("Hey arya!", True), ("good morning Arya", True),
    ("hello", False), ("Arya what is a hexapod", False), ("hi there robot", False),
])
def test_greeting_by_name(text: str, expected: bool) -> None:
    assert is_greeting_by_name(text) is expected
