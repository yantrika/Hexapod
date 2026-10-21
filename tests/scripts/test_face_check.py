"""scripts/face_check.py without a webcam: consent first, its own database, no embeddings shown."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pytest

import config
from brain.memory import Memory
from scripts import face_check
from vision.events import ENROLL_TIMEOUT, EnrollResult


def good(args: argparse.Namespace, memory: Memory, person_id: int) -> EnrollResult:
    return EnrollResult(person_id, tuple(np.full(128, 0.1 * n, dtype=np.float32)
                                         for n in range(1, 6)))


def failed(args: argparse.Namespace, memory: Memory, person_id: int) -> EnrollResult:
    return EnrollResult(person_id, (), ENROLL_TIMEOUT)


def never(args: argparse.Namespace, memory: Memory, person_id: int) -> EnrollResult:
    raise AssertionError("the camera must not be touched without a yes")


def command(db: Path, *words: str) -> list[str]:
    return [*words, "--db", str(db)]


def test_the_default_database_is_not_the_real_memory() -> None:
    assert face_check.DEFAULT_DB.name == "face_check.db"
    assert face_check.DEFAULT_DB != config.MEMORY_DB_PATH


def test_the_real_database_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = face_check.main(["list", "--db", str(config.MEMORY_DB_PATH)])
    assert code == 2 and "never uses the real memory" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["no", "", "y", "yes please", "maybe"])
def test_anything_but_yes_stores_nothing_and_never_opens_the_camera(
        tmp_path: Path, answer: str) -> None:
    db = tmp_path / "face_check.db"
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return answer

    assert face_check.main(command(db, "enroll", "Anna"), ask=ask, collect=never) == 0
    assert asked == ["Remember Anna's face? (yes/no) "]
    memory = Memory(db)
    assert memory.get_person("anna") is None and memory.all_embeddings() == {}
    memory.close()


def test_a_yes_stores_the_samples_with_consent_and_list_shows_no_numbers(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "face_check.db"
    assert face_check.main(command(db, "enroll", "anna"), ask=lambda p: " Yes ", collect=good) == 0
    memory = Memory(db)
    person = memory.get_person("Anna")
    assert person and person.face_consent
    assert len(memory.embeddings(person.id)) == 5
    memory.close()
    capsys.readouterr()
    assert face_check.main(command(db, "list")) == 0
    shown = capsys.readouterr().out
    assert "Anna: 5 samples" in shown and "0.1" not in shown


def test_a_failed_enrolment_keeps_no_consent_and_no_embeddings(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "face_check.db"
    assert face_check.main(command(db, "enroll", "Anna"), ask=lambda p: "yes", collect=failed) == 1
    assert "Enrolment failed" in capsys.readouterr().err
    memory = Memory(db)
    person = memory.get_person("Anna")
    assert person and not person.face_consent and memory.embedding_count(person.id) == 0
    memory.close()


def test_a_failed_re_enrolment_keeps_the_old_face(tmp_path: Path) -> None:
    db = tmp_path / "face_check.db"
    assert face_check.main(command(db, "enroll", "Anna"), ask=lambda p: "yes", collect=good) == 0
    assert face_check.main(command(db, "enroll", "Anna"), ask=lambda p: "yes", collect=failed) == 1
    memory = Memory(db)
    person = memory.get_person("Anna")
    assert person and person.face_consent and memory.embedding_count(person.id) == 5
    memory.close()


def test_forget_asks_first_then_deletes_only_the_face(tmp_path: Path) -> None:
    db = tmp_path / "face_check.db"
    face_check.main(command(db, "enroll", "Anna"), ask=lambda p: "yes", collect=good)
    assert face_check.main(command(db, "forget", "Anna"), ask=lambda p: "no") == 0
    memory = Memory(db)
    person = memory.get_person("Anna")
    assert person and memory.embedding_count(person.id) == 5
    memory.close()
    assert face_check.main(command(db, "forget", "Anna"), ask=lambda p: "yes") == 0
    memory = Memory(db)
    person = memory.get_person("Anna")
    assert person and not person.face_consent and memory.embedding_count(person.id) == 0
    memory.close()
    assert face_check.main(command(db, "forget", "Anna"), ask=lambda p: "yes") == 1  # none left


def test_a_bad_name_or_a_missing_name_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "face_check.db"
    assert face_check.main(command(db, "enroll", "guest"), ask=lambda p: "yes", collect=never) == 2
    assert face_check.main(command(db, "enroll")) == 2
    assert face_check.main(command(db, "forget")) == 2
