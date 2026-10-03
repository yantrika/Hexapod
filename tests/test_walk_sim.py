"""Step 3 checks: the gait walks the PyBullet robot (DIRECT mode, no GUI).

Each test runs 10 s of sim time after a 2 s ramp-up (see ``walk_harness``) and
prints the measured numbers (``pytest -s`` to see them).
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

import config
from body.gait import BodyVelocity
from body.sim_backend import SimBackend
from tests.walk_harness import WalkStats, run_walk

SECONDS = 10.0
MAX_SPEED = config.GAIT_MAX_SPEED_M_S
MAX_YAW_RATE = math.radians(config.TURN_RATE_MAX_DEG_S)


@pytest.fixture
def sim() -> Iterator[SimBackend]:
    backend = SimBackend(gui=False)
    yield backend
    backend.close()


def _report(name: str, stats: WalkStats) -> None:
    print(
        f"{name}: forward={stats.forward_m:+.3f} m lateral={stats.lateral_m:+.3f} m "
        f"heading={stats.heading_drift_deg:+.1f} deg roll<={stats.max_roll_deg:.2f} "
        f"pitch<={stats.max_pitch_deg:.2f} height=[{stats.min_height_m:.4f}, "
        f"{stats.max_height_m:.4f}] m stride-scaled ticks={stats.warnings}"
    )


def _assert_stable(stats: WalkStats) -> None:
    assert stats.max_roll_deg < config.WALK_TEST_MAX_TILT_DEG
    assert stats.max_pitch_deg < config.WALK_TEST_MAX_TILT_DEG
    assert stats.min_height_m > config.BODY_HEIGHT_STAND - config.WALK_TEST_HEIGHT_TOL_M
    assert stats.max_height_m < config.BODY_HEIGHT_STAND + config.WALK_TEST_HEIGHT_TOL_M
    assert stats.warnings == 0  # the planner never had to shrink the stride


def test_walk_forward(sim: SimBackend) -> None:
    stats = run_walk(sim, BodyVelocity(vx=MAX_SPEED), SECONDS)
    _report("forward", stats)
    _assert_stable(stats)
    expected = MAX_SPEED * SECONDS
    assert abs(stats.forward_m - expected) <= config.WALK_TEST_SPEED_TOL * expected
    assert abs(stats.lateral_m) < config.WALK_TEST_POSITION_DRIFT_M
    assert abs(stats.heading_drift_deg) < config.WALK_TEST_HEADING_DRIFT_DEG


def test_turn_in_place(sim: SimBackend) -> None:
    stats = run_walk(sim, BodyVelocity(yaw_rate=MAX_YAW_RATE), SECONDS)
    _report("turn", stats)
    _assert_stable(stats)
    expected_rate = math.degrees(MAX_YAW_RATE)
    assert abs(stats.yaw_rate_deg_s - expected_rate) <= config.WALK_TEST_SPEED_TOL * expected_rate
    assert stats.drift_m < config.WALK_TEST_POSITION_DRIFT_M


def test_strafe_left(sim: SimBackend) -> None:
    stats = run_walk(sim, BodyVelocity(vy=MAX_SPEED), SECONDS)
    _report("strafe", stats)
    _assert_stable(stats)
    expected = MAX_SPEED * SECONDS
    assert abs(stats.lateral_m - expected) <= config.WALK_TEST_SPEED_TOL * expected
    assert abs(stats.forward_m) < config.WALK_TEST_POSITION_DRIFT_M
    assert abs(stats.heading_drift_deg) < config.WALK_TEST_HEADING_DRIFT_DEG
