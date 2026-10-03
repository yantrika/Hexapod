"""Step 1 checks: leg FK/IK and the single body-frame conversion."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

import config
from body import kinematics as kin

NEUTRAL = kin.NEUTRAL_FOOT_LOCAL


def _reachable_points(count: int = 100, seed: int = 1234) -> np.ndarray:
    """Seeded points in a box around the neutral stance foot position.

    The box is well inside the workspace and the hard limits, so every
    point is reachable by construction (the first test asserts it).
    """
    rng = np.random.default_rng(seed)
    low = NEUTRAL + np.array([-0.04, -0.06, -0.04])
    high = NEUTRAL + np.array([0.04, 0.06, 0.04])
    return rng.uniform(low, high, size=(count, 3))


def test_neutral_pose_is_femur_horizontal_tibia_vertical() -> None:
    expected = [config.COXA_LENGTH + config.FEMUR_LENGTH, 0.0, -config.TIBIA_LENGTH]
    assert NEUTRAL == pytest.approx(expected)


def test_ik_of_neutral_foot_is_all_zero() -> None:
    angles = kin.ik(NEUTRAL)
    assert angles is not None
    assert angles == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


def test_fk_of_ik_recovers_point_on_100_random_points() -> None:
    points = _reachable_points()
    assert len(points) == 100
    for point in points:
        angles = kin.ik(point)
        assert angles is not None, f"reachable point {point} returned None"
        assert kin.fk(*angles) == pytest.approx(point, abs=config.IK_TOLERANCE_M)


def test_ik_returns_knee_up_branch() -> None:
    for point in _reachable_points():
        angles = kin.ik(point)
        assert angles is not None
        femur_joint, knee, foot = kin.fk_joints(*angles)
        # Work in the leg's vertical plane (radial distance, height).
        radial = np.array([math.cos(angles[0]), math.sin(angles[0]), 0.0])
        to_foot = np.array([(foot - femur_joint) @ radial, foot[2] - femur_joint[2]])
        to_knee = np.array([(knee - femur_joint) @ radial, knee[2] - femur_joint[2]])
        cross = to_foot[0] * to_knee[1] - to_foot[1] * to_knee[0]
        assert cross > 0.0  # knee on the upper side of the femur-joint -> foot line
        # And the knee is above that line at the knee's radial position.
        line_height = to_foot[1] * to_knee[0] / to_foot[0]
        assert to_knee[1] > line_height


def test_knee_down_solution_is_not_returned() -> None:
    angles = kin.ik(NEUTRAL + np.array([0.02, 0.01, 0.0]))
    assert angles is not None
    _, femur, _ = angles
    # Knee-down would pitch the femur below the line to the foot; the foot is
    # below the femur joint here, so the femur must point above that line.
    _, _, foot = kin.fk_joints(*angles)
    femur_joint = kin.fk_joints(*angles)[0]
    line_pitch = math.atan2(foot[2] - femur_joint[2], math.hypot(*(foot[:2] - femur_joint[:2])))
    assert femur > line_pitch


@pytest.mark.parametrize(
    "point",
    [
        (0.60, 0.0, 0.0),  # beyond full reach
        (0.0, 0.40, -0.10),  # beyond full reach, sideways
        (0.14, 0.0, -0.40),  # too far below
        (config.COXA_LENGTH, 0.0, 0.0),  # at the femur joint (distance 0)
        (config.COXA_LENGTH + 0.01, 0.0, 0.0),  # inside the minimum reach annulus
    ],
)
def test_unreachable_points_return_none(point: tuple[float, float, float]) -> None:
    assert kin.ik(point) is None


@pytest.mark.parametrize(
    "point",
    [
        (-0.02, 0.14, -0.13),  # coxa would need ~98 deg
        (config.COXA_LENGTH, 0.0, 0.10),  # femur would need ~176 deg
    ],
)
def test_reachable_but_outside_hard_limits_returns_none(point: tuple[float, float, float]) -> None:
    femur_len, tibia_len = config.FEMUR_LENGTH, config.TIBIA_LENGTH
    r = math.hypot(point[0], point[1]) - config.COXA_LENGTH
    dist = math.hypot(r, point[2])
    assert abs(femur_len - tibia_len) < dist < femur_len + tibia_len  # geometrically reachable
    assert kin.ik(point) is None


def test_full_extension_boundary_is_accepted() -> None:
    reach = config.COXA_LENGTH + config.FEMUR_LENGTH + config.TIBIA_LENGTH
    angles = kin.ik((reach, 0.0, 0.0))
    assert angles is not None
    assert angles == pytest.approx((0.0, 0.0, math.pi / 2), abs=1e-6)


def test_ik_never_raises_and_respects_hard_limits() -> None:
    rng = np.random.default_rng(7)
    for point in rng.uniform(-1.0, 1.0, size=(2000, 3)):
        angles = kin.ik(point)
        if angles is None:
            continue
        for joint, angle in zip(config.JOINTS_PER_LEG, angles, strict=True):
            low, high = config.JOINT_HARD_LIMITS_DEG[joint]
            assert math.radians(low) - 1e-9 <= angle <= math.radians(high) + 1e-9


@pytest.mark.parametrize(
    "point",
    [
        (math.nan, 0.0, 0.0),
        (0.0, math.inf, 0.0),
        (0.0, 0.0, -math.inf),
        (1e300, 1e300, 1e300),
        ("a", 0, 0),
    ],
)
def test_ik_rejects_bad_input_without_raising(point: object) -> None:
    assert kin.ik(point) is None  # type: ignore[arg-type]


def test_hip_positions_lie_on_body_circle() -> None:
    for leg in config.LEG_NAMES:
        assert np.linalg.norm(kin.hip_position(leg)) == pytest.approx(config.BODY_RADIUS)


@pytest.mark.parametrize("leg", config.LEG_NAMES)
def test_leg_body_round_trip(leg: str) -> None:
    for point in _reachable_points(10):
        assert kin.body_to_leg(leg, kin.leg_to_body(leg, point)) == pytest.approx(point, abs=1e-12)


def test_neutral_foot_sides_and_mirror() -> None:
    feet = {leg: kin.neutral_foot_body(leg) for leg in config.LEG_NAMES}
    # RF lands on the right (y < 0) and in front (x > 0); LF on the left.
    assert feet["RF"][0] > 0 and feet["RF"][1] < 0
    assert feet["LF"][0] > 0 and feet["LF"][1] > 0
    assert feet["RR"][0] < 0 and feet["RR"][1] < 0
    assert feet["LR"][0] < 0 and feet["LR"][1] > 0
    # Middle legs sit at x ~ 0 on opposite sides.
    assert feet["RM"][0] == pytest.approx(0.0, abs=1e-12) and feet["RM"][1] < 0
    assert feet["LM"][0] == pytest.approx(0.0, abs=1e-12) and feet["LM"][1] > 0
    # Each left foot is the mirror image (y negated) of its right counterpart.
    for right, left in (("RF", "LF"), ("RM", "LM"), ("RR", "LR")):
        mirrored = feet[right] * np.array([1.0, -1.0, 1.0])
        assert feet[left] == pytest.approx(mirrored, abs=1e-12)
    # Every foot hangs the same distance below the body plane.
    assert all(foot[2] == pytest.approx(-config.TIBIA_LENGTH) for foot in feet.values())


def test_same_local_target_gives_same_ik_for_every_leg() -> None:
    # Left and right legs differ only by mount yaw; the IK itself is shared.
    angles = kin.ik(NEUTRAL + np.array([0.01, 0.02, -0.01]))
    assert angles is not None
    for leg in config.LEG_NAMES:
        body_point = kin.leg_to_body(leg, NEUTRAL + np.array([0.01, 0.02, -0.01]))
        assert kin.ik(kin.body_to_leg(leg, body_point)) == pytest.approx(angles, abs=1e-9)


def test_kinematics_uses_mount_yaw_helper_only() -> None:
    source = (Path(config.PROJECT_ROOT) / "body" / "kinematics.py").read_text()
    assert "mount_yaw_rad" in source
    assert "LEG_MOUNT_ANGLES_DEG" not in source


def test_neutral_foot_height_equals_stand_height() -> None:
    assert NEUTRAL[2] == pytest.approx(-config.BODY_HEIGHT_STAND)
    for leg in config.LEG_NAMES:
        assert kin.neutral_foot_body(leg)[2] == pytest.approx(-config.BODY_HEIGHT_STAND)


def test_foot_positions_body_matches_per_leg_fk_and_neutral() -> None:
    zero = np.zeros((6, 3))
    for i, leg in enumerate(config.LEG_NAMES):
        assert kin.foot_positions_body(zero)[i] == pytest.approx(kin.neutral_foot_body(leg))
    angles = np.random.default_rng(3).uniform(-1.0, 1.0, size=(6, 3))
    feet = kin.foot_positions_body(angles)
    for i, leg in enumerate(config.LEG_NAMES):
        assert feet[i] == pytest.approx(kin.leg_to_body(leg, kin.fk(*angles[i])))
    assert kin.foot_positions_body(angles.ravel()) == pytest.approx(feet)  # flat input works too
