"""Recognition in the vision loop: voting, the match threshold, the quality gate, the events."""

from __future__ import annotations

import numpy as np
import pytest

import config
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
    Forget,
    SetKnownFaces,
    VisionEvent,
)
from vision.quality import BLURRY, NO_LANDMARKS, NOT_FRONTAL, TOO_SMALL, face_quality, yaw_ratio
from vision.recognizer import IdentityVoter, KnownFaces, cosine_similarity

ANNA = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
STRANGER = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)


def known(*people: tuple[int, np.ndarray]) -> SetKnownFaces:
    return SetKnownFaces(tuple((person_id, (vector,)) for person_id, vector in people))


def of_kind(events: list[VisionEvent], kind: str) -> list[VisionEvent]:
    return [e for e in events if e.kind == kind]


def run(who: list[np.ndarray], script: list[tuple[float, object]], stop_at: float = 10.0,
        ) -> tuple[list[VisionEvent], FakeRecognizer]:
    clock = FakeClock()
    recognizer = FakeRecognizer(lambda i: who[i] if i < len(who) else who[-1])
    events, _ = run_face_loop(FakeCamera(clock, stop_at), clock,
                              FakeDetector(lambda i: [FACE]), recognizer, script)
    return events, recognizer


# -- voting ---------------------------------------------------------------------------------------

def test_a_name_needs_m_of_the_last_n_matches() -> None:
    voter = IdentityVoter(window=5, needed=3)
    assert voter.add(1, 0.9) is None and voter.add(1, 0.8) is None
    assert voter.add(None, 0.1) is None  # a miss does not help a name
    assert voter.add(2, 0.7) is None  # another person's vote does not either
    decision = voter.add(1, 0.7)
    assert decision is not None and decision[0] == 1 and abs(decision[1] - 0.8) < 1e-6


def test_old_votes_fall_out_of_the_window() -> None:
    voter = IdentityVoter(window=3, needed=3)
    voter.add(1, 0.9)
    voter.add(1, 0.9)
    assert voter.add(None, 0.0) is None
    assert voter.add(1, 0.9) is None  # the window holds 1, None, 1
    assert voter.add(1, 0.9) is None  # then None, 1, 1: the miss has not left yet
    assert voter.add(1, 0.9) is not None  # 1, 1, 1


# -- threshold ------------------------------------------------------------------------------------

def test_the_default_threshold_is_the_documented_sface_cosine_value() -> None:
    assert config.VISION_MATCH_THRESHOLD == 0.363


def test_the_threshold_boundary_matches_at_equal_and_not_below() -> None:
    faces = KnownFaces()
    faces.replace([(7, [ANNA])])
    probe = np.array([0.5, np.sqrt(0.75), 0.0, 0.0], dtype=np.float32)
    score = cosine_similarity(probe, ANNA)
    assert faces.match(probe, threshold=score) == (7, score)  # equal to the threshold: a match
    assert faces.match(probe, threshold=score + 1e-4)[0] is None
    assert faces.match(probe, threshold=score - 1e-4)[0] == 7


def test_the_best_of_several_embeddings_names_its_owner() -> None:
    faces = KnownFaces()
    faces.replace([(1, [ANNA]), (2, [STRANGER, np.array([0.0, 1.0, 0.0, 0.0])])])
    assert faces.match(np.array([0.0, 0.9, 0.1, 0.0]), 0.5)[0] == 2
    faces.forget(2)
    assert faces.match(np.array([0.0, 0.9, 0.1, 0.0]), 0.5)[0] is None
    assert len(faces) == 1


# -- quality gate ---------------------------------------------------------------------------------

def row(width: float = 60.0, nose_x: float = 40.0) -> np.ndarray:
    return np.array([10, 10, width, width, 30, 30, 50, 30, nose_x, 45, 33, 55, 47, 55, 0.9],
                    dtype=np.float32)


def test_the_gate_rejects_small_turned_and_unlandmarked_faces() -> None:
    frame = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    assert face_quality(frame, row(width=20.0)) == TOO_SMALL
    assert face_quality(frame, row(nose_x=58.0)) == NOT_FRONTAL
    assert face_quality(frame, None) == NO_LANDMARKS
    assert yaw_ratio(row()) == 0.0 and yaw_ratio(row(nose_x=50.0)) == 0.5


