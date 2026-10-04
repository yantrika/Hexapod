"""Step 4 checks: the controller driving the PyBullet robot (DIRECT mode, fake clock).

Prints the measured numbers (``pytest -s``).
"""

from __future__ import annotations

import functools
import math
from collections.abc import Iterator

import numpy as np
import pytest

pytest.importorskip("pybullet")  # the simulator is not installed on the Pi

import config  # noqa: E402
from body import poses  # noqa: E402
from body.controller import Controller, State  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402
from tests.fakes import FakeClock  # noqa: E402

DT = 1.0 / config.CONTROL_HZ
MAX_V = config.GAIT_MAX_SPEED_M_S
HOLD_S = 2.5  # how long a held or stopped pose is observed


@pytest.fixture
def rig() -> Iterator[tuple[SimBackend, Controller, FakeClock]]:
    sim, clock = SimBackend(gui=False), FakeClock()
    yield sim, Controller(sim, clock), clock
    sim.close()


def run(ctrl: Controller, clock: FakeClock, seconds: float, dt: float = DT,
        heartbeat: bool = True) -> None:
    elapsed = 0.0
    while elapsed < seconds - 1e-9:
        step = min(dt, seconds - elapsed)
        if heartbeat:
            ctrl.heartbeat()
        clock.advance(step)
        ctrl.tick(step)
        elapsed += step


def run_until(ctrl: Controller, clock: FakeClock, state: State, limit_s: float = 10.0) -> None:
    for _ in range(round(limit_s / DT)):
        if ctrl.state is state:
            return
        run(ctrl, clock, DT)
    raise AssertionError(f"never reached {state}, stuck in {ctrl.state}")


class Measure:
    """Tracks body tilt and height over a stretch of simulation."""

    def __init__(self, sim: SimBackend) -> None:
        self.sim = sim
        self.tilt = 0.0
        self.low = math.inf
        self.high = -math.inf

    def sample(self) -> None:
        roll, pitch = self.sim.tilt_deg()
        self.tilt = max(self.tilt, abs(roll), abs(pitch))
        self.low = min(self.low, self.sim.body_height())
        self.high = max(self.high, self.sim.body_height())


def run_measured(ctrl: Controller, clock: FakeClock, sim: SimBackend, seconds: float,
                 heartbeat: bool = True) -> Measure:
    measure = Measure(sim)
    for _ in range(round(seconds / DT)):
        run(ctrl, clock, DT, heartbeat=heartbeat)
        measure.sample()
    return measure


def body_speed(sim: SimBackend) -> float:
    pb = sim.client
    linear, _ = pb.getBaseVelocity(sim._robot)  # noqa: SLF001
    return float(np.linalg.norm(linear))


def crossed(before: float, after: float, target: float) -> bool:
    """Did the phase pass *target* going from *before* to *after* (allowing a wrap)?"""
    if after >= before:
        return before < target <= after
    return target > before or target <= after


def advance_to_phase(ctrl: Controller, clock: FakeClock, target: float) -> None:
    for _ in range(round(3.0 / DT)):
        before = ctrl.phase
        run(ctrl, clock, DT)
        if crossed(before, ctrl.phase, target):
            return
    raise AssertionError("phase never reached")


# --- Held-pose stability ----------------------------------------------------------------------
@pytest.mark.parametrize("stop_phase", [0.05, 0.3, 0.55, 0.8])
def test_held_pose_after_stop_is_stable(
    rig: tuple[SimBackend, Controller, FakeClock], stop_phase: float
) -> None:
    sim, ctrl, clock = rig
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.5)  # ramped up and walking at full speed
    advance_to_phase(ctrl, clock, stop_phase)
    airborne = float((sim.foot_positions_body()[:, 2] > -config.BODY_HEIGHT_STAND + 0.005).sum())
    ctrl.stop()
    assert ctrl.state is State.HOLDING
    held = ctrl.angles.copy()
    measure = run_measured(ctrl, clock, sim, HOLD_S, heartbeat=False)
    speed = body_speed(sim)
    print(
        f"hold at phase {stop_phase:.2f} ({airborne:.0f} feet in the air): "
        f"max tilt {measure.tilt:.2f} deg, height [{measure.low:.4f}, {measure.high:.4f}] m, "
        f"body speed after {HOLD_S:.0f} s {speed:.4f} m/s"
    )
    assert ctrl.angles == pytest.approx(held, abs=0.0)  # joint targets never moved
    assert measure.tilt < config.HOLD_TEST_MAX_TILT_DEG
    assert measure.low > config.BODY_HEIGHT_STAND - config.WALK_TEST_HEIGHT_TOL_M
    assert measure.high < config.BODY_HEIGHT_STAND + config.WALK_TEST_HEIGHT_TOL_M
    assert speed < config.HOLD_TEST_MAX_BODY_SPEED_M_S


