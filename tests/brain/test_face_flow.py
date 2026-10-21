"""The spoken enrolment flow: name, read-back, consent, samples. Nothing is stored before a yes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from brain.face_flow import (
    ASK_CONSENT,
    ASK_NAME,
    CONFIRM_NAME,
    ENROLLING,
    IDLE,
    FaceFlow,
    name_from_reply,
)
from brain.memory import Memory
from tests.fakes import FakeClock
from vision.events import (
    ENROLL_MULTIPLE_FACES,
    CancelEnroll,
    Enroll,
    EnrollResult,
)


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
    def __init__(self, path: Path) -> None:
        self.clock = FakeClock()
        self.memory = Memory(path)
        self.speech = FakeSpeech()
        self.sent: list[Any] = []
        self.known: list[str] = []
        self.synced = 0
        self.track: int | None = 7
        self.send_ok = True
        self.flow = FaceFlow(
            self.memory, self.speech, self._send, lambda: self.track,
            lambda name: name.lower() != "badword", self.known.append, self._synced,
            clock=self.clock.now)

    def _send(self, command: Any) -> bool:
        self.sent.append(command)
        return self.send_ok

    def _synced(self) -> None:
        self.synced += 1

    def sample(self, person_id: int, count: int = 3) -> EnrollResult:
        vectors = tuple(np.full(128, i + 1, dtype=np.float32) for i in range(count))
        return EnrollResult(person_id, vectors)

    def to_consent(self, name: str = "Priya") -> None:
        assert self.flow.ask_name(7)
        assert self.flow.consume(name)
        assert self.flow.consume("yes")
        assert self.flow.state == ASK_CONSENT


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path / "arya.db")
    yield made
    made.memory.close()


def test_the_name_in_a_reply() -> None:
    assert name_from_reply("Priya") == "Priya"
    assert name_from_reply("my name is priya") == "Priya"
    assert name_from_reply("it's Priya") == "Priya"
    assert name_from_reply("call me Anna Maria") == "Anna Maria"
    assert name_from_reply("hello") is None
    assert name_from_reply("what is your name") is None
    assert name_from_reply("walk forward") is None
    assert name_from_reply("I am hungry") is None
    assert name_from_reply("tell me a long joke about robots") is None


def test_the_whole_flow_stores_a_face_only_after_two_yeses(rig: Rig) -> None:
    assert rig.flow.ask_name(7) and rig.flow.state == ASK_NAME
    assert "name" in rig.speech.said[-1]
    assert rig.memory.list_people() == []
    assert rig.flow.consume("Priya") and rig.flow.state == CONFIRM_NAME
    assert rig.speech.said[-1] == "Did you say Priya?"
    assert rig.memory.list_people() == []  # a name heard once is not stored
    assert rig.flow.consume("yes") and rig.flow.state == ASK_CONSENT
    assert "remember your face" in rig.speech.said[-1]
    assert rig.memory.list_people() == []  # still nothing: no consent yet
    assert rig.flow.consume("yes") and rig.flow.state == ENROLLING
    person = rig.memory.get_person("Priya")
    assert person is not None and person.face_consent
    assert rig.sent == [Enroll(7, person.id)]
    rig.flow.on_enroll_result(rig.sample(person.id))
    assert rig.flow.state == IDLE
    assert rig.memory.embedding_count(person.id) == 3
    assert rig.known == ["Priya"] and rig.synced == 1
    assert "Got it, Priya" in rig.speech.said[-1]


def test_no_to_the_name_asks_again_then_gives_up(rig: Rig) -> None:
    rig.flow.ask_name(7)
    rig.flow.consume("Pria")
    assert rig.flow.consume("no") and rig.flow.state == ASK_NAME
    rig.flow.consume("Priya")
    assert rig.flow.state == CONFIRM_NAME
    assert rig.flow.consume("no") and rig.flow.state == IDLE  # the second miss
    assert "type your name" in rig.speech.said[-1]
    assert rig.memory.list_people() == []


def test_a_corrected_name_replaces_the_one_being_confirmed(rig: Rig) -> None:
    rig.flow.ask_name(7)
    rig.flow.consume("Pria")
    assert rig.flow.consume("Priya") and rig.speech.said[-1] == "Did you say Priya?"


def test_no_to_consent_keeps_the_name_but_never_asks_vision_for_samples(rig: Rig) -> None:
    rig.to_consent()
    assert rig.flow.consume("no") and rig.flow.state == IDLE
    assert rig.sent == [] and rig.known == ["Priya"]
    assert rig.memory.list_people() == []  # the name is stored by Arya's set_speaker, not here
    assert "won't remember your face" in rig.speech.said[-1]


def test_a_command_or_a_long_sentence_ends_the_question_and_goes_on(rig: Rig) -> None:
    rig.flow.ask_name(7)
    assert not rig.flow.consume("walk forward") and rig.flow.state == IDLE
    rig.flow.ask_name(7)
    assert not rig.flow.consume("what is the weather like today") and rig.flow.state == IDLE
    rig.to_consent()
    assert not rig.flow.consume("walk forward") and rig.flow.state == IDLE
    assert rig.sent == []


def test_a_refused_name_is_not_repeated_or_stored(rig: Rig) -> None:
    rig.flow.ask_name(7)
    assert rig.flow.consume("Badword") and rig.flow.state == IDLE
    assert rig.speech.said[-1] == "I can't use that name."


def test_a_question_nobody_answers_is_dropped(rig: Rig) -> None:
    rig.flow.ask_name(7)
    rig.clock.sleep(config.FACE_FLOW_ANSWER_S + 1)
    rig.flow.tick()
    assert rig.flow.state == IDLE
    rig.flow.ask_name(7)
    rig.clock.sleep(config.FACE_FLOW_ANSWER_S + 1)
    assert not rig.flow.consume("Priya")  # too late: it is an ordinary sentence now


def test_stop_cancels_everything_and_a_running_enrolment_is_cancelled(rig: Rig) -> None:
    rig.to_consent()
    rig.flow.cancel()
    assert rig.flow.state == IDLE and rig.sent == []
    rig.to_consent("Anna")
    rig.flow.consume("yes")
    person = rig.memory.get_person("Anna")
    assert person is not None and person.face_consent
    rig.flow.cancel()
    assert rig.flow.state == IDLE and rig.sent[-1] == CancelEnroll()
    assert not rig.memory.get_person("Anna").face_consent  # type: ignore[union-attr]
    assert rig.memory.embedding_count(person.id) == 0


def test_a_failed_enrolment_leaves_no_yes_behind(rig: Rig) -> None:
    rig.to_consent()
    rig.flow.consume("yes")
    person = rig.memory.get_person("Priya")
    assert person is not None
    rig.flow.on_enroll_result(EnrollResult(person.id, (), ENROLL_MULTIPLE_FACES))
    assert rig.flow.state == IDLE
    assert not rig.memory.get_person("Priya").face_consent  # type: ignore[union-attr]
    assert rig.memory.embedding_count(person.id) == 0
    assert "more than one person" in rig.speech.said[-1] and rig.known == []


def test_a_failed_retry_keeps_the_old_face(rig: Rig) -> None:
    rig.to_consent()
    rig.flow.consume("yes")
    person = rig.memory.get_person("Priya")
    assert person is not None
    rig.flow.on_enroll_result(rig.sample(person.id))
    rig.flow.remember_my_face("Priya")
    assert rig.flow.state == ASK_CONSENT
    rig.flow.consume("yes")
    rig.flow.on_enroll_result(EnrollResult(person.id, (), "timeout"))
    assert rig.memory.get_person("Priya").face_consent  # type: ignore[union-attr]
    assert rig.memory.embedding_count(person.id) == 3


def test_an_enrolment_that_never_answers_times_out(rig: Rig) -> None:
    rig.to_consent()
    rig.flow.consume("yes")
    rig.clock.sleep(config.VISION_ENROLL_TIMEOUT_S + config.FACE_FLOW_ENROLL_MARGIN_S + 1)
    rig.flow.tick()
    assert rig.flow.state == IDLE and rig.sent[-1] == CancelEnroll()
    assert not rig.memory.get_person("Priya").face_consent  # type: ignore[union-attr]


def test_a_full_vision_queue_is_said_and_no_yes_is_left(rig: Rig) -> None:
    rig.to_consent()
    rig.send_ok = False
    rig.flow.consume("yes")
    assert rig.flow.state == IDLE
    assert not rig.memory.get_person("Priya").face_consent  # type: ignore[union-attr]
    assert "try again" in rig.speech.said[-1]


def test_a_result_for_someone_else_is_ignored(rig: Rig) -> None:
    rig.to_consent()
    rig.flow.consume("yes")
    rig.flow.on_enroll_result(rig.sample(999))
    assert rig.flow.state == ENROLLING


def test_remember_my_face_for_a_known_speaker_and_for_a_guest(rig: Rig) -> None:
    rig.flow.remember_my_face("Anna")
    assert rig.flow.state == ASK_CONSENT and "Anna" in rig.speech.said[-1]
    rig.flow.cancel()
    rig.flow.remember_my_face(config.GUEST_NAME)
    assert rig.flow.state == ASK_NAME
    rig.flow.cancel()
    rig.track = None
    rig.flow.remember_my_face("Anna")
    assert rig.flow.state == IDLE and "exactly one face" in rig.speech.said[-1]


def test_forget_my_face_deletes_the_samples_and_tells_vision(rig: Rig) -> None:
    person = rig.memory.ensure_person("Anna")
    assert person is not None
    rig.memory.set_face_consent(person.id, True)
    rig.memory.replace_embeddings(person.id, [b"\x01" * 512])
    rig.flow.forget_my_face("Anna")
    assert rig.memory.embedding_count(person.id) == 0 and rig.synced == 1
    assert rig.memory.get_person("Anna") is not None  # the person stays, only the face goes
    rig.flow.forget_my_face("Anna")
    assert rig.speech.said[-1] == "I don't have your face."


def test_without_a_database_nothing_is_asked(tmp_path: Path) -> None:
    speech = FakeSpeech()
    flow = FaceFlow(None, speech, lambda c: True, lambda: 1, lambda n: True, lambda n: None,
                    lambda: None)
    assert not flow.ask_name(1) and flow.state == IDLE
    flow.remember_my_face("Anna")
    assert speech.phrases == ["memory_off"]
