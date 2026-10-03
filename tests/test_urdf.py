"""Step 2 checks: the generated URDF matches config and kinematics conventions."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest

import config
from body import kinematics
from body.urdf import build_urdf


@pytest.fixture(scope="module")
def robot() -> ET.Element:
    return ET.fromstring(build_urdf())


def _joint(robot: ET.Element, name: str) -> ET.Element:
    joint = next(j for j in robot.findall("joint") if j.get("name") == name)
    return joint


def _floats(text: str | None) -> list[float]:
    assert text is not None
    return [float(v) for v in text.split()]


def test_committed_urdf_is_up_to_date() -> None:
    assert config.URDF_PATH.read_text() == build_urdf()


def test_has_18_revolute_joints_in_config_names(robot: ET.Element) -> None:
    revolute = [j.get("name") for j in robot.findall("joint") if j.get("type") == "revolute"]
    assert revolute == list(config.JOINT_NAMES)
    assert len(revolute) == config.DOF


@pytest.mark.parametrize("leg", config.LEG_NAMES)
def test_axes_match_kinematics_sign_conventions(robot: ET.Element, leg: str) -> None:
    axis = lambda joint: _floats(_joint(robot, f"{leg}_{joint}").find("axis").get("xyz"))  # type: ignore[union-attr]  # noqa: E731
    assert axis("coxa") == [0, 0, 1]  # positive = counter-clockwise about +Z
    assert axis("femur") == [0, -1, 0]  # positive raises the femur
    assert axis("tibia") == [0, -1, 0]  # positive opens the knee


@pytest.mark.parametrize("leg", config.LEG_NAMES)
def test_leg_mount_uses_mount_yaw_helper(robot: ET.Element, leg: str) -> None:
    origin = _joint(robot, f"{leg}_coxa").find("origin")
    assert origin is not None
    xyz, rpy = _floats(origin.get("xyz")), _floats(origin.get("rpy"))
    assert rpy[2] == pytest.approx(config.mount_yaw_rad(leg), abs=1e-8)
    assert xyz == pytest.approx(list(kinematics.hip_position(leg)), abs=1e-8)
    assert math.hypot(xyz[0], xyz[1]) == pytest.approx(config.BODY_RADIUS)


def test_limits_match_hard_limits(robot: ET.Element) -> None:
    for name in config.JOINT_NAMES:
        limit = _joint(robot, name).find("limit")
        assert limit is not None
        low, high = config.JOINT_HARD_LIMITS_DEG[name.split("_")[1]]
        assert float(limit.get("lower", "nan")) == pytest.approx(math.radians(low), abs=1e-8)
        assert float(limit.get("upper", "nan")) == pytest.approx(math.radians(high), abs=1e-8)


def test_link_lengths_match_config(robot: ET.Element) -> None:
    offsets = {
        "femur": (_joint(robot, "RF_femur"), config.COXA_LENGTH),
        "tibia": (_joint(robot, "RF_tibia"), config.FEMUR_LENGTH),
    }
    for joint, expected in offsets.values():
        origin = joint.find("origin")
        assert origin is not None
        assert _floats(origin.get("xyz")) == pytest.approx([expected, 0, 0])
    foot = _joint(robot, "RF_foot_joint").find("origin")
    assert foot is not None
    assert _floats(foot.get("xyz")) == pytest.approx([0, 0, -config.TIBIA_LENGTH])
