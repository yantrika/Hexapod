"""Fakes for the vision tests: a clock that advances only when waited on, a scripted camera and a
scripted detector. No webcam, no OpenCV and no model is needed."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from vision.camera import CameraError
from vision.detector import Box, Detection
from vision.events import FACE_BEARING, VisionEvent
from vision.process import VisionLoop, VisionOptions
from vision.stats import VisionStats
from vision.tracker import IouTracker


class FakeClock:
    """``wait`` advances time instead of sleeping and reports whether stop is set."""

    def __init__(self) -> None:
        self.now = 0.0
        self.stop = threading.Event()
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        return self.stop.is_set()


class FakeCamera:
    """``script(frame_index, clock_now)`` returns a frame or raises CameraError; ``open_failures``
    counts how many ``open`` calls fail first. Sets the stop event once ``stop_at`` seconds pass."""

    def __init__(self, clock: FakeClock, stop_at: float,
                 script: Callable[[int, float], Any] | None = None,
                 open_failures: int = 0) -> None:
        self.clock = clock
        self.stop_at = stop_at
        self.script = script
        self.open_failures_left = open_failures
        self.opened = False
        self.opens = 0
        self.releases = 0
        self.reads = 0

    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opens += 1
        if self.open_failures_left > 0:
            self.open_failures_left -= 1
            raise CameraError("fake camera cannot open")
        self.opened = True

    def read(self) -> Any:
        if self.clock.now >= self.stop_at:
            self.clock.stop.set()
        index = self.reads
        self.reads += 1
        if self.script is not None:
            return self.script(index, self.clock.now)
        return np.zeros((240, 320, 3), dtype=np.uint8)

    def release(self) -> None:
        self.opened = False
        self.releases += 1


class FakeDetector:
    """``boxes(frame_index)`` gives the face boxes of that frame (the loop reads each once)."""

    def __init__(self, boxes: Callable[[int], list[Box]]) -> None:
        self._boxes = boxes
        self.calls = 0

    def resize(self, frame: Any) -> Any:
        return frame

    def detect(self, small_frame: Any) -> list[Detection]:
        index = self.calls
        self.calls += 1
        return [Detection(box=box, score=0.9) for box in self._boxes(index)]


FACE: Box = (140.0, 60.0, 40.0, 40.0)
OPTIONS = VisionOptions(fps=4.0, paused_fps=1.0, bearing_max_hz=2.0, paused_bearing_hz=0.5,
                        retry_backoff_s=(1.0, 4.0), stats_s=5.0)


def run_loop(camera: FakeCamera, clock: FakeClock, detector: FakeDetector,
             pause: bool = False, stats: list[VisionStats] | None = None) -> list[VisionEvent]:
    events: list[VisionEvent] = []
    pause_event = threading.Event()
    if pause:
        pause_event.set()
    loop = VisionLoop(camera, detector, IouTracker(appear_frames=2, lost_frames=3),
                      emit=events.append, options=OPTIONS, stop_event=clock.stop,
                      pause_event=pause_event, clock=clock, wait=clock.wait,
                      stats_sink=stats.append if stats is not None else None,
                      meter=lambda: (clock.now * 0.25, 40.0))
    loop.run()
    return events


def bearings(events: list[VisionEvent]) -> list[VisionEvent]:
    return [e for e in events if e.kind == FACE_BEARING]
