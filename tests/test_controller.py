"""Step 4 checks: the controller state machine (fake backend, fake clock, no PyBullet)."""

from __future__ import annotations

import math

import numpy as np
import pytest

import config
from body import gait, kinematics, poses
from body.controller import Controller, Result, State, rejected
from tests.fakes import FakeBackend, FakeClock

DT = 1.0 / config.CONTROL_HZ
MAX_V = config.GAIT_MAX_SPEED_M_S
MAX_W = math.radians(config.TURN_RATE_MAX_DEG_S)
RAMP_TICKS = round(config.VELOCITY_RAMP_S / DT)
BUSY = Result("busy")
ACCEPTED = Result("accepted")


def make(initial: State = State.STANDING) -> tuple[Controller, FakeBackend, FakeClock]:
    backend, clock = FakeBackend(), FakeClock()
    return Controller(backend, clock, initial_state=initial), backend, clock


def run(
    ctrl: Controller, clock: FakeClock, seconds: float, dt: float = DT, heartbeat: bool = True
) -> None:
    elapsed = 0.0
    while elapsed < seconds - 1e-9:
        step = min(dt, seconds - elapsed)  # a last short tick, so the total is exact
        if heartbeat:
            ctrl.heartbeat()
        clock.advance(step)
        ctrl.tick(step)
        elapsed += step


def run_until(ctrl: Controller, clock: FakeClock, state: State, limit_s: float = 10.0) -> None:
    for _ in range(round(limit_s / DT)):
        if ctrl.state is state:
            return
        ctrl.heartbeat()
        clock.advance(DT)
        ctrl.tick(DT)
    raise AssertionError(f"never reached {state}, stuck in {ctrl.state}")


def foot_speed(history: list[np.ndarray], dt: float = DT) -> float:
    feet = [kinematics.foot_positions_body(angles) for angles in history]
    steps = zip(feet, feet[1:], strict=False)
    return max(float(np.linalg.norm(b - a, axis=1).max()) / dt for a, b in steps)


def phase_gap(a: float, b: float) -> float:
    return min(abs(a - b) % 1.0, 1.0 - abs(a - b) % 1.0)


# --- Postures and busy/rejected rules ---------------------------------------------------------
def test_starts_standing_and_sends_the_stand_pose() -> None:
    ctrl, backend, _ = make()
    assert ctrl.state is State.STANDING
    assert backend.history[0] == pytest.approx(poses.STAND_ANGLES)


def test_stand_while_standing_is_rejected() -> None:
    ctrl, _, _ = make()
    assert ctrl.stand() == rejected("already_in_state")


def test_sit_runs_a_transition_and_finishes_with_a_done_event() -> None:
    ctrl, backend, clock = make()
    assert ctrl.sit() == ACCEPTED
    assert ctrl.state is State.SITTING_DOWN
    run_until(ctrl, clock, State.SITTING)
    run(ctrl, clock, 0.1)
    assert backend.history[-1] == pytest.approx(poses.SIT_ANGLES)
    assert [(e.kind, e.action) for e in ctrl.drain_events()] == [("done", "sit")]
    assert ctrl.sit() == rejected("already_in_state")


def test_transition_takes_the_configured_time_and_moves_smoothly() -> None:
    ctrl, backend, clock = make()
    ctrl.sit()
    run_until(ctrl, clock, State.SITTING)
    assert len(backend.history) * DT == pytest.approx(config.SIT_STAND_TRANSITION_S, abs=2 * DT)
    assert foot_speed(backend.history) < config.FOOT_TARGET_MAX_SPEED_M_S


def test_posture_actions_and_motion_return_busy_during_a_transition() -> None:
    ctrl, _, clock = make()
    ctrl.sit()
    run(ctrl, clock, 0.3)
    assert ctrl.state is State.SITTING_DOWN
    assert ctrl.stand() == BUSY
    assert ctrl.sit() == BUSY
    assert ctrl.wave() == BUSY
    assert ctrl.walk("fwd", 0.5) == BUSY
    assert ctrl.turn("left", 90) == BUSY
    assert ctrl.set_velocity(0.05, 0, 0) == BUSY
    assert ctrl.state is State.SITTING_DOWN  # none of them disturbed the transition


