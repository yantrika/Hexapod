"""Enrolment: K good samples within T seconds, clean failures, and no image ever kept."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.vision_fakes import (
    FACE,
    FakeCamera,
    FakeClock,
    FakeDetector,
    FakeRecognizer,
    run_face_loop,
)
from vision.detector import Box, Detection
from vision.enrollment import EnrollmentSession
from vision.events import (
    ENROLL_BUSY,
    ENROLL_CANCELLED,
    ENROLL_INCONSISTENT,
    ENROLL_LOW_QUALITY,
    ENROLL_MULTIPLE_FACES,
    ENROLL_NO_RECOGNIZER,
    ENROLL_PERSON_LEFT,
    ENROLL_TIMEOUT,
    ENROLL_UNKNOWN_TRACK,
    CancelEnroll,
    Enroll,
    EnrollResult,
)

SECOND_FACE: Box = (230.0, 150.0, 40.0, 40.0)


def anna(i: int) -> np.ndarray:
    return np.array([1.0, 0.01 * i, 0.0, 0.0], dtype=np.float32)  # a little different each time


def enrol(script: list[tuple[float, object]], boxes: list[Box] | None = None,
          detector: FakeDetector | None = None, with_recognizer: bool = True,
          quality: Callable[[Any, Detection], str | None] | None = None,
          stop_at: float = 12.0) -> list[EnrollResult]:
    clock = FakeClock()
    _, replies = run_face_loop(
        FakeCamera(clock, stop_at), clock,
        detector or FakeDetector(lambda i: boxes if boxes is not None else [FACE]),
        FakeRecognizer(anna) if with_recognizer else None, script, quality=quality)
    return replies


# -- through the loop -----------------------------------------------------------------------------

def test_enrolment_collects_k_embeddings_and_returns_only_vectors() -> None:
    replies = enrol([(2.0, Enroll(1, 7))])
    assert len(replies) == 1
    result = replies[0]
    assert result.ok and result.person_id == 7 and result.reason is None
    assert len(result.embeddings) == 5
    assert all(isinstance(e, np.ndarray) and e.dtype == np.float32 for e in result.embeddings)
    assert "array" not in repr(result) and str(result) == "enroll_result(person 7, 5 samples)"


def test_enrolment_fails_when_the_person_leaves() -> None:
    detector = FakeDetector(lambda i: [FACE] if i < 10 else [])
    replies = enrol([(2.0, Enroll(1, 7))], detector=detector)
    assert [(r.reason, r.embeddings) for r in replies] == [(ENROLL_PERSON_LEFT, ())]


def test_enrolment_fails_when_a_second_face_is_in_view() -> None:
    detector = FakeDetector(lambda i: [FACE] if i < 8 else [FACE, SECOND_FACE])
    replies = enrol([(2.0, Enroll(1, 7))], detector=detector)
    assert [(r.reason, r.embeddings) for r in replies] == [(ENROLL_MULTIPLE_FACES, ())]


def test_enrolment_fails_when_the_quality_stays_too_low() -> None:
    replies = enrol([(2.0, Enroll(1, 7))], quality=lambda frame, detection: "blurry",
                    stop_at=22.0)
    assert [(r.reason, r.embeddings) for r in replies] == [(ENROLL_LOW_QUALITY, ())]


def test_cancel_enroll_ends_it_with_a_cancelled_result() -> None:
    replies = enrol([(2.0, Enroll(1, 7)), (2.6, CancelEnroll())])
    assert [(r.reason, r.embeddings) for r in replies] == [(ENROLL_CANCELLED, ())]


def test_a_second_enrol_while_one_runs_is_busy_and_the_first_still_finishes() -> None:
    replies = enrol([(2.0, Enroll(1, 7)), (2.0, Enroll(1, 8))])
    assert replies[0].reason == ENROLL_BUSY and replies[0].person_id == 8
    assert replies[1].ok and replies[1].person_id == 7


def test_enrolling_a_track_that_does_not_exist_fails_at_once() -> None:
    replies = enrol([(2.0, Enroll(99, 7))])
    assert [r.reason for r in replies] == [ENROLL_UNKNOWN_TRACK]


def test_enrolling_without_a_recognizer_fails_cleanly() -> None:
    replies = enrol([(2.0, Enroll(1, 7))], with_recognizer=False)
    assert [r.reason for r in replies] == [ENROLL_NO_RECOGNIZER]


def test_enrolment_writes_nothing_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert enrol([(2.0, Enroll(1, 7))])[0].ok
    assert list(tmp_path.rglob("*")) == []


# -- the session on its own (explicit time) -------------------------------------------------------

def session_run(session: EnrollmentSession, frames: list[tuple[float, bool, int]],
                vectors: list[list[float]]) -> EnrollResult | None:
    """frames: (time, face seen, faces in the picture). The n-th embedding is vectors[n]."""
    taken = iter(vectors)
    for now, seen, count in frames:
        result = session.step(
            now, Detection(box=FACE, score=0.9) if seen else None, count,
            check=lambda d: None, embed=lambda d: np.array(next(taken), dtype=np.float32))
        if result is not None:
            return result
    return None


def test_the_session_times_out_with_fewer_samples_than_wanted() -> None:
    session = EnrollmentSession(1, 7, 0.0, samples=5, timeout_s=3.0, gap_s=10.0)
    result = session_run(session, [(0.5 * n, True, 1) for n in range(9)], [[1, 0]] * 9)
    assert result is not None and result.reason == ENROLL_TIMEOUT and result.embeddings == ()


def test_samples_of_different_people_are_inconsistent() -> None:
    session = EnrollmentSession(1, 7, 0.0, samples=4, gap_s=0.0)
    result = session_run(session, [(0.1 * n, True, 1) for n in range(6)],
                         [[1, 0], [1, 0.1], [0, 1], [0, 1]])
    assert result is not None and result.reason == ENROLL_INCONSISTENT and result.embeddings == ()


def test_the_session_waits_the_gap_between_samples() -> None:
    session = EnrollmentSession(1, 7, 0.0, samples=3, gap_s=1.0)
    result = session_run(session, [(0.25 * n, True, 1) for n in range(20)], [[1, 0]] * 3)
    assert result is not None and result.ok and session.collected == 0  # handed over, not kept
