"""Test doubles: a fake clock and a fake backend (no PyBullet, no real time)."""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

import config
from body.backend import BasePose, HexapodBackend, JointArray
from body.clock import ManualClock
from voice.tts import AudioClip


class FakeClock(ManualClock):
    """A clock that only moves when told to; ``sleep`` advances it."""


class FakeBackend(HexapodBackend):
    """Records every target set and advances exactly the requested time."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[JointArray] = []  # each entry has shape (6, 3)
        self.sim_time = 0.0

    def _apply(self, angles: JointArray) -> None:
        self.history.append(angles.reshape(len(config.LEG_NAMES), 3).copy())

    def get_joint_angles(self) -> JointArray:
        return self._joint_targets.copy()

    def get_joint_velocities(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), np.zeros(3))

    def advance(self, dt: float) -> float:
        self.sim_time += dt
        return dt

    def close(self) -> None:
        pass


# --- speech doubles (Step 7): fake engine and sink driven by a fake clock ---------------------
FAKE_RATE = 1000  # Hz: clips are tiny arrays, durations come from the fake clock


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, what: str = "") -> None:
    """Real-time poll for threads to catch up with a fake-clock change."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what or predicate}")
        time.sleep(0.001)


def run_fake_time(
    clock: FakeClock, predicate: Callable[[], bool], step: float = 0.02, limit_s: float = 120.0
) -> None:
    """Advance the fake clock in *step* slices until *predicate* (a few real ms per slice)."""
    end = clock.now() + limit_s
    while not predicate():
        if clock.now() > end:
            raise AssertionError("fake time ran out before the condition held")
        clock.advance(step)
        time.sleep(0.003)


class FakeEngine:
    """A TtsEngine whose synthesis takes ``rtf * audio duration`` of FAKE time.

    ``duration_s`` maps a sentence to its audio length; ``fail_on`` sentences raise.
    """

    def __init__(
        self,
        clock: FakeClock,
        rtf: float = 0.0,
        duration_s: Callable[[str], float] = lambda text: 2.0,
        fail_on: tuple[str, ...] = (),
    ) -> None:
        self.clock = clock
        self.rtf = rtf
        self.duration_s = duration_s
        self.fail_on = fail_on
        self.calls: list[tuple[str, float, float]] = []  # (text, fake start, fake end)
        self.started: list[tuple[str, float]] = []
        self.codes: dict[str, int] = {}
        self.closed = False

    def synthesize(self, text: str) -> AudioClip:
        begin = self.clock.now()
        self.started.append((text, begin))
        duration = self.duration_s(text)
        while self.clock.now() < begin + self.rtf * duration:
            if self.closed:
                raise RuntimeError("engine closed while synthesizing")
            time.sleep(0.001)
        self.calls.append((text, begin, self.clock.now()))
        if text in self.fail_on:
            raise RuntimeError(f"synthetic failure for {text!r}")
        code = self.codes.setdefault(text, len(self.codes) + 1)
        return AudioClip(np.full(int(duration * FAKE_RATE), code, dtype=np.int16), FAKE_RATE)

    def text_of(self, code: int) -> str:
        return next(text for text, value in self.codes.items() if value == code)

    def close(self) -> None:
        self.closed = True


class FakeSink:
    """An AudioSink that "plays" a clip for its duration of FAKE time; no sound device."""

    def __init__(self, clock: FakeClock, fail_on_start: int | None = None) -> None:
        self.clock = clock
        self.fail_on_start = fail_on_start  # fail the Nth start() (1-based)
        self.plays: list[dict[str, float]] = []  # code, start, end (None while playing), aborted
        self._aborted = False
        self._end = 0.0
        self.opened = False
        self.closed = False
        self.abort_calls = 0
        self.starts = 0

    def open(self) -> None:
        self.opened = True

    def start(self, clip: AudioClip) -> None:
        self.starts += 1
        if self.fail_on_start == self.starts:
            raise RuntimeError("synthetic audio device failure")
        self._aborted = False
        self._end = self.clock.now() + clip.duration_s
        self.plays.append(
            {"code": float(clip.samples[0]), "start": self.clock.now(), "end": -1.0, "aborted": 0.0}
        )

    def wait(self) -> None:
        while not self._aborted and self.clock.now() < self._end:
            time.sleep(0.001)
        self.plays[-1]["end"] = self.clock.now()
        self.plays[-1]["aborted"] = float(self._aborted)

    def abort(self) -> None:
        self.abort_calls += 1
        self._aborted = True

    def close(self) -> None:
        self.closed = True