def test_walk_turn_and_wave_while_sitting_are_rejected_invalid_state() -> None:
    ctrl, _, _ = make(State.SITTING)
    assert ctrl.walk("fwd", 0.5) == rejected("invalid_state")
    assert ctrl.turn("left", 90) == rejected("invalid_state")
    assert ctrl.set_velocity(0.05, 0, 0) == rejected("invalid_state")
    assert ctrl.wave() == rejected("invalid_state")
    assert ctrl.state is State.SITTING


def test_stand_up_from_sitting_returns_to_the_stand_pose() -> None:
    ctrl, backend, clock = make(State.SITTING)
    assert ctrl.stand() == ACCEPTED
    assert ctrl.state is State.STANDING_UP
    run_until(ctrl, clock, State.STANDING)
    run(ctrl, clock, 0.1)
    assert backend.history[-1] == pytest.approx(poses.STAND_ANGLES, abs=1e-9)
    assert [e.action for e in ctrl.drain_events()] == ["stand"]


def test_wave_is_busy_then_returns_to_standing() -> None:
    ctrl, backend, clock = make()
    assert ctrl.wave() == ACCEPTED
    run(ctrl, clock, 1.0)
    assert ctrl.state is State.WAVING
    assert ctrl.sit() == BUSY and ctrl.walk("fwd", 1.0) == BUSY and ctrl.wave() == BUSY
    raised = max(abs(a[config.LEG_NAMES.index(config.WAVE_LEG), 1]) for a in backend.history)
    assert raised == pytest.approx(math.radians(config.WAVE_FEMUR_DEG), abs=1e-6)
    run_until(ctrl, clock, State.STANDING)
    run(ctrl, clock, 0.1)
    assert backend.history[-1] == pytest.approx(poses.STAND_ANGLES, abs=1e-9)
    assert [e.action for e in ctrl.drain_events()] == ["wave"]


# --- Ramped velocity and clamps ---------------------------------------------------------------
def test_walk_starts_moving_and_velocity_is_ramped_not_stepped() -> None:
    ctrl, _, clock = make()
    assert ctrl.walk("fwd", 1.0) == ACCEPTED
    assert ctrl.state is State.MOVING
    previous, speeds = 0.0, []
    for _ in range(RAMP_TICKS + 5):
        ctrl.heartbeat()
        clock.advance(DT)
        ctrl.tick(DT)
        speeds.append(ctrl.velocity.vx)
        assert ctrl.velocity.vx - previous <= MAX_V / config.VELOCITY_RAMP_S * DT + 1e-12
        previous = ctrl.velocity.vx
    assert speeds[0] < 0.25 * MAX_V  # does not jump
    assert speeds[-1] == pytest.approx(MAX_V)  # but gets there


def test_a_step_command_never_moves_a_foot_target_faster_than_the_limit() -> None:
    ctrl, backend, clock = make()
    ctrl.set_velocity(MAX_V, MAX_V, MAX_W)  # a hard step from standing, clamped to the stride cap
    run(ctrl, clock, 4.0)
    peak = foot_speed(backend.history)
    print(f"max foot target speed during a step command: {peak:.3f} m/s "
          f"(limit {config.FOOT_TARGET_MAX_SPEED_M_S})")
    assert peak <= config.FOOT_TARGET_MAX_SPEED_M_S


def test_walk_parameters_are_clamped_in_one_place() -> None:
    ctrl, _, _ = make()
    ctrl.walk("fwd", 50.0)
    assert ctrl.target_velocity.vx == pytest.approx(MAX_V)
    ctrl.walk("back", 0.5)
    assert ctrl.target_velocity.vx == pytest.approx(-0.5 * MAX_V)
    ctrl.set_velocity(5.0, 5.0, 50.0)
    limited = gait.limit_command(gait.BodyVelocity(5.0, 5.0, 50.0))
    assert ctrl.target_velocity == limited
    assert math.hypot(ctrl.target_velocity.vx, ctrl.target_velocity.vy) <= MAX_V + 1e-12


