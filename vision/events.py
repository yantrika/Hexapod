"""What the vision process sends and receives. Small and picklable; an image never appears in one,
and an embedding never appears in a repr (so it cannot reach a log by accident)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PERSON_APPEARED = "person_appeared"
PERSON_LEFT = "person_left"
FACE_BEARING = "face_bearing"
PERSON_IDENTIFIED = "person_identified"
PERSON_UNKNOWN = "person_unknown"

# EnrollResult.reason when enrolment fails
ENROLL_TIMEOUT = "timeout"
ENROLL_PERSON_LEFT = "person_left"
ENROLL_MULTIPLE_FACES = "multiple_faces"
ENROLL_LOW_QUALITY = "low_quality"
ENROLL_INCONSISTENT = "inconsistent"  # the samples do not look like one person
ENROLL_CANCELLED = "cancelled"
ENROLL_BUSY = "busy"
ENROLL_UNKNOWN_TRACK = "unknown_track"
ENROLL_NO_RECOGNIZER = "no_recognizer"


@dataclass(frozen=True)
class VisionEvent:
    kind: str
    track_id: int
    angle_deg: float | None = None  # face_bearing only: + is to the robot's left (CCW from above)
    time: float = 0.0  # time.monotonic() in the vision process (only differences mean anything)
    person_id: int | None = None  # person_identified only: the id the brain gave the person
    score: float | None = None  # person_identified only: cosine score of the agreeing matches

    def __str__(self) -> str:
        if self.person_id is not None:
            return (f"{self.kind}(track {self.track_id}, person {self.person_id}, "
                    f"score {self.score or 0.0:.2f})")
        if self.angle_deg is None:
            return f"{self.kind}(track {self.track_id})"
        return f"{self.kind}(track {self.track_id}, {self.angle_deg:+.1f} deg)"


# -- brain -> vision commands (the brain owns the database; vision never opens it) ---------------


@dataclass(frozen=True, eq=False)
class SetKnownFaces:
    """Replace everything vision knows: ``faces`` is ((person_id, (embedding, ...)), ...)."""

    faces: tuple[tuple[int, tuple[Any, ...]], ...] = field(repr=False)


@dataclass(frozen=True)
class Enroll:
    """Collect face samples of the person on *track_id* for *person_id*. The brain sends this only
    after the person said yes."""

    track_id: int
    person_id: int


@dataclass(frozen=True)
class CancelEnroll:
    pass


@dataclass(frozen=True)
class Forget:
    """Drop one person's embeddings from vision's memory (the brain deletes them from the DB)."""

    person_id: int


# -- vision -> brain ----------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class EnrollResult:
    """Success: ``embeddings`` (float32 vectors) and no reason. Failure: ``reason`` and none."""

    person_id: int
    embeddings: tuple[Any, ...] = field(default=(), repr=False)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None and bool(self.embeddings)

    def __str__(self) -> str:
        if self.ok:
            return f"enroll_result(person {self.person_id}, {len(self.embeddings)} samples)"
        return f"enroll_result(person {self.person_id}, failed: {self.reason})"
