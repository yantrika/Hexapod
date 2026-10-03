"""Step 3 checks: the pure tripod gait planner (no PyBullet)."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pytest

import config
from body import gait, kinematics
from body.gait import BodyVelocity, GaitParams

PARAMS = gait.DEFAULT_PARAMS
GROUND = -PARAMS.body_height_m
PHASES = np.linspace(0.0, 1.0, 400, endpoint=False)  # cheap geometry checks
IK_PHASES = PHASES[::8]  # 50 phases for checks that run IK
MAX_V = PARAMS.max_speed_m_s
MAX_W = PARAMS.max_yaw_rate_rad_s
COMMANDS = {
    "forward": BodyVelocity(MAX_V, 0, 0),
    "backward": BodyVelocity(-MAX_V, 0, 0),
    "strafe_left": BodyVelocity(0, MAX_V, 0),
    "strafe_right": BodyVelocity(0, -MAX_V, 0),
    "turn_left": BodyVelocity(0, 0, MAX_W),
    "turn_right": BodyVelocity(0, 0, -MAX_W),
    "forward_and_turn": BodyVelocity(MAX_V, 0, MAX_W),
    "diagonal": BodyVelocity(MAX_V, MAX_V, -MAX_W),
}
LEG_INDEX = {leg: i for i, leg in enumerate(config.LEG_NAMES)}
MIRROR_LEG = {"RF": "LF", "RM": "LM", "RR": "LR", "LR": "RR", "LM": "RM", "LF": "RF"}


def test_stance_feet_never_above_ground_and_swing_feet_lift() -> None:
    peak = 0.0
    for phase in PHASES:
        targets = gait.foot_targets(phase, COMMANDS["forward"])
        for leg in config.LEG_NAMES:
            z = targets[LEG_INDEX[leg], 2]
            if gait.is_swing(leg, phase):
                assert z >= GROUND - 1e-12
                peak = max(peak, z - GROUND)
            else:
                assert z == pytest.approx(GROUND, abs=1e-12)
    assert peak == pytest.approx(PARAMS.swing_height_m, abs=1e-4)


def test_groups_are_never_in_swing_together() -> None:
    for phase in PHASES:
        a_swing = [gait.is_swing(leg, phase) for leg in config.TRIPOD_A]
        b_swing = [gait.is_swing(leg, phase) for leg in config.TRIPOD_B]
        assert len(set(a_swing)) == 1 and len(set(b_swing)) == 1  # a group moves together
        assert not (a_swing[0] and b_swing[0])
    swung_a = sum(gait.is_swing("RF", p) for p in PHASES) / len(PHASES)
    assert swung_a == pytest.approx(PARAMS.swing_fraction, abs=0.01)


def test_groups_come_from_config() -> None:
    for leg in config.TRIPOD_A:
        assert gait.group_phase(leg, 0.2) == pytest.approx(0.2)
    for leg in config.TRIPOD_B:
        assert gait.group_phase(leg, 0.2) == pytest.approx(0.7)


def test_zero_velocity_keeps_every_foot_at_neutral() -> None:
    for phase in PHASES:
        targets = gait.foot_targets(phase, BodyVelocity())
        for leg in config.LEG_NAMES:
            expected = kinematics.neutral_foot_body(leg)
            assert targets[LEG_INDEX[leg]] == pytest.approx(expected, abs=1e-12)


def test_stance_foot_moves_backward_in_a_straight_line_at_ground_height() -> None:
    v = 0.06
    stance_time = PARAMS.stance_time_s
    leg = "RM"  # tripod B: stance begins at phase 0 (group phase 0.5)
    start = gait.foot_targets(0.0, BodyVelocity(vx=v))[LEG_INDEX[leg]]
    end = gait.foot_targets(0.5 - 1e-9, BodyVelocity(vx=v))[LEG_INDEX[leg]]
    assert start[0] - end[0] == pytest.approx(v * stance_time, abs=1e-6)  # moves back by v*T_stance
    path = np.array([gait.foot_targets(p, BodyVelocity(vx=v))[LEG_INDEX[leg]] for p in
                     np.linspace(0.0, 0.5 - 1e-9, 50)])
    assert np.allclose(path[:, 1], path[0, 1])  # straight (no sideways drift)
    assert np.allclose(path[:, 2], GROUND)  # at ground height
    assert np.all(np.diff(path[:, 0]) < 0)  # monotonically backward


def test_turn_moves_feet_tangentially_about_the_body_centre() -> None:
    w = MAX_W * 0.5
    targets_a = gait.foot_targets(0.0, BodyVelocity(yaw_rate=w))
    targets_b = gait.foot_targets(0.25, BodyVelocity(yaw_rate=w))
    for leg in config.LEG_NAMES:
        i = LEG_INDEX[leg]
        move = targets_b[i, :2] - targets_a[i, :2]
        radial = kinematics.neutral_foot_body(leg)[:2]
        if np.linalg.norm(move) > 1e-9:  # compare with the tangential direction (ccw turn)
            tangent = np.array([-radial[1], radial[0]])
            cos = move @ tangent / (np.linalg.norm(move) * np.linalg.norm(tangent))
            assert abs(cos) > 0.95


def test_swing_velocity_is_zero_at_lift_off_and_touch_down() -> None:
    cmd = COMMANDS["forward"]
    leg = "RF"  # tripod A: swing in group phase [0, beta)
    i = LEG_INDEX[leg]
    eps = 1e-4
    peak_speed = max(
        np.linalg.norm(gait.foot_targets(p + eps, cmd)[i] - gait.foot_targets(p, cmd)[i]) / eps
        for p in np.linspace(0.01, PARAMS.swing_fraction - 0.01, 50)
    )
    for p in (0.0, PARAMS.swing_fraction - eps):
        step = gait.foot_targets(p + eps, cmd)[i] - gait.foot_targets(p, cmd)[i]
        edge = np.linalg.norm(step) / eps
        assert edge < 0.01 * peak_speed


@pytest.mark.parametrize("name", ["strafe_left", "strafe_right", "forward", "turn_left"])
def test_mirrored_command_gives_mirrored_targets(name: str) -> None:
    # Mirror pairs (RF/LF, RM/LM, RR/LR) sit in opposite tripod groups, so the
    # mirror image of the targets at phase p is the plan at p + 0.5 for the
    # mirrored command (vx, -vy, -yaw_rate), with left and right legs swapped.
    cmd = COMMANDS[name]
    mirrored_cmd = BodyVelocity(cmd.vx, -cmd.vy, -cmd.yaw_rate)
    for phase in PHASES:
        original = gait.foot_targets(phase, cmd)
        other = gait.foot_targets((phase + 0.5) % 1.0, mirrored_cmd)
        for leg in config.LEG_NAMES:
            mirrored_target = original[LEG_INDEX[leg]] * np.array([1.0, -1.0, 1.0])
            assert other[LEG_INDEX[MIRROR_LEG[leg]]] == pytest.approx(mirrored_target, abs=1e-9)


@pytest.mark.parametrize("name", list(COMMANDS))
def test_all_targets_reachable_at_max_speed(name: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        for phase in IK_PHASES:
            step = gait.plan(phase, COMMANDS[name])
            assert step.stride_scale == 1.0
            for i, leg in enumerate(config.LEG_NAMES):
                local = kinematics.body_to_leg(leg, step.foot_targets[i])
                angles = kinematics.ik(local)
                assert angles is not None
                assert angles == pytest.approx(step.joint_angles[i], abs=1e-9)
    assert not caplog.records  # no warning at max speed
    coxa = np.degrees(np.abs(step.joint_angles[:, 0]))
    assert coxa.max() <= config.GAIT_SOFT_LIMITS_DEG["coxa"][1]


@pytest.mark.parametrize("name", list(COMMANDS))
def test_joint_angles_respect_soft_limits(name: str) -> None:
    for phase in IK_PHASES:
        angles = gait.plan(phase, COMMANDS[name]).joint_angles
        for j, joint in enumerate(config.JOINTS_PER_LEG):
            low, high = config.GAIT_SOFT_LIMITS_DEG[joint]
            assert np.all(np.degrees(angles[:, j]) >= low - 1e-6)
            assert np.all(np.degrees(angles[:, j]) <= high + 1e-6)


@pytest.mark.parametrize("name", list(COMMANDS))
def test_targets_continuous_across_phase_wrap(name: str) -> None:
    cmd = COMMANDS[name]
    wrap_gap = np.abs(gait.foot_targets(1.0 - 1e-9, cmd) - gait.foot_targets(0.0, cmd)).max()
    assert wrap_gap < 1e-6
    # And no jumps anywhere in the cycle: each tiny phase step moves feet a tiny amount.
    fine = np.linspace(0.0, 2.0, 2001)
    targets = np.array([gait.foot_targets(p, cmd) for p in fine])
    assert np.abs(np.diff(targets, axis=0)).max() < 1e-3  # metres per 1/1000 of a cycle


def test_phase_outside_unit_interval_wraps() -> None:
    cmd = COMMANDS["forward"]
    assert gait.foot_targets(0.3, cmd) == pytest.approx(gait.foot_targets(1.3, cmd))
    assert gait.foot_targets(0.3, cmd) == pytest.approx(gait.foot_targets(-0.7, cmd))


def test_over_limit_commands_are_clamped() -> None:
    huge = BodyVelocity(5.0, 5.0, 10.0)
    limited = gait.limit_command(huge)
    assert math.hypot(limited.vx, limited.vy) <= MAX_V + 1e-12
    assert abs(limited.yaw_rate) <= MAX_W + 1e-12
    stance = PARAMS.stance_time_s
    for leg in config.LEG_NAMES:
        stride = np.hypot(*gait._foot_velocity(leg, limited)) * stance  # noqa: SLF001
        assert stride <= PARAMS.max_stride_m + 1e-12
    for phase in PHASES[::20]:
        assert gait.plan(phase, huge).stride_scale == 1.0


def test_unreachable_stride_is_scaled_down_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    # A stride far beyond what the soft limits allow must shrink, never produce a bad pose.
    params = GaitParams(max_stride_m=0.4, max_speed_m_s=2.0)
    cmd = BodyVelocity(vx=2.0)
    with caplog.at_level(logging.WARNING):
        steps = [gait.plan(phase, cmd, params) for phase in PHASES[::10]]
    assert any(step.stride_scale < 1.0 for step in steps)
    assert any("scaled" in record.message for record in caplog.records)
    for step in steps:
        for j, joint in enumerate(config.JOINTS_PER_LEG):
            low, high = config.GAIT_SOFT_LIMITS_DEG[joint]
            assert np.all(np.degrees(step.joint_angles[:, j]) >= low - 1e-6)
            assert np.all(np.degrees(step.joint_angles[:, j]) <= high + 1e-6)


def test_params_validation() -> None:
    with pytest.raises(ValueError):
        GaitParams(swing_fraction=0.6)  # groups would swing together
    with pytest.raises(ValueError):
        GaitParams(swing_fraction=0.0)
    with pytest.raises(ValueError):
        GaitParams(period_s=0.0)


def test_max_speed_matches_stride_and_stance_time() -> None:
    assert config.GAIT_MAX_SPEED_M_S == pytest.approx(
        config.STEP_LENGTH_MAX_M / ((1 - config.GAIT_SWING_FRACTION) * config.GAIT_PERIOD_S)
    )


def test_gait_module_is_pure() -> None:
    source = (Path(config.PROJECT_ROOT) / "body" / "gait.py").read_text()
    for forbidden in ("pybullet", "import time", "monotonic", "sleep"):
        assert forbidden not in source
