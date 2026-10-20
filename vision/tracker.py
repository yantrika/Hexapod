"""A simple IoU tracker: stable ids, and appear/lost debounce so one bad frame changes nothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import config
from vision.detector import Box


def iou(a: Box, b: Box) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


class TrackEvent(NamedTuple):
    kind: str  # "appeared" or "left"
    track_id: int


class VisibleTrack(NamedTuple):
    track_id: int
    box: Box


@dataclass
class _Track:
    box: Box
    hits: int = 1  # frames in a row with a detection (while tentative)
    misses: int = 0  # frames in a row without one
    track_id: int | None = None  # given when the track is confirmed
    seen_now: bool = field(default=True)


class TrackerResult(NamedTuple):
    events: list[TrackEvent]
    visible: list[VisibleTrack]  # confirmed tracks that have a detection in THIS frame


class IouTracker:
    """A face becomes a person (id assigned, "appeared") after ``appear_frames`` frames in a row,
    and the person is "left" after ``lost_frames`` frames in a row without a matching box. A
    face that was never confirmed and misses one frame is forgotten silently. A person who comes
    back after "left" is a new track with a new id."""

    def __init__(self, iou_threshold: float = config.VISION_TRACK_IOU,
                 appear_frames: int = config.VISION_APPEAR_FRAMES,
                 lost_frames: int = config.VISION_LOST_FRAMES) -> None:
        self.iou_threshold = iou_threshold
        self.appear_frames = appear_frames
        self.lost_frames = lost_frames
        self._tracks: list[_Track] = []
        self._next_id = 1

    @property
    def confirmed_ids(self) -> list[int]:
        return [t.track_id for t in self._tracks if t.track_id is not None]

    def reset(self) -> list[TrackEvent]:
        """Forget everything (the camera went away): "left" for every confirmed person."""
        events = [TrackEvent("left", track_id) for track_id in self.confirmed_ids]
        self._tracks = []
        return events

    def update(self, boxes: list[Box]) -> TrackerResult:
        pairs = sorted(((iou(track.box, box), ti, bi)
                        for ti, track in enumerate(self._tracks) for bi, box in enumerate(boxes)),
                       reverse=True)
        used_tracks: set[int] = set()
        used_boxes: set[int] = set()
        for overlap, ti, bi in pairs:
            if overlap < self.iou_threshold:
                break
            if ti in used_tracks or bi in used_boxes:
                continue
            used_tracks.add(ti)
            used_boxes.add(bi)
            track = self._tracks[ti]
            track.box = boxes[bi]
            track.hits += 1
            track.misses = 0
            track.seen_now = True

        events: list[TrackEvent] = []
        kept: list[_Track] = []
        for ti, track in enumerate(self._tracks):
            if ti not in used_tracks:
                track.misses += 1
                track.hits = 0
                track.seen_now = False
                if track.track_id is None or track.misses >= self.lost_frames:
                    if track.track_id is not None:
                        events.append(TrackEvent("left", track.track_id))
                    continue
            kept.append(track)
        for bi, box in enumerate(boxes):
            if bi not in used_boxes:
                kept.append(_Track(box=box))
        self._tracks = kept

        for track in self._tracks:
            if track.track_id is None and track.hits >= self.appear_frames:
                track.track_id = self._next_id
                self._next_id += 1
                events.append(TrackEvent("appeared", track.track_id))
        visible = [VisibleTrack(t.track_id, t.box) for t in self._tracks
                   if t.track_id is not None and t.seen_now]
        return TrackerResult(events, visible)
