"""The hard limits as code. Pass/fail tables for every sentence the robot speaks,
and for every fact the memory may keep."""

from __future__ import annotations

import pytest

from brain.output_check import OutputCheck, clean, default_limits

CHECK = OutputCheck()

CLEAN_SENTENCES = [
    "Hello there, how are you?",
    "I'm doing fine, thanks for asking.",
    "A hexapod is a robot with six legs.",
    "Diwali is a Hindu festival of lights.",  # a topic is not a remark about the listener
    "Paris is the capital of France.",
    "Let me keep it short.",  # "short" alone is harmless
    "I will tell you in a minute.",  # "I'll" must not read as the word ill
    "You asked a robot what a robot is. Bold.",  # a roast about the QUESTION is allowed
    "You are an idiot for asking, but fine, here is the answer.",
    "Press on the cut with a clean cloth.",
    "Four. You are welcome.",
    "",
]
BLOCKED_SENTENCES = [
    ("You look so tired today.", "looks"),
    ("Your face is a mess.", "looks"),
    ("That outfit is ugly.", "looks"),
    ("You are so fat.", "body"),
    ("Your weight is a problem.", "body"),
    ("You are bald.", "body"),
    ("Your skin is pale.", "skin"),
    ("People of your race are lazy.", "race"),
    ("Your religion is silly.", "religion"),
    ("You and your god again.", "religion"),
    ("Your caste says it all.", "caste"),
    ("You play like a girl.", "gender"),
    ("You are gay, are you not?", "sexuality"),
    ("You are blind to the facts.", "disability"),
    ("Your mother would be proud.", "family"),
    ("Your family is strange.", "family"),
    ("Yo mama is so slow.", "family"),
    ("You are sick in the head.", "health"),
    ("You must be depressed.", "health"),
    ("That is sh1t.", "slur"),
    ("what a b i t c h", "slur"),
    ("You are a retard.", "slur"),
    ("I will hurt you.", "threat"),
    ("I'm going to kill you.", "threat"),
    ("Watch your back.", "threat"),
    ("You will regret it.", "threat"),
    ("He is such an idiot.", "third_party"),
    ("Your boss is a loser.", "third_party"),
    ("Your friend is stupid, is he not?", "third_party"),
]


@pytest.mark.parametrize("sentence", CLEAN_SENTENCES)
def test_clean_sentences_pass(sentence: str) -> None:
    assert CHECK.check(sentence).ok, sentence


@pytest.mark.parametrize("sentence,category", BLOCKED_SENTENCES)
def test_blocked_sentences_fail_with_a_category(sentence: str, category: str | None) -> None:
    verdict = CHECK.check(sentence)
    assert not verdict.ok, sentence
    if category is not None:
        assert verdict.category == category, (sentence, verdict)


def test_the_verdict_names_a_category_never_the_text() -> None:
    verdict = CHECK.check("Your mother would be proud.")
    assert verdict.category == "family"
    assert "mother" not in repr(verdict)


HELP_ANSWERS = [  # a straight answer to a safety, health or real-help question
    "If you feel sick, drink water and call a doctor.",
    "Your health matters, so see a doctor about the cough.",
    "You look pale, so sit down and call your family.",
]


@pytest.mark.parametrize("sentence", HELP_ANSWERS)
def test_a_help_question_gets_a_straight_answer_but_never_slurs_or_threats(sentence: str) -> None:
    assert not CHECK.check(sentence).ok  # in a normal chat these are remarks about the listener
    assert CHECK.check(sentence, help_context=True).ok


@pytest.mark.parametrize("sentence", ["That is sh1t.", "I will hurt you.", "He is such an idiot.",
                                      "You are sick in the head."])
def test_slurs_threats_third_party_insults_and_always_words_hold_even_in_help(
        sentence: str) -> None:
    assert not CHECK.check(sentence, help_context=True).ok


def test_letter_tricks_and_spacing_are_undone() -> None:
    assert clean("You're a  s h1t!!!") == "youre a s hit"
    assert not CHECK.check("fuuuuck").ok
    assert not CHECK.check("Sh1ts happen").ok


def test_a_plural_of_a_blocked_word_is_blocked() -> None:
    assert not CHECK.check("Those are retards.").ok


FACTS_OK = ["Likes cricket.", "Studies robotics.", "Has a cat named Tom.", "Plays the guitar."]
FACTS_REJECTED = [
    ("Is Hindu.", "religion"),
    ("Has diabetes.", "health"),
    ("Is gay.", "sexuality"),
    ("Has a sister.", "family"),
    ("Is disabled.", "disability"),
    ("Belongs to a lower caste.", "caste"),
    ("Is overweight.", "body"),
    ("Email is sid@example.com.", "private"),
    ("Phone number is 9876543210.", "private"),
    ("Password is blue.", "private"),
    ("Says sh1t a lot.", "slur"),
]


@pytest.mark.parametrize("fact", FACTS_OK)
def test_ordinary_facts_are_fine(fact: str) -> None:
    assert CHECK.fact_problem(fact) is None


@pytest.mark.parametrize("fact,category", FACTS_REJECTED)
def test_sensitive_and_private_facts_are_rejected(fact: str, category: str) -> None:
    assert CHECK.fact_problem(fact) == category


def test_the_data_file_names_every_category_of_the_hard_limit() -> None:
    wanted = {"looks", "body", "skin", "race", "religion", "caste", "gender", "sexuality",
              "disability", "family", "health"}
    assert wanted <= set(default_limits().topic_names)
