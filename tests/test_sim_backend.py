"""Step 2 checks: PyBullet backend in DIRECT mode (no GUI anywhere in tests)."""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np
import pytest

import config
from body import kinematics, poses
from body.sim_backend import SimBackend


@pytest.fixture
def sim() -> Iterator[SimBackend]:
    backend = SimBackend(gui=False)
    yield backend
    backend.close()


def test_foot_positions_match_kinematics_on_20_random_poses(sim: SimBackend) -> None:
    rng = np.random.default_rng(2024)
    sim.reset_base_pose([0.3, -0.2, 1.0], [0.1, -0.2, 0.4])  # non-trivial body pose
    for _ in range(20):
        angles = rng.uniform(-math.pi / 2, math.pi / 2, size=(6, 3))
        sim.reset_joint_angles(angles)
        measured = sim.foot_positions_body()
        for i, leg in enumerate(config.LEG_NAMES):
            expected = kinematics.leg_to_body(leg, kinematics.fk(*angles[i]))
            assert measured[i] == pytest.approx(expected, abs=1e-3)  # within 1 mm


def test_stands_still_after_3_seconds(sim: SimBackend) -> None:
    sim.set_joint_targets(poses.STAND_ANGLES)
    sim.run_for(3.0)
    roll, pitch = sim.tilt_deg()
    max_speed = float(np.abs(sim.get_joint_velocities()).max())
    print(
        f"stand: height={sim.body_height():.4f} m (target {config.BODY_HEIGHT_STAND}), "
        f"roll={roll:.4f} deg, pitch={pitch:.4f} deg, max joint speed={max_speed:.5f} rad/s"
    )
    assert abs(sim.body_height() - config.BODY_HEIGHT_STAND) < 0.005
    assert abs(roll) < 2.0 and abs(pitch) < 2.0
    assert max_speed < 0.05


def test_out_of_range_commands_are_clamped(sim: SimBackend) -> None:
    wild = poses.STAND_ANGLES.copy()
    wild[0, 0] = 5.0  # RF coxa far past +90 deg
    wild[1, 1] = -5.0  # RM femur far past -90 deg
    applied = sim.set_joint_targets(wild)
    assert applied[0] == pytest.approx(math.pi / 2)
    assert applied[4] == pytest.approx(-math.pi / 2)
    assert sim.joint_targets == pytest.approx(applied)
    sim.run_for(1.0)
    measured = sim.get_joint_angles()
    assert np.all(np.abs(measured) <= math.radians(90.0) + math.radians(1.0))


def test_sit_then_stand_returns_to_stand_pose(sim: SimBackend) -> None:
    sim.set_joint_targets(poses.STAND_ANGLES)
    sim.run_for(2.0)
    sim.set_joint_targets(poses.SIT_ANGLES)
    sim.run_for(2.0)
    assert abs(sim.body_height() - config.BODY_HEIGHT_SIT) < 0.01
    sim.set_joint_targets(poses.STAND_ANGLES)
    sim.run_for(3.0)
    error_deg = np.degrees(np.abs(sim.get_joint_angles() - poses.STAND_ANGLES.ravel()).max())
    roll, pitch = sim.tilt_deg()
    assert error_deg < 1.0
    assert abs(sim.body_height() - config.BODY_HEIGHT_STAND) < 0.005
    assert abs(roll) < 2.0 and abs(pitch) < 2.0


def test_advance_uses_wall_clock_time_not_call_count(sim: SimBackend) -> None:
    # 0.1 s of wall time gives the same step count however it is sliced.
    def steps_for(slices: int) -> float:
        backend = SimBackend(gui=False)
        try:
            before = backend.body_height()
            for _ in range(slices):
                backend.advance(0.05 / slices)
            return backend.body_height() - before
        finally:
            backend.close()

    assert steps_for(1) == pytest.approx(steps_for(5), abs=1e-4)


def test_advance_returns_simulated_time_and_caps_catch_up(sim: SimBackend) -> None:
    dt = 1.0 / config.PHYSICS_HZ
    assert sim.advance(3.5 * dt) == pytest.approx(3 * dt)  # whole steps only; remainder is kept
    stepped = sim.advance(5.0)  # a 5 s stall must not run 1200 steps in one call
    assert stepped == pytest.approx(config.MAX_PHYSICS_CATCHUP_STEPS * dt)
    assert sim._time_debt == 0.0  # noqa: SLF001
    sim.advance(1.0 / config.PHYSICS_HZ * 0.5)  # less than one step: nothing to run
    assert sim._time_debt > 0.0  # noqa: SLF001


def test_stand_poses_are_consistent() -> None:
    assert poses.STAND_ANGLES == pytest.approx(np.zeros((6, 3)), abs=1e-12)
    assert poses.SIT_ANGLES.shape == (6, 3)
    assert np.all(poses.SIT_ANGLES[:, 0] == 0.0)  # coxa untouched


@pytest.mark.parametrize("tick_s", [0.005, 0.015, 0.02])
def test_physics_time_follows_elapsed_wall_time_at_240_hz(sim: SimBackend, tick_s: float) -> None:
    stepped, elapsed = 0.0, 0.0
    while elapsed < 2.0 - 1e-9:
        dt = min(tick_s, 2.0 - elapsed)
        stepped += sim.advance(dt)
        elapsed += dt
    one_step = 1.01 / config.PHYSICS_HZ  # less than one step is left over
    assert stepped == pytest.approx(2.0, abs=one_step)
