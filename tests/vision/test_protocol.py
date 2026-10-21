"""The brain <-> vision protocol end to end, with a real (temporary) database on the brain side
and a fake camera and recognizer on the vision side."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from brain.memory import Memory
from tests.vision_fakes import (
    FACE,
    FakeCamera,
    FakeClock,
    FakeDetector,
    FakeRecognizer,
    run_face_loop,
)
from vision.events import (
    PERSON_IDENTIFIED,
    PERSON_UNKNOWN,
    Enroll,
    EnrollResult,
    Forget,
    SetKnownFaces,
)
from vision.recognizer import embedding_from_bytes, embedding_to_bytes

ANNA = np.array([1.0] + [0.0] * 127, dtype=np.float32)  # 128 numbers, like SFace


def look_at(who: np.ndarray, script: list[tuple[float, object]], stop_at: float = 10.0,
            ) -> tuple[list[str], list[EnrollResult]]:
    clock = FakeClock()
    events, replies = run_face_loop(
        FakeCamera(clock, stop_at), clock, FakeDetector(lambda i: [FACE]),
        FakeRecognizer(lambda i: who), script)
    return [e.kind for e in events if e.kind in (PERSON_IDENTIFIED, PERSON_UNKNOWN)], replies


def known_faces(memory: Memory) -> SetKnownFaces:
    return SetKnownFaces(tuple(
        (person_id, tuple(embedding_from_bytes(blob) for blob in blobs))
        for person_id, blobs in memory.all_embeddings().items()))


def test_enrol_store_recognise_forget_round_trip(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "brain" / "face.db")
    person = memory.ensure_person("anna")
    assert person
    # 1. no consent yet: the brain cannot store, so it must not even start (and vision is not asked)
    assert not memory.add_embedding(person.id, embedding_to_bytes(ANNA))
    assert memory.all_embeddings() == {}
    # 2. the person said yes: the brain records it and asks vision to enrol track 1
    assert memory.set_face_consent(person.id, True)
    _, replies = look_at(ANNA, [(2.0, Enroll(1, person.id))])
    assert len(replies) == 1 and replies[0].ok and replies[0].person_id == person.id
    blobs = [embedding_to_bytes(e) for e in replies[0].embeddings]
    assert memory.replace_embeddings(person.id, blobs)
    # 3. the brain sends what it stored; vision names the face
    kinds, _ = look_at(ANNA, [(0.0, known_faces(memory))])
    assert kinds == [PERSON_IDENTIFIED]
    # 4. forget: the brain deletes, then tells vision; nothing is recognised any more
    assert memory.forget_person(person.id)
    assert memory.all_embeddings() == {}
    kinds, _ = look_at(ANNA, [(0.0, known_faces(memory)), (0.0, Forget(person.id))])
    assert kinds == [PERSON_UNKNOWN]
    memory.close()


def test_a_forgotten_person_is_dropped_from_a_running_vision(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "face.db")
    person = memory.ensure_person("anna")
    assert person
    memory.set_face_consent(person.id, True)
    memory.replace_embeddings(person.id, [embedding_to_bytes(ANNA)])
    kinds, _ = look_at(ANNA, [(0.0, known_faces(memory)), (6.0, Forget(person.id))], stop_at=14.0)
    assert kinds == [PERSON_IDENTIFIED, PERSON_UNKNOWN]
    memory.close()


def test_the_database_holds_embeddings_only_and_no_other_file_is_written(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    memory = Memory(tmp_path / "brain" / "face.db")
    person = memory.ensure_person("anna")
    assert person
    memory.set_face_consent(person.id, True)
    _, replies = look_at(ANNA, [(2.0, Enroll(1, person.id))])
    memory.replace_embeddings(person.id, [embedding_to_bytes(e) for e in replies[0].embeddings])
    assert list(work.rglob("*")) == []  # vision wrote nothing
    assert {len(blob) for blob in memory.embeddings(person.id)} == {128 * 4}  # numbers, not pixels
    with memory._lock:
        columns = [row[1] for row in memory._db.execute("PRAGMA table_info(face_embeddings)")]
    assert columns == ["id", "person_id", "vector", "created"]
    memory.close()
    files = [p for p in (tmp_path / "brain").rglob("*") if p.is_file()]
    assert all(p.name.startswith("face.db") for p in files)
