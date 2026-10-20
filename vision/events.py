"""The events the vision process sends. Small and picklable; an image never appears in one."""

from __future__ import annotations

from dataclasses import dataclass

PERSON_APPEARED = "person_appeared"
PERSON_LEFT = "person_left"
FACE_BEARING = "face_bearing"


@dataclass(frozen=True)
class VisionEvent:
    kind: str
    track_id: int
    angle_deg: float | None = None  # face_bearing only: + is to the robot's left (CCW from above)
    time: float = 0.0  # time.monotonic() in the vision process (only differences mean anything)

    def __str__(self) -> str:
        if self.angle_deg is None:
            return f"{self.kind}(track {self.track_id})"
        return f"{self.kind}(track {self.track_id}, {self.angle_deg:+.1f} deg)"
