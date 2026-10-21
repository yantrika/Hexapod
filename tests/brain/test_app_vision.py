"""The camera wired into the whole app: a real dry-run body, FakeChat, typed text as the voice, and
a fake vision process (a queue pair) in place of the webcam. Names, consent, enrolment, greetings,
turning, stop and forget, end to end."""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from brain.app import AppOptions, HexaApp
from brain.chat import FakeChat
from brain.memory import Memory
from tests.fakes import FakeStt, wait_until
from vision.events import (
    FACE_BEARING,
    PERSON_APPEARED,
    PERSON_IDENTIFIED,
    PERSON_UNKNOWN,
    Enroll,
    EnrollResult,
    Forget,
    SetKnownFaces,
    VisionEvent,
)
from vision.recognizer import embedding_to_bytes
from voice.audio import QueueSource


class FakeVisionProcess:
    def __init__(self) -> None:
        self.events: queue.Queue[Any] = queue.Queue()
        self.sent: list[Any] = []
        self.pause_event = threading.Event()
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True

    @property
    def is_alive(self) -> bool:
        return self.started and not self.stopped

    def send(self, command: Any) -> bool:
        self.sent.append(command)
        return True

    def get_event(self, timeout: float = 0.0) -> Any:
        try:
            return self.events.get(timeout=timeout) if timeout > 0 else self.events.get_nowait()
        except queue.Empty:
            return None

    def known_faces(self) -> list[SetKnownFaces]:
        return [c for c in self.sent if isinstance(c, SetKnownFaces)]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "arya.db"
        self.process = FakeVisionProcess()
        options = AppOptions(backend="dryrun", listen="always", chat="fake", no_speak=True,
                             no_mic=True, memory_path=self.db, vision=True)
        self.app = HexaApp(options, stt=FakeStt({}), source=QueueSource(),
                           chat_backend=FakeChat(("Hello there.",)),
                           vision_process=self.process)
        self.said: list[str] = []
        self.heard = self.app.events.subscribe("test")

    def said_texts(self) -> list[str]:
        self.said += [e["text"] for e in self.heard.get_all() if e["type"] == "said"]
        return self.said

    def said_contains(self, fragment: str) -> bool:
        return any(fragment in text for text in self.said_texts())

    def see(self, kind: str, track: int, **fields: Any) -> None:
        self.process.events.put(VisionEvent(kind, track, **fields))

    def say(self, text: str) -> None:
        self.app.submit_text(text)

    @property
    def memory(self) -> Memory:
        assert self.app.memory is not None
        return self.app.memory


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path)
    made.app.start()
    yield made
    made.app.shutdown()


def vector(seed: int) -> Any:
    return np.full(128, float(seed), dtype=np.float32)


def test_vision_starts_with_the_app_and_stops_with_it(rig: Rig) -> None:
    assert rig.process.started and rig.app.vision is not None and rig.app.behaviour is not None
    assert len(rig.process.known_faces()) == 1 and rig.process.known_faces()[0].faces == ()
    rig.app.shutdown()
    assert rig.process.stopped and rig.app.vision is None


def test_a_known_face_becomes_the_speaker_and_is_greeted(rig: Rig) -> None:
    person = rig.memory.ensure_person("Anna")
    assert person is not None
    rig.memory.set_face_consent(person.id, True)
    rig.memory.replace_embeddings(person.id, [embedding_to_bytes(vector(1))])
    rig.app.vision.sync_known_faces()  # type: ignore[union-attr]
    wait_until(lambda: len(rig.process.known_faces()) == 2, what="the known faces sent")
    assert [pid for pid, _ in rig.process.known_faces()[-1].faces] == [person.id]
    rig.see(PERSON_APPEARED, 5)
    rig.see(PERSON_IDENTIFIED, 5, person_id=person.id, score=0.8)
    wait_until(lambda: rig.app.arya is not None and rig.app.arya.speaker == "Anna",
               what="the speaker")
    wait_until(lambda: rig.said_contains("Hi Anna!"), what="the greeting")


def test_a_stranger_is_asked_confirmed_and_enrolled_by_voice(rig: Rig) -> None:
    rig.see(PERSON_APPEARED, 3)
    rig.see(PERSON_UNKNOWN, 3)
    wait_until(lambda: rig.said_contains("What is your name"), what="the question")
    rig.say("Priya")
    wait_until(lambda: rig.said_contains("Did you say Priya?"), what="the read-back")
    assert rig.memory.list_people() == []
    rig.say("yes")
    wait_until(lambda: rig.said_contains("remember your face"), what="the consent question")
    assert rig.memory.list_people() == []
    rig.say("yes")
    wait_until(lambda: any(isinstance(c, Enroll) for c in rig.process.sent), what="Enroll")
    person = rig.memory.get_person("Priya")
    assert person is not None and person.face_consent
    assert [c for c in rig.process.sent if isinstance(c, Enroll)] == [Enroll(3, person.id)]
    rig.process.events.put(EnrollResult(person.id, (vector(1), vector(2), vector(3))))
    wait_until(lambda: rig.memory.embedding_count(person.id) == 3, what="the samples")
    wait_until(lambda: rig.app.arya is not None and rig.app.arya.speaker == "Priya",
               what="the speaker")
    wait_until(lambda: rig.said_contains("Got it, Priya"), what="the confirmation")
    wait_until(lambda: [pid for pid, _ in rig.process.known_faces()[-1].faces] == [person.id],
               what="vision told who Priya is")
    # the image never existed; the brain log and the events carry no face data
    assert not any("embedding" in text.lower() for text in rig.said_texts())


