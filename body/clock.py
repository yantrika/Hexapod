"""Injectable wall clock and a fixed-rate loop timer.

The body process paces its control tick against the wall clock, not against a
loop iteration count, so gait speed does not depend on how long one iteration
takes. Tests inject a fake clock (``tests/fakes.py``) so no real time passes.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Monotonic seconds plus a sleep that advances the same clock."""

    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class MonotonicClock:
    """The real clock."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class ManualClock:
    """A clock that only moves when told to (``sleep`` advances it).

    Used by tests and by headless runs that simulate wall time as fast as possible.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def now(self) -> float:
        return self.time

    def sleep(self, seconds: float) -> None:
        self.time += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.time += seconds


class FixedRateLoop:
    """Paces a loop at ``hz`` against a clock and reports the real time per tick.

    ``wait()`` sleeps until the next deadline and returns the wall seconds since
    the previous ``wait()`` returned (the nominal period on the first call). If
    the loop runs late it does not try to catch up: the deadline restarts from
    now, and the returned ``dt`` is simply larger, so callers that integrate
    ``dt`` stay correct under load.
    """

    def __init__(self, hz: float, clock: Clock | None = None) -> None:
        if hz <= 0:
            raise ValueError("hz must be positive")
        self.period = 1.0 / hz
        self._clock: Clock = clock if clock is not None else MonotonicClock()
        self._deadline: float | None = None
        self._last: float | None = None

    def reset(self) -> None:
        self._deadline = None
        self._last = None

    def wait(self) -> float:
        now = self._clock.now()
        if self._deadline is None:
            self._deadline = now
            self._last = now
        self._deadline += self.period
        if now < self._deadline:
            self._clock.sleep(self._deadline - now)
        else:
            self._deadline = now  # late: restart the schedule instead of catching up
        after = self._clock.now()
        assert self._last is not None
        dt = after - self._last
        self._last = after
        return dt


class FixedStepper:
    """Turns variable wall-clock ``dt`` into whole fixed control ticks.

    The controller is only validated for small tick lengths (at 50 ms and above
    the walk degrades), so a loop that runs late should run several nominal
    ticks, not one long one. ``steps(dt)`` returns how many ticks of
    ``self.period`` are due; at most ``max_steps`` (the rest is dropped, so a slow
    machine runs the simulation in slow motion instead of bursting).
    """

    def __init__(self, hz: float, max_steps: int = 3) -> None:
        if hz <= 0 or max_steps < 1:
            raise ValueError("hz must be positive and max_steps at least 1")
        self.period = 1.0 / hz
        self.max_steps = max_steps
        self._debt = 0.0

    def steps(self, dt: float) -> int:
        self._debt += max(0.0, dt)
        due = int(self._debt / self.period + 1e-9)
        if due > self.max_steps:
            due = self.max_steps
            self._debt = 0.0
        else:
            self._debt -= due * self.period
        return due
