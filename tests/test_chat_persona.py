"""The chat responder with the persona hooks: the output check before the TTS, the
neutral line, the history, the context builder, the turn callback. FakeChat, a stub playback."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest

from brain.arya import Arya
from brain.chat import ChatResponder, FakeChat
from brain.context import ContextBuilder
from brain.memory import Memory
from brain.persona import Persona
from tests.fakes import StubPlayback, wait_until


class Silent:
    def say(self, text: str) -> int:
        return 0

    def say_phrase(self, name: str) -> int:
        return 0


class Rig:
    def __init__(self, reply: str) -> None:
        self.memory = Memory(":memory:")
        self.arya = Arya(Persona.from_file(), self.memory, Silent())
        self.backend = FakeChat((reply,))
        self.playback = StubPlayback()
        self.turns: list[tuple[str, str]] = []
        self.responder = ChatResponder(
            self.backend, self.playback,
            context_builder=ContextBuilder(self.arya.context_parts).build,
            sentence_check=self.arya.sentence_check,
            on_turn=lambda user, reply: self.turns.append((user, reply)))

    def ask(self, text: str) -> None:
        self.responder.submit(text)
        wait_until(lambda: not self.responder.active, 5.0, "the reply thread to end")

    def close(self) -> None:
        self.responder.shutdown()
        self.memory.close()


@pytest.fixture
def make() -> Iterator[Callable[[str], Rig]]:
    made: list[Rig] = []

    def build(reply: str) -> Rig:
        made.append(Rig(reply))
        return made[-1]

    yield build
    for rig in made:
        rig.close()


def test_a_clean_reply_is_spoken_unchanged_and_remembered(make: Callable[[str], Rig]) -> None:
    rig = make("A hexapod has six legs. That is me.")
    rig.ask("what is a hexapod")
    assert rig.playback.sentences == ["A hexapod has six legs.", "That is me."]
    assert rig.playback.phrases == []
    assert rig.responder.history == [("what is a hexapod", "A hexapod has six legs. That is me.")]
    assert rig.turns == rig.responder.history
    assert rig.responder.blocked_sentences == 0


def test_a_bad_sentence_is_dropped_and_one_neutral_line_is_spoken(
        make: Callable[[str], Rig]) -> None:
    rig = make("Sure thing. You look so tired today. Your mother would agree. Anyway, bye.")
    rig.ask("how are you")
    assert rig.playback.sentences == ["Sure thing.", "Anyway, bye."]
    assert rig.playback.phrases == ["persona_blocked"]  # ONE line for two blocked sentences
    assert rig.responder.blocked_sentences == 2


def test_the_blocked_text_is_not_kept_in_the_history_or_given_to_the_extractor(
        make: Callable[[str], Rig]) -> None:
    rig = make("Sure thing. You look so tired today. Anyway, bye.")
    rig.ask("how are you")
    assert rig.responder.history == [("how are you", "Sure thing. Anyway, bye.")]
    assert rig.turns == rig.responder.history
    assert "tired" not in " ".join(r for _, r in rig.turns)


def test_a_reply_that_is_all_blocked_is_one_neutral_line_and_no_failure_phrase(
        make: Callable[[str], Rig]) -> None:
    rig = make("That is sh1t. You are a retard.")
    rig.ask("hello")
    assert rig.playback.sentences == [] and rig.playback.phrases == ["persona_blocked"]
    assert rig.responder.history == []  # nothing worth remembering
    assert "cant_think" not in rig.playback.phrases


def test_the_log_names_the_category_and_never_the_words(
        make: Callable[[str], Rig], caplog: pytest.LogCaptureFixture) -> None:
    rig = make("Your mother would be proud of you, friend.")
    with caplog.at_level(logging.INFO):
        rig.ask("secret user words about lunch")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "persona check (family)" in text
    assert "mother" not in text and "secret user words" not in text


def test_a_help_question_gets_its_straight_answer_through(make: Callable[[str], Rig]) -> None:
    rig = make("If you feel sick, drink water and call a doctor.")
    rig.ask("I feel sick, what should I do")
    assert rig.playback.sentences == ["If you feel sick, drink water and call a doctor."]
    assert rig.playback.phrases == []
    other = make("If you feel sick, drink water and call a doctor.")
    other.ask("tell me a joke")  # the same words as a remark in a joke: blocked
    assert other.playback.sentences == [] and other.playback.phrases == ["persona_blocked"]


def test_the_model_gets_the_persona_the_mode_and_the_speakers_facts(
        make: Callable[[str], Rig]) -> None:
    rig = make("Fine.")
    rig.arya.set_speaker("anna", announce=False)
    person = rig.memory.get_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    rig.arya.set_mode("roast", announce=False)
    rig.ask("hello there")
    messages = rig.backend.last_messages
    assert messages[0]["role"] == "system" and messages[-1] == {
        "role": "user", "content": "hello there"}
    system = messages[0]["content"]
    assert "Mode: roast" in system and "talking to Anna" in system and "- Likes tea." in system
    assert "Hard rules" in system
    rig.ask("and again")
    again = rig.backend.last_messages
    assert [m["role"] for m in again] == ["system", "user", "assistant", "user"]  # history joined


def test_a_broken_check_fails_closed(make: Callable[[str], Rig]) -> None:
    rig = make("Perfectly fine words.")

    def broken(sentence: str, user_text: str) -> str | None:
        raise RuntimeError("boom")

    rig.responder.sentence_check = broken
    rig.ask("hi")
    assert rig.playback.sentences == [] and rig.playback.phrases == ["persona_blocked"]


def test_without_the_hooks_the_responder_is_the_old_one() -> None:
    backend, playback = FakeChat(("Your mother is fine. Bye.",)), StubPlayback()
    responder = ChatResponder(backend, playback)
    responder.submit("hi")
    wait_until(lambda: not responder.active, 5.0, "the reply")
    assert playback.sentences == ["Your mother is fine.", "Bye."]
    assert backend.last_messages[0]["role"] == "system"
    assert "Arya" in backend.last_messages[0]["content"]
    responder.shutdown()
