"""The IoU tracker on synthetic boxes: appear, move, lost, re-appear."""

from __future__ import annotations

from vision.detector import Box
from vision.tracker import IouTracker, TrackEvent, iou

FACE: Box = (100.0, 60.0, 50.0, 50.0)


def shifted(box: Box, dx: float) -> Box:
    return (box[0] + dx, box[1], box[2], box[3])


def tracker() -> IouTracker:
    return IouTracker(iou_threshold=0.3, appear_frames=2, lost_frames=3)


def test_iou_of_identical_disjoint_and_half_overlapping_boxes() -> None:
    assert iou(FACE, FACE) == 1.0
    assert iou(FACE, shifted(FACE, 500)) == 0.0
    assert abs(iou(FACE, shifted(FACE, 25)) - 1 / 3) < 1e-9


def test_a_person_appears_only_after_the_debounce_frames() -> None:
    t = tracker()
    first = t.update([FACE])
    assert first.events == [] and first.visible == []
    second = t.update([FACE])
    assert second.events == [TrackEvent("appeared", 1)]
    assert [v.track_id for v in second.visible] == [1]
    assert t.update([FACE]).events == []  # no second "appeared"


def test_one_frame_flicker_never_appears() -> None:
    t = tracker()
    t.update([FACE])
    t.update([])
    t.update([FACE])
    assert t.confirmed_ids == []


def test_a_moving_face_keeps_its_id() -> None:
    t = tracker()
    box = FACE
    for _ in range(2):
        t.update([box])
    for _ in range(6):
        box = shifted(box, 10)
        result = t.update([box])
        assert result.events == []
        assert [v.track_id for v in result.visible] == [1]


def test_lost_is_debounced_and_then_reported_once() -> None:
    t = tracker()
    t.update([FACE])
    t.update([FACE])
    assert t.update([]).events == []
    assert t.update([]).events == []
    assert t.update([]).events == [TrackEvent("left", 1)]
    assert t.update([]).events == []


def test_a_short_gap_does_not_lose_the_person() -> None:
    t = tracker()
    t.update([FACE])
    t.update([FACE])
    t.update([])
    result = t.update([FACE])
    assert result.events == []
    assert [v.track_id for v in result.visible] == [1]


def test_reappearing_after_left_is_a_new_track() -> None:
    t = tracker()
    t.update([FACE])
    t.update([FACE])
    for _ in range(3):
        t.update([])
    t.update([FACE])
    result = t.update([FACE])
    assert result.events == [TrackEvent("appeared", 2)]


def test_two_faces_get_two_ids_and_keep_them() -> None:
    t = tracker()
    left, right = FACE, shifted(FACE, 150)
    t.update([left, right])
    result = t.update([left, right])
    assert sorted(e.track_id for e in result.events) == [1, 2]
    again = t.update([shifted(left, 5), shifted(right, 5)])
    assert sorted(v.track_id for v in again.visible) == [1, 2]
    ids_by_x = {v.track_id: v.box[0] for v in again.visible}
    assert ids_by_x[1] < ids_by_x[2]


def test_reset_reports_left_for_every_confirmed_person() -> None:
    t = tracker()
    t.update([FACE])
    t.update([FACE])
    assert t.reset() == [TrackEvent("left", 1)]
    assert t.confirmed_ids == []