def test_the_gate_rejects_a_blurry_crop_and_passes_a_sharp_one() -> None:
    pytest.importorskip("cv2")
    sharp = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    flat = np.full((100, 100, 3), 120, dtype=np.uint8)
    assert face_quality(sharp, row()) is None
    assert face_quality(flat, row()) == BLURRY


def test_bad_crops_are_skipped_not_matched() -> None:
    clock = FakeClock()
    recognizer = FakeRecognizer(lambda i: ANNA)
    events, _ = run_face_loop(
        FakeCamera(clock, 8.0), clock, FakeDetector(lambda i: [FACE]), recognizer,
        [(0.0, known((1, ANNA)))], quality=lambda frame, detection: BLURRY)
    assert recognizer.calls == 0
    assert not of_kind(events, PERSON_IDENTIFIED) and not of_kind(events, PERSON_UNKNOWN)


# -- events ---------------------------------------------------------------------------------------

def test_a_known_face_is_identified_once_and_rechecked_at_a_low_rate() -> None:
    events, recognizer = run([ANNA], [(0.0, known((1, ANNA)))])
    identified = of_kind(events, PERSON_IDENTIFIED)
    assert len(identified) == 1
    assert identified[0].person_id == 1 and identified[0].track_id == 1
    assert identified[0].score is not None and identified[0].score > 0.99
    assert not of_kind(events, PERSON_UNKNOWN)
    assert recognizer.calls <= 3 + 3  # three votes, then one look every VISION_RECHECK_S


def test_two_matches_in_five_are_not_a_name_and_the_track_becomes_unknown() -> None:
    who = [ANNA, STRANGER, ANNA, STRANGER, STRANGER, STRANGER]
    events, _ = run(who, [(0.0, known((1, ANNA)))])
    assert not of_kind(events, PERSON_IDENTIFIED)
    assert len(of_kind(events, PERSON_UNKNOWN)) == 1


def test_one_wrong_look_among_right_ones_still_names_the_person() -> None:
    events, _ = run([ANNA, STRANGER, ANNA, ANNA], [(0.0, known((1, ANNA)))])
    assert [e.person_id for e in of_kind(events, PERSON_IDENTIFIED)] == [1]


def test_a_stranger_is_unknown_after_n_good_looks_once() -> None:
    events, _ = run([STRANGER], [(0.0, known((1, ANNA)))])
    unknown = of_kind(events, PERSON_UNKNOWN)
    assert len(unknown) == 1 and unknown[0].track_id == 1
    assert not of_kind(events, PERSON_IDENTIFIED)


def test_with_nobody_enrolled_no_embedding_is_computed_and_the_track_is_unknown() -> None:
    events, recognizer = run([ANNA], [])
    assert recognizer.calls == 0
    assert len(of_kind(events, PERSON_UNKNOWN)) == 1


def test_new_known_faces_name_a_track_that_was_unknown() -> None:
    events, _ = run([ANNA], [(4.0, known((1, ANNA)))])
    kinds = [e.kind for e in events if e.kind in (PERSON_UNKNOWN, PERSON_IDENTIFIED)]
    assert kinds == [PERSON_UNKNOWN, PERSON_IDENTIFIED]


def test_forget_drops_the_person_from_vision_at_once() -> None:
    events, _ = run([ANNA], [(0.0, known((1, ANNA))), (6.0, Forget(1))], stop_at=14.0)
    assert len(of_kind(events, PERSON_IDENTIFIED)) == 1  # never named again after the forget
    unknown = of_kind(events, PERSON_UNKNOWN)
    assert len(unknown) == 1 and unknown[0].time > 6.0


def test_event_text_never_shows_an_embedding() -> None:
    command = known((1, ANNA))
    assert "array" not in repr(command) and "1.0" not in repr(command)
    event = VisionEvent(PERSON_IDENTIFIED, 3, time=1.0, person_id=2, score=0.5)
    assert str(event) == "person_identified(track 3, person 2, score 0.50)"