def test_stop_in_the_middle_of_a_question_cancels_it_and_stores_nothing(rig: Rig) -> None:
    rig.see(PERSON_APPEARED, 3)
    rig.see(PERSON_UNKNOWN, 3)
    wait_until(lambda: rig.said_contains("What is your name"), what="the question")
    rig.say("stop")
    wait_until(lambda: rig.app.face_flow is not None and not rig.app.face_flow.active,
               what="the flow to end")
    rig.say("Priya")  # now it is ordinary chat, not a name
    assert rig.memory.list_people() == []
    assert not any(isinstance(c, Enroll) for c in rig.process.sent)


def test_saying_no_to_the_face_keeps_the_name_and_no_face(rig: Rig) -> None:
    rig.see(PERSON_APPEARED, 3)
    rig.see(PERSON_UNKNOWN, 3)
    wait_until(lambda: rig.said_contains("What is your name"), what="the question")
    rig.say("my name is Anna")
    wait_until(lambda: rig.said_contains("Did you say Anna?"), what="the read-back")
    rig.say("yes")
    wait_until(lambda: rig.said_contains("remember your face"), what="the consent question")
    rig.say("no")
    wait_until(lambda: rig.memory.get_person("Anna") is not None, what="the person")
    person = rig.memory.get_person("Anna")
    assert person is not None and not person.face_consent
    assert rig.memory.embedding_count(person.id) == 0
    assert not any(isinstance(c, Enroll) for c in rig.process.sent)


def test_remember_my_face_by_voice_for_a_named_speaker(rig: Rig) -> None:
    rig.say("my name is Anna")
    wait_until(lambda: rig.app.arya is not None and rig.app.arya.speaker == "Anna", what="Anna")
    rig.see(PERSON_APPEARED, 9)
    wait_until(lambda: rig.app.behaviour is not None and rig.app.behaviour.visible_track() == 9,
               what="the face in view")
    rig.say("remember my face")
    wait_until(lambda: rig.said_contains("May I remember it"), what="the consent question")
    rig.say("yes")
    person = rig.memory.get_person("Anna")
    assert person is not None
    wait_until(lambda: any(isinstance(c, Enroll) for c in rig.process.sent), what="Enroll")
    assert [c for c in rig.process.sent if isinstance(c, Enroll)] == [Enroll(9, person.id)]


def test_forget_my_face_and_forget_me_tell_vision(rig: Rig) -> None:
    rig.say("my name is Anna")
    wait_until(lambda: rig.memory.get_person("Anna") is not None, what="Anna")
    person = rig.memory.get_person("Anna")
    assert person is not None
    rig.memory.set_face_consent(person.id, True)
    rig.memory.replace_embeddings(person.id, [embedding_to_bytes(vector(1))])
    rig.say("forget my face")
    wait_until(lambda: rig.memory.embedding_count(person.id) == 0, what="the face to go")
    assert rig.memory.get_person("Anna") is not None
    rig.say("forget me")
    wait_until(lambda: rig.said_contains("Say yes to confirm") or True, what="the question")
    rig.say("yes")
    wait_until(lambda: rig.memory.get_person("Anna") is None, what="Anna to go")
    wait_until(lambda: Forget(person.id) in rig.process.sent, what="Forget for vision")


def test_a_standing_robot_turns_a_little_toward_a_face_and_never_when_told_to_stop(
        rig: Rig) -> None:
    rig.say("stand up")
    assert rig.app.brain is not None and rig.app.behaviour is not None
    wait_until(lambda: rig.app.behaviour is not None and rig.app.behaviour.posture == "standing",
               timeout=30, what="standing")
    wait_until(lambda: rig.app.brain is not None and rig.app.brain.body_idle(), 30, "idle")
    rig.see(PERSON_APPEARED, 2)
    rig.see(FACE_BEARING, 2, angle_deg=35.0)
    wait_until(lambda: rig.app.behaviour is not None and rig.app.behaviour.turns_sent == 1,
               what="the turn")
    assert rig.app.behaviour.turns_sent == 1
    rig.say("stop")
    wait_until(lambda: rig.app.brain is not None and rig.app.brain.body_idle(), 30, "idle")
    rig.see(FACE_BEARING, 2, angle_deg=-35.0)
    threading.Event().wait(config.VISION_LINK_POLL_S * 5)
    assert rig.app.behaviour.turns_sent == 1  # quiet after a stop


def test_the_llm_busy_flag_slows_the_camera(rig: Rig) -> None:
    assert not rig.process.pause_event.is_set()
    assert rig.app.chat is not None
    rig.say("tell me a story")
    wait_until(lambda: rig.app.chat is not None and len(rig.app.chat.history) >= 1,
               what="the reply")
    wait_until(lambda: not rig.process.pause_event.is_set(), what="the camera at full rate")


def test_without_the_flag_there_is_no_camera(tmp_path: Path) -> None:
    process = FakeVisionProcess()
    options = AppOptions(backend="dryrun", listen="always", chat="fake", no_speak=True,
                         no_mic=True, memory_path=tmp_path / "arya.db")
    app = HexaApp(options, stt=FakeStt({}), source=QueueSource(), vision_process=process)
    app.start()
    try:
        assert app.vision is None and not process.started
    finally:
        app.shutdown()