def test_invalid_parameters_are_rejected() -> None:
    ctrl, _, _ = make()
    assert ctrl.walk("sideways", 0.5) == rejected("invalid_params")
    assert ctrl.walk("fwd", math.nan) == rejected("invalid_params")
    assert ctrl.turn("up", 90) == rejected("invalid_params")
    assert ctrl.set_velocity(math.inf, 0, 0) == rejected("invalid_params")
    assert ctrl.state is State.STANDING


def test_latest_motion_command_wins() -> None:
    ctrl, _, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 0.2)
    ctrl.walk("back", 0.5)
    assert ctrl.target_velocity.vx == pytest.approx(-0.5 * MAX_V)
    assert ctrl.state is State.MOVING


def test_turn_finishes_by_itself_near_the_requested_angle() -> None:
    ctrl, _, clock = make()
    assert ctrl.turn("left", 45) == ACCEPTED
    turned = 0.0
    for _ in range(round(10.0 / DT)):
        if ctrl.state is not State.MOVING:
            break
        ctrl.heartbeat()
        clock.advance(DT)
        ctrl.tick(DT)
        turned += math.degrees(ctrl.velocity.yaw_rate * DT)
    assert ctrl.state is State.STANDING
    assert turned == pytest.approx(45, rel=0.1)
    assert [(e.action) for e in ctrl.drain_events()] == ["turn"]


def test_turn_angle_is_clamped() -> None:
    ctrl, _, _ = make()
    ctrl.turn("right", 9999)
    assert ctrl._turn_remaining_deg == config.TURN_ANGLE_MAX_DEG  # noqa: SLF001
    assert ctrl.target_velocity.yaw_rate < 0


def test_ramping_to_zero_ends_on_the_exact_stand_pose() -> None:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 3.0)
    ctrl.set_velocity(0, 0, 0)
    run(ctrl, clock, 2.0)
    assert ctrl.state is State.STANDING
    assert backend.history[-1] == pytest.approx(poses.STAND_ANGLES, abs=1e-9)
    assert foot_speed(backend.history) <= config.FOOT_TARGET_MAX_SPEED_M_S  # no snap at the end


# --- Timing: wall clock, not iteration count --------------------------------------------------
@pytest.mark.parametrize("tick_s", [0.005, 0.015, 0.02])
def test_gait_phase_after_2_s_does_not_depend_on_tick_length(tick_s: float) -> None:
    reference, _, ref_clock = make()
    reference.walk("fwd", 1.0)
    run(reference, ref_clock, 2.0, dt=0.005)
    ctrl, _, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.0, dt=tick_s)
    assert phase_gap(ctrl.phase, reference.phase) < 1e-9
    assert ctrl.velocity.vx == pytest.approx(reference.velocity.vx, abs=1e-9)


def test_controller_follows_the_time_the_backend_actually_advanced() -> None:
    class Stalling(FakeBackend):
        def advance(self, dt: float) -> float:  # drops everything beyond 10 ms, like a capped sim
            return super().advance(min(dt, 0.01))

    backend, clock = Stalling(), FakeClock()
    ctrl = Controller(backend, clock)
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 1.0, dt=0.1)  # ten 100 ms ticks, each only advances 10 ms
    assert ctrl.phase == pytest.approx(0.1 / config.GAIT_PERIOD_S)  # 0.1 s of simulated time


# --- Stop: hold the pose ----------------------------------------------------------------------
def test_stop_mid_walk_freezes_targets_for_10_ticks_and_keeps_the_phase() -> None:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.3)
    phase_before = ctrl.phase
    assert ctrl.stop() == ACCEPTED
    assert ctrl.state is State.HOLDING
    first = len(backend.history)
    run(ctrl, clock, 11 * DT, heartbeat=False)
    held = backend.history[first:first + 10]
    assert len(held) == 10
    for angles in held:
        assert angles == pytest.approx(held[0], abs=0.0)  # bit-identical
    assert held[0] == pytest.approx(backend.history[first - 1], abs=0.0)  # the pose it was in
    assert phase_gap(ctrl.phase, phase_before) < 1e-12
    assert ctrl.velocity == gait.BodyVelocity()


