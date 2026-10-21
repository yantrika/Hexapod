"""The brain's end of the vision process: start it, read its events, tell it who is known.

One thread reads ``VisionEvent`` / ``EnrollResult`` from the process, hands them to the behaviour
layer and the enrolment flow, ticks both about ten times a second, and mirrors "the LLM is busy" to
the process (it drops to its low frame rate while a reply is generated). The brain owns the
database; the vision process only ever gets embeddings by command (``SetKnownFaces``) and sends
back person ids and an ``EnrollResult``. A dead or missing vision process is logged once and the
rest of the robot carries on: vision is never needed to talk or to move.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

import config
from bridge import Status
from vision.events import EnrollResult, Forget, SetKnownFaces, VisionEvent
from vision.recognizer import embedding_from_bytes

logger = logging.getLogger(__name__)


class VisionHandle(Protocol):
    """``vision.process.VisionProcess``, or a fake in the tests."""

    pause_event: Any

    def start(self) -> None: ...
    def stop(self, timeout: float = 5.0) -> None: ...
    @property
    def is_alive(self) -> bool: ...
    def send(self, command: Any) -> bool: ...
    def get_event(self, timeout: float = 0.0) -> VisionEvent | EnrollResult | None: ...


class Consumer(Protocol):
    def on_event(self, event: VisionEvent) -> None: ...
    def tick(self) -> None: ...


class ResultSink(Protocol):
    def on_enroll_result(self, result: EnrollResult) -> None: ...
    def tick(self) -> None: ...


class VisionLink:
    def __init__(
        self,
        process: VisionHandle,
        behaviour: Consumer,
        flow: ResultSink,
        known_faces: Callable[[], dict[int, list[bytes]]],
        llm_busy: Callable[[], bool] = lambda: False,
        statuses: Callable[[], list[Status]] = lambda: [],
        on_status: Callable[[Status], None] = lambda status: None,
    ) -> None:
        self._process = process
        self._behaviour = behaviour
        self._flow = flow
        self._known_faces = known_faces  # Memory.all_embeddings (consenting people only)
        self._llm_busy = llm_busy
        self._statuses = statuses
        self._on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._was_alive = True
        self.events_seen = 0

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._process.start()
        self.sync_known_faces()
        self._thread = threading.Thread(target=self._run, name="vision-link", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(2.0)
        self._process.stop()

    # -- to vision -----------------------------------------------------------------------------
    def sync_known_faces(self) -> bool:
        """Tell vision everyone who may be recognised now (after an enrolment or a forget)."""
        faces = tuple(
            (person_id, tuple(embedding_from_bytes(blob) for blob in blobs))
            for person_id, blobs in self._known_faces().items())
        sent = self._process.send(SetKnownFaces(faces))
        if not sent:
            logger.warning("vision: could not send the known faces (its queue is full)")
        return sent

    def forget(self, person_id: int) -> None:
        """Drop a person from vision at once (the brain has deleted them from the database)."""
        self._process.send(Forget(person_id))

    def send(self, command: Any) -> bool:
        return self._process.send(command)

    # -- the thread ----------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step(config.VISION_LINK_POLL_S)
            except Exception:  # noqa: BLE001 - perception must never take the robot down
                logger.exception("vision link failed; carrying on")
                self._stop.wait(config.VISION_LINK_POLL_S)

    def step(self, wait_s: float = 0.0) -> None:
        """One round: read events, mirror the LLM load, tick. (Public so tests can drive it.)"""
        self._check_alive()
        event = self._process.get_event(wait_s)
        while event is not None:
            self.events_seen += 1
            if isinstance(event, EnrollResult):
                self._flow.on_enroll_result(event)
            else:
                self._behaviour.on_event(event)
            event = self._process.get_event(0.0)
        for status in self._statuses():
            self._on_status(status)
        if self._llm_busy():
            self._process.pause_event.set()
        else:
            self._process.pause_event.clear()
        self._behaviour.tick()
        self._flow.tick()

    def _check_alive(self) -> None:
        alive = self._process.is_alive
        if self._was_alive and not alive:
            logger.warning("vision: the camera process ended (no camera, or a model is missing); "
                           "the robot goes on without it")
        self._was_alive = alive
