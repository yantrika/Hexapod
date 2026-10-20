"""Enrolment: collect K good face samples of one tracked person within T seconds.

The session never sees an image: the loop hands it callbacks that check a face and embed it, and
it keeps only the embeddings. It fails cleanly (a reason, no embeddings) when the person leaves,
a second face is in view, the quality stays too low, the time runs out, or the samples do not look
like one person."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import config
from vision.detector import Detection
from vision.events import (
    ENROLL_INCONSISTENT,
    ENROLL_LOW_QUALITY,
    ENROLL_MULTIPLE_FACES,
    ENROLL_PERSON_LEFT,
    ENROLL_TIMEOUT,
    EnrollResult,
)
from vision.recognizer import cosine_similarity


class EnrollmentSession:
    def __init__(self, track_id: int, person_id: int, now: float,
                 samples: int = config.VISION_ENROLL_SAMPLES,
                 timeout_s: float = config.VISION_ENROLL_TIMEOUT_S,
                 gap_s: float = config.VISION_ENROLL_GAP_S,
                 lost_frames: int = config.VISION_ENROLL_LOST_FRAMES,
                 multi_frames: int = config.VISION_ENROLL_MULTI_FRAMES,
                 same_person: float = config.VISION_MATCH_THRESHOLD) -> None:
        self.track_id = track_id
        self.person_id = person_id
        self._start = now
        self._wanted = samples
        self._timeout_s = timeout_s
        self._gap_s = gap_s
        self._lost_frames = lost_frames
        self._multi_frames = multi_frames
        self._same_person = same_person
        self._embeddings: list[Any] = []
        self._last_sample = -1e9
        self._missing = 0
        self._multi = 0
        self._seen = 0
        self._rejected = 0

    @property
    def collected(self) -> int:
        return len(self._embeddings)

    def fail(self, reason: str) -> EnrollResult:
        self._embeddings = []
        return EnrollResult(self.person_id, (), reason)

    def step(self, now: float, detection: Detection | None, face_count: int,
             check: Callable[[Detection], str | None],
             embed: Callable[[Detection], Any]) -> EnrollResult | None:
        """One frame. *detection*: this track's face in the frame (None if not seen), *face_count*:
        all faces in the frame. A result when the session is over, else None."""
        self._multi = self._multi + 1 if face_count > 1 else 0
        if self._multi >= self._multi_frames:
            return self.fail(ENROLL_MULTIPLE_FACES)
        if detection is None:
            self._missing += 1
            if self._missing >= self._lost_frames:
                return self.fail(ENROLL_PERSON_LEFT)
        else:
            self._missing = 0
            if face_count <= 1:
                self._seen += 1
                if check(detection) is not None:
                    self._rejected += 1
                elif now - self._last_sample >= self._gap_s:
                    self._embeddings.append(embed(detection))
                    self._last_sample = now
        if len(self._embeddings) >= self._wanted:
            return self._finish()
        if now - self._start > self._timeout_s:
            low = self._seen > 0 and self._rejected * 2 > self._seen
            return self.fail(ENROLL_LOW_QUALITY if low else ENROLL_TIMEOUT)
        return None

    def _finish(self) -> EnrollResult:
        first = self._embeddings[0]
        if any(cosine_similarity(first, other) < self._same_person for other in self._embeddings):
            return self.fail(ENROLL_INCONSISTENT)
        embeddings, self._embeddings = tuple(self._embeddings), []
        return EnrollResult(self.person_id, embeddings)
