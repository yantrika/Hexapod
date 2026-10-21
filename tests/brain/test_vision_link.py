"""The link between the vision process and the brain: events in, known faces out, pause mirrored."""

from __future__ import annotations

from typing import Any

import numpy as np

from brain.vision_link import VisionLink
from bridge import Status
from tests.brain.test_app_vision import FakeVisionProcess
from vision.events import (
    PERSON_APPEARED,
    EnrollResult,
    Forget,
    SetKnownFaces,
    VisionEvent,
)
from vision.recognizer import embedding_to_bytes


class Sink:
    def __init__(self) -> None:
        self.events: list[VisionEvent] = []
        self.results: list[EnrollResult] = []
        self.ticks = 0

    def on_event(self, event: VisionEvent) -> None:
        self.events.append(event)

    def on_enroll_result(self, result: EnrollResult) -> None:
        self.results.append(result)

    def tick(self) -> None:
        self.ticks += 1


def make(process: FakeVisionProcess, sink: Sink, busy: Any = lambda: False,
         faces: Any = dict, statuses: Any = lambda: [], on_status: Any = lambda s: None
         ) -> VisionLink:
    return VisionLink(process, sink, sink, faces, busy, statuses, on_status)


def test_events_and_results_are_routed_and_both_sides_tick() -> None:
    process, sink = FakeVisionProcess(), Sink()
    link = make(process, sink)
    process.events.put(VisionEvent(PERSON_APPEARED, 1))
    process.events.put(EnrollResult(4, (np.zeros(128, dtype=np.float32),)))
    link.step()
    assert [e.kind for e in sink.events] == [PERSON_APPEARED]
    assert [r.person_id for r in sink.results] == [4]
    assert sink.ticks == 2 and link.events_seen == 2  # behaviour.tick and flow.tick


def test_known_faces_are_sent_as_vectors_for_consenting_people() -> None:
    process, sink = FakeVisionProcess(), Sink()
    vector = np.arange(128, dtype=np.float32)
    link = make(process, sink, faces=lambda: {3: [embedding_to_bytes(vector)]})
    assert link.sync_known_faces()
    command = process.sent[-1]
    assert isinstance(command, SetKnownFaces)
    (person_id, vectors), = command.faces
    assert person_id == 3 and np.array_equal(vectors[0], vector)
    assert "array" not in repr(command)  # an embedding never reaches a log by accident
    link.forget(3)
    assert process.sent[-1] == Forget(3)


def test_the_pause_flag_follows_the_llm() -> None:
    process, sink = FakeVisionProcess(), Sink()
    busy = [False]
    link = make(process, sink, busy=lambda: busy[0])
    link.step()
    assert not process.pause_event.is_set()
    busy[0] = True
    link.step()
    assert process.pause_event.is_set()
    busy[0] = False
    link.step()
    assert not process.pause_event.is_set()


def test_statuses_are_passed_on_and_a_dead_process_is_logged_once(
        caplog: Any) -> None:
    process, sink = FakeVisionProcess(), Sink()
    seen: list[Status] = []
    status = Status("done", 1, {"action": "stand"}, 1, 0.0)
    link = make(process, sink, statuses=lambda: [status], on_status=seen.append)
    process.started = False  # never started: not alive
    with caplog.at_level("WARNING"):
        link.step()
        link.step()
    assert seen == [status, status]
    assert sum("camera process ended" in r.message for r in caplog.records) == 1