@pytest.mark.parametrize("action", ["sit", "stand", "wave"])
def test_stop_mid_transition_or_wave_freezes_the_pose(action: str) -> None:
    ctrl, backend, clock = make(State.SITTING if action == "stand" else State.STANDING)
    getattr(ctrl, action)()
    run(ctrl, clock, 0.4)
    assert ctrl.state in (State.SITTING_DOWN, State.STANDING_UP, State.WAVING)
    ctrl.stop()
    assert ctrl.state is State.HOLDING
    first = len(backend.history)
    run(ctrl, clock, 11 * DT, heartbeat=False)
    for angles in backend.history[first:first + 10]:
        assert angles == pytest.approx(backend.history[first - 1], abs=0.0)


def test_stop_is_idempotent_and_never_rejected() -> None:
    ctrl, _, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 1.0)
    assert ctrl.stop() == ACCEPTED
    assert ctrl.stop() == ACCEPTED
    assert ctrl.state is State.HOLDING


def test_stop_while_idle_leaves_the_state_alone() -> None:
    standing, _, _ = make(State.STANDING)
    sitting, _, _ = make(State.SITTING)
    assert standing.stop() == ACCEPTED and standing.state is State.STANDING
    assert sitting.stop() == ACCEPTED and sitting.state is State.SITTING


def test_walk_after_stop_resumes_from_the_held_phase_without_jumps() -> None:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.3)
    ctrl.stop()
    held_phase = ctrl.phase
    run(ctrl, clock, 0.5, heartbeat=False)
    start = len(backend.history) - 1
    assert ctrl.walk("fwd", 1.0) == ACCEPTED
    assert ctrl.state is State.MOVING
    ctrl.heartbeat()
    clock.advance(DT)
    ctrl.tick(DT)
    assert phase_gap(ctrl.phase, held_phase + DT / config.GAIT_PERIOD_S) < 1e-9  # continues
    run(ctrl, clock, 2.0)
    assert foot_speed(backend.history[start:]) <= config.FOOT_TARGET_MAX_SPEED_M_S


# --- Sit / stand from a held pose settle first ------------------------------------------------
def _hold_mid_swing() -> tuple[Controller, FakeBackend, FakeClock]:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.2)  # some feet are in the air and the stride is full
    ctrl.stop()
    assert ctrl.state is State.HOLDING
    # the held pose really is away from the neutral stance
    assert not np.allclose(backend.history[-1], poses.STAND_ANGLES, atol=0.05)
    return ctrl, backend, clock


def _states_until(
    ctrl: Controller, clock: FakeClock, end: State
) -> list[tuple[State, np.ndarray]]:
    """Run until *end*; return (state, last commanded angles) for each tick."""
    trace = []
    for _ in range(round(10.0 / DT)):
        trace.append((ctrl.state, ctrl.angles.copy()))
        if ctrl.state is end:
            return trace
        clock.advance(DT)
        ctrl.tick(DT)
    raise AssertionError("did not finish")


def test_sit_from_a_held_pose_settles_to_the_neutral_stance_first() -> None:
    ctrl, backend, clock = _hold_mid_swing()
    assert ctrl.sit() == ACCEPTED
    assert ctrl.state is State.SETTLING
    assert ctrl.walk("fwd", 1.0) == BUSY  # settling is busy
    trace = _states_until(ctrl, clock, State.SITTING)
    order = [state for i, (state, _) in enumerate(trace) if i == 0 or state != trace[i - 1][0]]
    assert order == [State.SETTLING, State.SITTING_DOWN, State.SITTING]
    first_sit_tick = next(i for i, (state, _) in enumerate(trace) if state is State.SITTING_DOWN)
    settled_pose = trace[first_sit_tick][1]  # the last pose sent before the transition starts
    for leg_index, leg in enumerate(config.LEG_NAMES):  # every foot at its neutral target
        foot = kinematics.foot_positions_body(settled_pose)[leg_index]
        assert foot == pytest.approx(kinematics.neutral_foot_body(leg), abs=1e-9)


def test_stand_from_a_held_pose_settles_then_stands() -> None:
    ctrl, backend, clock = _hold_mid_swing()
    assert ctrl.stand() == ACCEPTED
    assert ctrl.state is State.SETTLING
    trace = _states_until(ctrl, clock, State.STANDING)
    assert {state for state, _ in trace} == {State.SETTLING, State.STANDING}
    assert ctrl.angles == pytest.approx(poses.STAND_ANGLES, abs=1e-9)
    assert [e.action for e in ctrl.drain_events()][-1] == "stand"


