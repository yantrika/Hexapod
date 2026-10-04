"""Step 9: the streamed-text sentence splitter (pure, no I/O)."""

from __future__ import annotations

import random

import pytest

from brain.sentences import SentenceSplitter, clean_text, split_all


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello there. How are you?", ["Hello there.", "How are you?"]),
        ("I am Hexa! I walk. I wave.", ["I am Hexa!", "I walk.", "I wave."]),
        ("Hello", ["Hello"]),  # the remainder is flushed at the end
        ("It costs 3.5 dollars. Cheap!", ["It costs 3.5 dollars.", "Cheap!"]),  # decimals
        ("Ask Dr. Smith about it. Then go.", ["Ask Dr. Smith about it.", "Then go."]),
        ("Mr. and Mrs. Lee came. Nice.", ["Mr. and Mrs. Lee came.", "Nice."]),
        ("Use e.g. a cup. Or a mug.", ["Use e.g. a cup.", "Or a mug."]),
        ("I met J. Smith today. Fun.", ["I met J. Smith today.", "Fun."]),
        ("Wait... what? Really!", ["Wait...", "what?", "Really!"]),  # an ellipsis ends a sentence
        ('He said "go." Then left.', ['He said "go."', "Then left."]),
        ("Line one.\nLine two.\n\nLine three.", ["Line one.", "Line two.", "Line three."]),
        ("No terminator at all", ["No terminator at all"]),
        ("", []),
        ("   ", []),
        ("... !!!", []),  # nothing speakable
    ],
)
def test_table(text: str, expected: list[str]) -> None:
    assert split_all(text) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**Hello** there", "Hello there"),
        ("This is `code` text", "This is code text"),
        ("# A heading", "A heading"),
        ("- a bullet", "a bullet"),
        ("* another bullet", "another bullet"),
        ("see [the docs](http://x.y/z) now", "see the docs now"),
        ("I am happy \U0001F600 today ❤️", "I am happy today"),
        ("snake_case stays, _emphasis_ goes", "snake_case stays, emphasis goes"),
        ("> quoted", "quoted"),
        ("many   spaces\tand\nnewlines", "many spaces and newlines"),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_markdown_and_emoji_never_reach_the_speaker() -> None:
    text = "**Sure!** I can \U0001F916 wave.\n- first\n- second\n\n_Done_ now."
    for sentence in split_all(text):
        assert not any(c in sentence for c in "*_`#\U0001F916\n")


LONG_TEXT = (
    "Hello! I am Hexa, a small friendly robot. I walk, sit, stand and wave. "
    "Dr. Smith built me for 3.5 months, e.g. in spring. Why do you ask? "
    "Well... it is a long story, and it has many parts; the first part is short, "
    "but the second part keeps going on and on without any stop at all because I like to talk "
    "and talk and talk until somebody tells me to be quiet and listen to them for once"
)


def test_the_same_sentences_for_every_chunking() -> None:
    expected = split_all(LONG_TEXT)
    assert len(expected) >= 6
    rng = random.Random(7)
    for _ in range(200):
        splitter = SentenceSplitter()
        out: list[str] = []
        position = 0
        while position < len(LONG_TEXT):
            size = rng.choice([1, 1, 2, 3, 5, 8, 13, 40])
            out += splitter.feed(LONG_TEXT[position:position + size])
            position += size
        out += splitter.flush()
        assert out == expected


def test_one_character_at_a_time_matches_all_at_once() -> None:
    splitter = SentenceSplitter()
    out: list[str] = []
    for char in LONG_TEXT:
        out += splitter.feed(char)
    out += splitter.flush()
    assert out == split_all(LONG_TEXT)


def test_a_sentence_is_released_only_after_the_character_following_its_punctuation() -> None:
    splitter = SentenceSplitter()
    assert splitter.feed("Hello there.") == []  # "3.5" might still follow: wait for one more char
    assert splitter.feed(" ") == ["Hello there."]
    assert splitter.feed("Next one") == []
    assert splitter.flush() == ["Next one"]


def test_the_first_sentence_comes_out_before_the_stream_ends() -> None:
    splitter = SentenceSplitter()
    first = splitter.feed("I am Hexa. I like to ")
    assert first == ["I am Hexa."]


def test_flush_resets_the_splitter() -> None:
    splitter = SentenceSplitter()
    splitter.feed("One two")
    assert splitter.flush() == ["One two"]
    assert splitter.flush() == []


# --- forced splitting of overlong sentences ------------------------------------------------------
def test_an_overlong_sentence_is_split_at_a_clause_boundary() -> None:
    text = ("I like walking in the park, and I like sitting on the grass, but I do not like "
            "the rain because it makes my legs wet and slippery")
    parts = split_all(text, max_chars=60)
    assert len(parts) >= 2 and all(len(p) <= 62 for p in parts)
    assert parts[0].endswith(",") or parts[0].endswith("park")  # cut at a clause, not mid-word
    assert " ".join(parts).replace(",", "") == text.replace(",", "")  # nothing lost


def test_a_long_run_without_clauses_is_split_at_a_space() -> None:
    text = "word " * 40
    parts = split_all(text.strip(), max_chars=50)
    assert all(len(p) <= 50 for p in parts) and all(not p.startswith(" ") for p in parts)
    assert " ".join(parts) == text.strip()


def test_a_word_longer_than_the_limit_is_cut_hard() -> None:
    parts = split_all("x" * 130, max_chars=50)
    assert [len(p) for p in parts] == [50, 50, 30]


def test_forced_splits_do_not_depend_on_the_chunking() -> None:
    text = ("alpha beta gamma, delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi "
            * 3)
    expected = split_all(text, max_chars=45)
    rng = random.Random(3)
    for _ in range(100):
        splitter = SentenceSplitter(45)
        out, position = [], 0
        while position < len(text):
            size = rng.choice([1, 2, 7, 20, 90])
            out += splitter.feed(text[position:position + size])
            position += size
        out += splitter.flush()
        assert out == expected