def test_watchdog_expiry_reaches_zero_and_holds_a_stable_stand(
    rig: tuple[SimBackend, Controller, FakeClock],
) -> None:
    sim, ctrl, clock = rig
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.5)
    assert ctrl.velocity.vx == pytest.approx(MAX_V)
    run(ctrl, clock, config.WATCHDOG_TIMEOUT_S + config.VELOCITY_RAMP_S + 0.5, heartbeat=False)
    assert ctrl.velocity.vx == 0.0 and ctrl.state is State.STANDING
    measure = run_measured(ctrl, clock, sim, HOLD_S, heartbeat=False)
    speed = body_speed(sim)
    error = np.degrees(np.abs(sim.get_joint_angles() - poses.STAND_ANGLES.ravel()).max())
    print(
        f"after watchdog: max tilt {measure.tilt:.2f} deg, height [{measure.low:.4f}, "
        f"{measure.high:.4f}] m, body speed {speed:.4f} m/s, joints {error:.2f} deg from stand"
    )
    assert measure.tilt < config.HOLD_TEST_MAX_TILT_DEG
    assert abs(measure.low - config.BODY_HEIGHT_STAND) < 0.005
    assert abs(measure.high - config.BODY_HEIGHT_STAND) < 0.005
    assert speed < config.HOLD_TEST_MAX_BODY_SPEED_M_S
    assert error < 1.0


# --- Timing -----------------------------------------------------------------------------------
@functools.cache
def walk_distance(dt: float) -> float:
    """Distance walked in 4 s (after a 1.5 s ramp-up) with controller ticks of *dt* seconds."""
    sim, clock = SimBackend(gui=False), FakeClock()
    try:
        ctrl = Controller(sim, clock)
        ctrl.walk("fwd", 1.0)
        run(ctrl, clock, 1.5, dt=dt)
        start = sim.get_base_pose().position[0]
        run(ctrl, clock, 4.0, dt=dt)
        return float(sim.get_base_pose().position[0] - start)
    finally:
        sim.close()


@pytest.mark.parametrize("tick_s", [0.005, 0.01])
def test_distance_walked_does_not_depend_on_tick_length(tick_s: float) -> None:
    reference, other = walk_distance(DT), walk_distance(tick_s)
    print(
        f"distance in 4 s: {reference:.3f} m at 20 ms ticks, "
        f"{other:.3f} m at {tick_s * 1000:.0f} ms"
    )
    assert other == pytest.approx(reference, rel=0.10)
    assert reference == pytest.approx(MAX_V * 4.0, rel=config.WALK_TEST_SPEED_TOL)


# --- Scripted sequences -----------------------------------------------------------------------
def test_scripted_stand_walk_stop_sit_stand(rig: tuple[SimBackend, Controller, FakeClock]) -> None:
    sim, ctrl, clock = rig
    run(ctrl, clock, 1.0)
    assert ctrl.walk("fwd", 1.0).ok
    measure = run_measured(ctrl, clock, sim, 3.0)
    start_x = sim.get_base_pose().position[0]
    assert start_x > 0.05
    assert ctrl.stop().ok
    run(ctrl, clock, 1.0, heartbeat=False)
    assert ctrl.sit().ok
    run_until(ctrl, clock, State.SITTING)
    run(ctrl, clock, 1.5)
    sit_height = sim.body_height()
    assert abs(sit_height - config.BODY_HEIGHT_SIT) < 0.01
    assert ctrl.stand().ok
    run_until(ctrl, clock, State.STANDING)
    stand = run_measured(ctrl, clock, sim, 2.0)
    print(
        f"sequence: walked {start_x:.2f} m, sit height {sit_height:.4f} m, final height "
        f"[{stand.low:.4f}, {stand.high:.4f}] m, max tilt {max(measure.tilt, stand.tilt):.2f} deg"
    )
    assert max(measure.tilt, stand.tilt) < config.WALK_TEST_MAX_TILT_DEG
    assert abs(stand.high - config.BODY_HEIGHT_STAND) < 0.005
    assert abs(stand.low - config.BODY_HEIGHT_STAND) < 0.005


def test_wave_keeps_the_robot_upright(rig: tuple[SimBackend, Controller, FakeClock]) -> None:
    sim, ctrl, clock = rig
    run(ctrl, clock, 1.0)
    assert ctrl.wave().ok
    measure = run_measured(ctrl, clock, sim, config.WAVE_DURATION_S + 0.5)
    print(
        f"wave: max tilt {measure.tilt:.2f} deg, "
        f"height [{measure.low:.4f}, {measure.high:.4f}] m"
    )
    assert ctrl.state is State.STANDING
    assert measure.tilt < config.WALK_TEST_MAX_TILT_DEG
    assert measure.low > config.BODY_HEIGHT_STAND - config.WALK_TEST_HEIGHT_TOL_M


def test_sit_from_a_held_pose_ends_sitting_level(
    rig: tuple[SimBackend, Controller, FakeClock],
) -> None:
    sim, ctrl, clock = rig
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.3)
    ctrl.stop()
    assert ctrl.sit().ok
    measure = run_measured(ctrl, clock, sim, config.SETTLE_S + config.SIT_STAND_TRANSITION_S + 2.0)
    assert ctrl.state is State.SITTING
    assert measure.tilt < config.WALK_TEST_MAX_TILT_DEG
    assert abs(sim.body_height() - config.BODY_HEIGHT_SIT) < 0.01
