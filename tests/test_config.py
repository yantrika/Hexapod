"""Step 0 checks: the revised config is symmetric and complete."""

from __future__ import annotations

import math

import pytest

import config

REQUIRED_NAMES = [
    # filesystem
    "PROJECT_ROOT", "ASSETS_DIR", "URDF_DIR", "URDF_PATH", "VOSK_DIR", "PIPER_DIR",
    "LOG_DIR", "LOG_FILE", "LOG_LEVEL",
    # geometry
    "BODY_RADIUS", "COXA_LENGTH", "FEMUR_LENGTH", "TIBIA_LENGTH", "LEG_NAMES",
    "JOINTS_PER_LEG", "DOF", "JOINT_NAMES", "LEG_MOUNT_ANGLES_DEG", "TRIPOD_A", "TRIPOD_B",
    "KNEE_BRANCH", "mount_yaw_rad",
    # limits and clamps
    "JOINT_HARD_LIMITS_DEG", "GAIT_SOFT_LIMITS_DEG", "SPEED_MIN", "SPEED_MAX",
    "TURN_ANGLE_MIN_DEG", "TURN_ANGLE_MAX_DEG", "TURN_RATE_MAX_DEG_S",
    "STEP_LENGTH_MAX_M", "STEP_HEIGHT_M", "BODY_HEIGHT_SIT", "BODY_HEIGHT_STAND",
    "FALL_TILT_DEG", "clamp",
    # simulation model
    "BODY_THICKNESS_M", "BODY_MASS_KG", "LINK_RADIUS_M", "LINK_MASS_KG", "FOOT_RADIUS_M",
    "GROUND_FRICTION", "FOOT_FRICTION", "JOINT_MAX_FORCE_NM", "JOINT_MAX_VELOCITY_RAD_S",
    "JOINT_POSITION_GAIN", "JOINT_VELOCITY_GAIN", "SIM_GRAVITY", "SIM_SPAWN_CLEARANCE_M",
    # timing and bridge
    "PHYSICS_HZ", "CONTROL_HZ", "MAX_PHYSICS_CATCHUP_STEPS", "GAIT_PERIOD_S",
    "MAX_MESSAGE_AGE_S", "WATCHDOG_TIMEOUT_S", "HEARTBEAT_HZ", "COMMAND_QUEUE_MAXSIZE",
    "STATUS_QUEUE_MAXSIZE", "SIM_HEADLESS", "SIM_REALTIME",
    # router
    "ROUTER_THRESHOLD", "ROUTER_MAX_WORDS", "ROUTER_FILLERS", "STOP_WORDS", "ROUTER_PHRASES",
    # audio
    "AUDIO_SAMPLE_RATE", "AUDIO_BLOCKSIZE", "MIC_DEVICE", "SPEAKER_DEVICE", "SPEAK_TAIL_S",
    "UTTERANCE_QUEUE_MAXSIZE", "TTS_QUEUE_MAXSIZE", "TTS_CLEAR_MAX_S",
    # models
    "VOSK_MODEL_PATH", "PIPER_BINARY", "PIPER_MODEL_PATH", "OLLAMA_URL", "OLLAMA_MODEL",
    "OLLAMA_TIMEOUT_S", "CHAT_MAX_TOKENS", "CHAT_HISTORY_TURNS",
    # test tolerances
    "IK_TOLERANCE_M",
]


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_constant_exists(name: str) -> None:
    assert hasattr(config, name)


def test_mount_angles_match_spec() -> None:
    assert config.LEG_MOUNT_ANGLES_DEG == {
        "RF": 30, "RM": 90, "RR": 150, "LR": 210, "LM": 270, "LF": 330,
    }


def test_leg_order_is_clockwise() -> None:
    angles = [config.LEG_MOUNT_ANGLES_DEG[leg] for leg in config.LEG_NAMES]
    assert angles == sorted(angles)


@pytest.mark.parametrize("right,left", [("RF", "LF"), ("RM", "LM"), ("RR", "LR")])
def test_left_mirrors_right(right: str, left: str) -> None:
    angles = config.LEG_MOUNT_ANGLES_DEG
    assert angles[left] == pytest.approx(360.0 - angles[right])


