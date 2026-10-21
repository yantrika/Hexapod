"""Arya's state, speaker, forget flow, recall, mood events and chat hooks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from brain.arya import Arya, PersonaState
from brain.memory import Memory
from brain.persona import Persona
from brain.router import route
from tests.fakes import FakeClock


class FakeSpeech:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.phrases: list[str] = []

    def say(self, text: str) -> int:
        self.said.append(text)
        return 1

    def say_phrase(self, name: str) -> int:
        self.phrases.append(name)
        return 1


class Rig:
    def __init__(self, path: Path, with_memory: bool = True) -> None:
        self.clock = FakeClock()
        self.memory: Memory | None = Memory(path) if with_memory else None
        self.speech = FakeSpeech()
        self.states: list[PersonaState] = []
        self.arya = Arya(Persona.from_file(), self.memory, self.speech, clock=self.clock.now,
                         on_change=self.states.append)

    def hear(self, text: str) -> None:
        """What the voice loop does with a final result (without a body)."""
        result = route(text)
        if result.kind != "stop" and self.arya.consume(text):
            return
        self.arya.note(result)
        if result.kind == "persona":
            self.arya.handle(result)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path / "arya.db")
    yield made
    if made.memory is not None:
        made.memory.close()


def test_she_starts_calm_with_a_guest(rig: Rig) -> None:
    assert rig.arya.mode == "calm" and rig.arya.speaker == config.GUEST_NAME
    state = rig.arya.state()
    assert state.mood == "neutral" and state.people == () and state.memory


def test_mode_switches_announce_and_persist(rig: Rig, tmp_path: Path) -> None:
    rig.hear("Arya, roast mode")
    assert rig.arya.mode == "roast" and rig.speech.phrases == ["mode_roast"]
    rig.hear("stop roasting")
    assert rig.arya.mode == "calm" and rig.speech.phrases[-1] == "mode_calm"
    rig.hear("funny mode")
    again = Rig(tmp_path / "arya.db")  # a restart remembers the mode
    assert again.arya.mode == "funny"
    assert not again.arya.set_mode("shouting")
    assert again.arya.mode == "funny"


def test_every_mode_phrase_exists_as_a_prerendered_phrase() -> None:
    for mode in config.PERSONA_MODES:
        assert f"mode_{mode}" in config.TTS_PHRASES
    for name in ("persona_blocked", "say_yes_to_confirm", "forget_done", "forget_cancelled",
                 "remember_nothing", "memory_off"):
        assert name in config.TTS_PHRASES


def test_naming_yourself_creates_the_person_and_greets(rig: Rig) -> None:
    rig.hear("my name is siddharth")
    assert rig.arya.speaker == "Siddharth"
    assert rig.speech.said == ["Nice to meet you, Siddharth."]
    assert rig.memory is not None and rig.memory.get_person("Siddharth") is not None
    rig.hear("call me anna")
    assert rig.arya.speaker == "Anna"
    rig.hear("I am siddharth")
    assert rig.speech.said[-1] == "Welcome back, Siddharth."  # known now
    assert len(rig.memory.list_people()) == 2


def test_an_unusable_name_is_refused_politely(rig: Rig) -> None:
    assert not rig.arya.set_speaker("sh1t")
    assert rig.arya.speaker == config.GUEST_NAME
    assert rig.speech.said == ["I can't use that name."]
    assert rig.memory is not None and rig.memory.list_people() == []


def test_the_web_page_can_only_pick_someone_who_exists(rig: Rig) -> None:
    assert not rig.arya.set_speaker("Nobody", announce=False, create=False)
    rig.hear("my name is anna")
    rig.arya.set_speaker("guest")
    assert rig.arya.speaker == config.GUEST_NAME
    assert rig.arya.set_speaker("Anna", announce=False, create=False)
    assert rig.arya.speaker == "Anna"


def test_recall_reads_back_the_facts_and_a_stranger_gets_nothing(rig: Rig) -> None:
    rig.hear("what do you remember about me")
    assert rig.speech.phrases == ["remember_nothing"]  # a guest
    rig.hear("my name is anna")
    rig.hear("what do you remember about me")
    assert rig.speech.phrases[-1] == "remember_nothing"  # nothing yet
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    rig.memory.add_fact(person.id, "Studies maths.")
    rig.hear("what do you remember about me")
    assert rig.speech.said[-1] == "Here is what I remember, Anna. Likes tea. Studies maths."


def test_forget_me_asks_first_then_deletes_everything(rig: Rig) -> None:
    rig.hear("my name is anna")
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    rig.memory.add_summary(person.id, "Talked about tea.")
    rig.memory.set_face_consent(person.id, True)
    assert rig.memory.add_embedding(person.id, b"1234")
    rig.hear("forget me")
    assert rig.speech.phrases[-1] == "say_yes_to_confirm"
    assert rig.memory.get_person("anna") is not None  # nothing is deleted before the yes
    rig.hear("yes")
    assert rig.speech.phrases[-1] == "forget_done"
    assert rig.memory.get_person("anna") is None
    assert rig.memory.facts(person.id) == [] and rig.memory.embedding_count(person.id) == 0
    assert rig.arya.speaker == config.GUEST_NAME


def test_anything_but_a_yes_cancels_forget_me(rig: Rig) -> None:
    rig.hear("my name is anna")
    rig.hear("forget me")
    rig.hear("what is a hexapod")  # chat: the question is dropped
    rig.hear("yes")  # a late yes is just a word now
    assert rig.memory is not None and rig.memory.get_person("anna") is not None
    assert "forget_done" not in rig.speech.phrases


def test_no_cancels_out_loud(rig: Rig) -> None:
    rig.hear("my name is anna")
    rig.hear("forget me")
    rig.hear("no")
    assert rig.speech.phrases[-1] == "forget_cancelled"
    assert rig.memory is not None and rig.memory.get_person("anna") is not None


def test_a_yes_that_comes_too_late_does_not_delete(rig: Rig) -> None:
    rig.hear("my name is anna")
    rig.hear("forget me")
    rig.clock.advance(config.FORGET_CONFIRM_S + 1)
    rig.hear("yes")
    assert rig.memory is not None and rig.memory.get_person("anna") is not None


def test_a_stop_cancels_a_pending_forget(rig: Rig) -> None:
    rig.hear("my name is anna")
    rig.hear("forget me")
    rig.arya.cancel_pending()
    rig.hear("yes")
    assert rig.memory is not None and rig.memory.get_person("anna") is not None


def test_forgetting_a_guest_forgets_nobody(rig: Rig) -> None:
    rig.hear("forget me")
    assert rig.speech.phrases == ["remember_nothing"]


def test_the_web_forget_removes_the_person_and_tells_the_extractor(rig: Rig) -> None:
    rig.hear("my name is anna")
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    told: list[str] = []
    rig.arya.on_forgotten = told.append
    assert rig.arya.forget_person(person.id)
    assert told == ["Anna"] and rig.arya.speaker == config.GUEST_NAME
    assert not rig.arya.forget_person(person.id)
    assert rig.states[-1].people == ()


def test_changes_are_published_for_the_page(rig: Rig) -> None:
    rig.hear("roast mode")
    rig.hear("my name is anna")
    assert rig.states[-1].mode == "roast" and rig.states[-1].speaker == "Anna"
    event = rig.states[-1].as_event()
    assert event["type"] == "persona" and event["modes"] == list(config.PERSONA_MODES)
    assert event["people"] == [{"id": 1, "name": "Anna", "facts": 0, "roast_ok": True}]


def test_mood_follows_what_she_hears(rig: Rig) -> None:
    rig.hear("hello Arya")
    assert rig.arya.state().mood == "happy"
    rig.clock.advance(config.MOOD_HAPPY_S + config.MOOD_BORED_AFTER_S + 1)
    assert rig.arya.state().mood == "bored"
    for _ in range(config.MOOD_BUSY_COMMANDS):
        rig.hear("sit down")
    assert rig.arya.state().mood == "busy"


def test_the_context_has_the_mode_the_speaker_and_their_facts(rig: Rig) -> None:
    rig.hear("roast mode")
    rig.hear("my name is anna")
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    parts = rig.arya.context_parts("what is a hexapod")
    assert "Mode: roast" in parts.system and parts.speaker == "Anna"
    assert parts.facts == ["Likes tea."]
    straight = rig.arya.context_parts("seriously, what is a hexapod")
    assert "plain, honest, kind answer" in straight.system
    helping = rig.arya.context_parts("I need help")
    assert "plain, honest, kind answer" in helping.system


def test_roast_mode_applies_to_everyone_whatever_their_roast_ok(rig: Rig) -> None:
    rig.hear("roast mode")
    rig.hear("my name is anna")
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    assert rig.arya.set_roast_ok(person.id, False)
    assert not rig.memory.get_person("anna").roast_ok  # type: ignore[union-attr]
    assert "Mode: roast" in rig.arya.context_parts("hi").system  # roast_ok gates only auto-roast
    assert rig.arya.mode == "roast"


def test_set_roast_ok_is_published_and_refuses_strangers(rig: Rig) -> None:
    rig.hear("my name is anna")
    assert rig.memory is not None
    person = rig.memory.get_person("anna")
    assert person
    assert rig.arya.set_roast_ok(person.id, False)
    assert rig.states[-1].as_event()["people"][0]["roast_ok"] is False
    assert rig.arya.set_roast_ok(person.id, True)
    assert rig.states[-1].as_event()["people"][0]["roast_ok"] is True
    assert not rig.arya.set_roast_ok(999, False)


def test_the_sentence_check_knows_help_questions(rig: Rig) -> None:
    assert rig.arya.sentence_check("You look so tired.", "tell me a joke") == "looks"
    assert rig.arya.sentence_check("You look pale, sit down.", "I feel sick, help me") is None
    assert rig.arya.sentence_check("That is sh1t.", "I feel sick, help me") == "slur"
    assert rig.arya.sentence_check("Hello.", "hi") is None


def test_the_check_can_be_switched_off_in_config(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PERSONA_OUTPUT_CHECK", False)
    assert rig.arya.sentence_check("You look so tired.", "hi") is None


def test_without_a_database_she_still_talks_and_says_so(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "x.db", with_memory=False)
    rig.hear("my name is anna")
    assert rig.arya.speaker == "Anna" and "can't remember" in rig.speech.said[-1]
    rig.hear("forget me")
    assert rig.speech.phrases[-1] == "remember_nothing"
    rig.hear("what do you remember about me")
    assert rig.speech.phrases[-1] == "remember_nothing"
    assert rig.arya.context_parts("hi").facts == []
    assert not rig.arya.state().memory
