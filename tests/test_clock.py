"""Step 4 checks: the injectable clock and the fixed-rate loop."""

from __future__ import annotations

import time

import pytest

from body.clock import FixedRateLoop, MonotonicClock
from tests.fakes import FakeClock


def test_first_wait_returns_the_nominal_period() -> None:
    clock = FakeClock()
    loop = FixedRateLoop(50.0, clock)
    assert loop.wait() == pytest.approx(0.02)


def test_loop_returns_the_period_even_with_work_between_ticks() -> None:
    clock = FakeClock()
    loop = FixedRateLoop(50.0, clock)
    for _ in range(100):
        assert loop.wait() == pytest.approx(0.02, abs=1e-9)
        clock.advance(0.005)  # 5 ms of work per iteration: the loop sleeps the other 15 ms


def test_rate_does_not_depend_on_iteration_work() -> None:
    elapsed = {}
    for work in (0.0, 0.005, 0.015):
        clock = FakeClock()
        start = clock.now()
        loop = FixedRateLoop(50.0, clock)
        for _ in range(100):
            loop.wait()
            clock.advance(work)
        # Work is done after wait(): the loop sleeps (period - work), so ticks stay 20 ms apart.
        elapsed[work] = clock.now() - start
    assert elapsed[0.0] == pytest.approx(elapsed[0.005], abs=0.025)
    assert elapsed[0.0] == pytest.approx(elapsed[0.015], abs=0.025)
    assert elapsed[0.0] == pytest.approx(2.0, abs=0.05)


def test_late_loop_reports_the_real_dt_and_does_not_catch_up() -> None:
    clock = FakeClock()
    loop = FixedRateLoop(50.0, clock)
    loop.wait()
    clock.advance(0.2)  # a 200 ms stall
    assert loop.wait() == pytest.approx(0.2, abs=1e-9)  # real elapsed time since the last tick
    start = clock.now()
    loop.wait()  # back on schedule: no burst of catch-up ticks
    assert clock.now() - start == pytest.approx(0.02, abs=1e-9)


def test_reset_restarts_the_schedule() -> None:
    clock = FakeClock()
    loop = FixedRateLoop(50.0, clock)
    loop.wait()
    clock.advance(5.0)
    loop.reset()
    assert loop.wait() == pytest.approx(0.02)


def test_invalid_rate_is_rejected() -> None:
    with pytest.raises(ValueError):
        FixedRateLoop(0.0)


def test_monotonic_clock_moves_forward() -> None:
    clock = MonotonicClock()
    before = clock.now()
    clock.sleep(0.01)
    assert clock.now() - before >= 0.009
    clock.sleep(-1.0)  # negative sleeps are ignored, not an error
    assert time.monotonic() >= before


def test_stepper_returns_whole_nominal_ticks() -> None:
    from body.clock import FixedStepper

    stepper = FixedStepper(50.0, max_steps=3)
    assert stepper.steps(0.02) == 1
    assert stepper.steps(0.005) == 0  # not a whole tick yet
    assert stepper.steps(0.015) == 1  # 5 ms + 15 ms
    assert stepper.steps(0.04) == 2
    assert stepper.steps(0.0) == 0
    assert stepper.steps(-1.0) == 0


def test_stepper_total_ticks_follow_elapsed_time() -> None:
    from body.clock import FixedStepper

    for tick in (0.005, 0.013, 0.02, 0.045):
        stepper = FixedStepper(50.0, max_steps=3)
        total = sum(stepper.steps(tick) for _ in range(round(10.0 / tick)))
        assert total == pytest.approx(10.0 * 50.0, abs=3)


def test_stepper_drops_the_backlog_after_a_stall() -> None:
    from body.clock import FixedStepper

    stepper = FixedStepper(50.0, max_steps=3)
    assert stepper.steps(5.0) == 3  # a 5 s stall must not run 250 ticks
    assert stepper.steps(0.01) == 0  # and the backlog is gone, not queued
    with pytest.raises(ValueError):
        FixedStepper(50.0, max_steps=0)