def test_settle_is_a_smooth_move() -> None:
    ctrl, backend, clock = _hold_mid_swing()
    start = len(backend.history) - 1
    ctrl.sit()
    _states_until(ctrl, clock, State.SITTING)
    assert foot_speed(backend.history[start:]) <= config.FOOT_TARGET_MAX_SPEED_M_S


def test_wave_is_rejected_from_a_held_pose() -> None:
    ctrl, _, _ = _hold_mid_swing()
    assert ctrl.wave() == rejected("invalid_state")


def test_sit_and_stand_while_moving_stop_the_motion_first() -> None:
    ctrl, _, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 1.5)
    assert ctrl.sit() == ACCEPTED
    assert ctrl.state is State.MOVING  # still decelerating
    run_until(ctrl, clock, State.SITTING_DOWN)
    run_until(ctrl, clock, State.SITTING)
    other, _, other_clock = make()
    other.walk("fwd", 1.0)
    run(other, other_clock, 1.5)
    assert other.stand() == ACCEPTED
    run_until(other, other_clock, State.STANDING)
    assert other.velocity == gait.BodyVelocity()


# --- Watchdog ---------------------------------------------------------------------------------
def test_watchdog_ramps_to_zero_when_no_command_arrives() -> None:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 1.0)  # heartbeats keep it alive
    assert ctrl.state is State.MOVING and ctrl.velocity.vx == pytest.approx(MAX_V)
    ctrl.drain_events()
    run(ctrl, clock, config.WATCHDOG_TIMEOUT_S - 0.1, heartbeat=False)
    assert ctrl.target_velocity.vx == pytest.approx(MAX_V)  # not yet
    run(ctrl, clock, 0.3, heartbeat=False)
    assert ctrl.target_velocity == gait.BodyVelocity()  # expired: target is zero
    previous = ctrl.velocity.vx
    for _ in range(RAMP_TICKS + 5):  # and the velocity ramps down, never steps
        clock.advance(DT)
        ctrl.tick(DT)
        assert previous - ctrl.velocity.vx <= MAX_V / config.VELOCITY_RAMP_S * DT + 1e-12
        previous = ctrl.velocity.vx
    assert ctrl.velocity == gait.BodyVelocity()
    assert ctrl.state is State.STANDING
    assert backend.history[-1] == pytest.approx(poses.STAND_ANGLES, abs=1e-9)
    events = ctrl.drain_events()
    assert [(e.action, e.reason) for e in events] == [("walk", "watchdog")]


def test_heartbeats_keep_the_walk_alive() -> None:
    ctrl, _, clock = make()
    ctrl.walk("fwd", 1.0)
    for _ in range(round(10.0 / DT)):
        if _ % 10 == 0:
            ctrl.heartbeat()  # every 0.2 s
        clock.advance(DT)
        ctrl.tick(DT)
    assert ctrl.state is State.MOVING and ctrl.velocity.vx == pytest.approx(MAX_V)


def test_a_repeated_command_counts_as_a_heartbeat() -> None:
    ctrl, _, clock = make()
    for _ in range(round(5.0 / DT)):
        ctrl.walk("fwd", 1.0)
        clock.advance(DT)
        ctrl.tick(DT)
    assert ctrl.state is State.MOVING


def test_watchdog_does_not_touch_idle_or_held_states() -> None:
    for state in (State.STANDING, State.SITTING):
        ctrl, _, clock = make(state)
        run(ctrl, clock, 3.0, heartbeat=False)
        assert ctrl.state is state
    ctrl, _, clock = _hold_mid_swing()
    run(ctrl, clock, 3.0, heartbeat=False)
    assert ctrl.state is State.HOLDING


def test_every_pose_sent_to_the_backend_is_inside_the_hard_limits() -> None:
    ctrl, backend, clock = make()
    ctrl.walk("fwd", 1.0)
    run(ctrl, clock, 2.0)
    ctrl.stop()
    ctrl.sit()
    run(ctrl, clock, 4.0)
    for angles in backend.history:
        assert np.all(np.abs(angles) <= math.radians(90.0) + 1e-9)