def test_dof() -> None:
    assert config.DOF == 18


def test_tripod_groups_partition_legs() -> None:
    a, b = set(config.TRIPOD_A), set(config.TRIPOD_B)
    assert a.isdisjoint(b)
    assert a | b == set(config.LEG_NAMES)
    assert set(config.TRIPOD_A) == {"RF", "RR", "LM"}
    assert set(config.TRIPOD_B) == {"RM", "LR", "LF"}


@pytest.mark.parametrize("group_name", ["TRIPOD_A", "TRIPOD_B"])
def test_tripod_legs_are_120_degrees_apart(group_name: str) -> None:
    group = getattr(config, group_name)
    angles = sorted(config.LEG_MOUNT_ANGLES_DEG[leg] for leg in group)
    gaps = [angles[1] - angles[0], angles[2] - angles[1], 360.0 - angles[2] + angles[0]]
    assert gaps == pytest.approx([120.0, 120.0, 120.0])


def test_hard_limits_are_plus_minus_90() -> None:
    for joint in config.JOINTS_PER_LEG:
        assert config.JOINT_HARD_LIMITS_DEG[joint] == (-90.0, 90.0)


def test_soft_limits_inside_hard_limits() -> None:
    for joint in config.JOINTS_PER_LEG:
        hard_low, hard_high = config.JOINT_HARD_LIMITS_DEG[joint]
        soft_low, soft_high = config.GAIT_SOFT_LIMITS_DEG[joint]
        assert hard_low <= soft_low < soft_high <= hard_high


@pytest.mark.parametrize("leg", config.LEG_NAMES)
def test_mount_yaw_is_clockwise_convention(leg: str) -> None:
    yaw = config.mount_yaw_rad(leg)
    assert yaw == pytest.approx(-math.radians(config.LEG_MOUNT_ANGLES_DEG[leg]))
    x, y = math.cos(yaw), math.sin(yaw)  # outward direction in the body frame
    assert (y < -1e-9) == leg.startswith("R")  # right legs point to -Y
    assert (y > 1e-9) == leg.startswith("L")  # left legs point to +Y
    if leg.endswith("F"):
        assert x > 0
    elif leg.endswith("R"):
        assert x < 0
    else:
        assert x == pytest.approx(0.0, abs=1e-9)


def test_mount_yaw_known_values() -> None:
    assert config.mount_yaw_rad("RM") == pytest.approx(-math.pi / 2)  # straight right
    assert config.mount_yaw_rad("LM") == pytest.approx(-3 * math.pi / 2)  # straight left


def test_clamp() -> None:
    assert config.clamp(5.0, 0.0, 1.0) == 1.0
    assert config.clamp(-5.0, 0.0, 1.0) == 0.0
    assert config.clamp(0.4, 0.0, 1.0) == 0.4


def test_router_settings_are_sane() -> None:
    assert 0 < config.ROUTER_THRESHOLD <= 100
    assert config.ROUTER_MAX_WORDS >= 2
    assert all(word == word.lower() for word in config.STOP_WORDS + config.ROUTER_FILLERS)
    for phrase, (action, _params) in config.ROUTER_PHRASES.items():
        assert phrase == phrase.lower()
        assert len(phrase.split()) <= config.ROUTER_MAX_WORDS
        assert action in {"walk", "turn", "sit", "stand", "wave"}


def test_timing_relationships() -> None:
    assert config.PHYSICS_HZ > config.CONTROL_HZ > 0
    assert config.WATCHDOG_TIMEOUT_S > 1.0 / config.HEARTBEAT_HZ
    assert config.MAX_MESSAGE_AGE_S > 1.0 / config.CONTROL_HZ
    assert config.BODY_HEIGHT_SIT < config.BODY_HEIGHT_STAND


def test_stand_height_is_tibia_length() -> None:
    assert config.BODY_HEIGHT_STAND == config.TIBIA_LENGTH


def test_joint_names() -> None:
    assert len(config.JOINT_NAMES) == config.DOF
    assert len(set(config.JOINT_NAMES)) == config.DOF
    assert config.JOINT_NAMES[:3] == ("RF_coxa", "RF_femur", "RF_tibia")
