"""Central configuration for the hexapod.

Single source of truth for geometry, joint limits, clamp ranges, timing,
router, audio, paths and model settings. No other module should hard-code
these values (see ``plan.md`` section 7).

Lengths are metres; angles are radians unless a name ends in ``_DEG``.
Values marked PLACEHOLDER are to be tuned against the URDF and real servos.

Frames: body frame is +X forward, +Y left, +Z up. Leg mount angles are
measured clockwise as seen from above, 0 deg = +X. Use ``mount_yaw_rad``
to turn a mount angle into math yaw; it is the only conversion point.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

# Small numpy arrays gain nothing from BLAS threads, and the spinning threads starve the
# control loop (measured: 7x slower ticks on the 2-core dev laptop). config is imported
# before numpy everywhere, so this takes effect.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

# --- Filesystem ----------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets"
URDF_DIR = ASSETS_DIR / "urdf"
URDF_PATH = URDF_DIR / "hexapod.urdf"
VOSK_DIR = ASSETS_DIR / "vosk"
PIPER_DIR = ASSETS_DIR / "piper"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "hexa.log"
LOG_LEVEL = "INFO"

# --- Body geometry (project spec) ----------------------------------------
BODY_RADIUS = 0.12  # m
COXA_LENGTH = 0.05  # m
FEMUR_LENGTH = 0.09  # m
TIBIA_LENGTH = 0.13  # m

LEG_NAMES = ("RF", "RM", "RR", "LR", "LM", "LF")  # clockwise from front-right
JOINTS_PER_LEG = ("coxa", "femur", "tibia")
DOF = len(LEG_NAMES) * len(JOINTS_PER_LEG)  # 18
# Flat joint order used everywhere (backend arrays, URDF joint names).
JOINT_NAMES = tuple(f"{leg}_{joint}" for leg in LEG_NAMES for joint in JOINTS_PER_LEG)

# Mount angle around the body, degrees CLOCKWISE from above, 0 = +X (forward).
# Left legs mirror right legs: angle(L) = 360 - angle(R) for the same position.
LEG_MOUNT_ANGLES_DEG = {
    "RF": 30.0,
    "RM": 90.0,
    "RR": 150.0,
    "LR": 210.0,
    "LM": 270.0,
    "LF": 330.0,
}

# Alternating tripod gait: each group is 120 deg apart and swings together.
TRIPOD_A = ("RF", "RR", "LM")
TRIPOD_B = ("RM", "LR", "LF")

# IK always returns this knee solution ("up": knee above the hip-foot line).
KNEE_BRANCH = "up"

# --- Joint limits (degrees, clean joint frame: 0 = neutral) --------------
# Hard limits: servo range (~180 deg), enforced once in the backend clamp layer.
JOINT_HARD_LIMITS_DEG = {
    "coxa": (-90.0, 90.0),
    "femur": (-90.0, 90.0),
    "tibia": (-90.0, 90.0),
}
# Soft gait limits: used by the gait planner only; must lie inside the hard
# limits. PLACEHOLDER: femur/tibia are tuned in Steps 2-3.
GAIT_SOFT_LIMITS_DEG = {
    "coxa": (-30.0, 30.0),
    "femur": (-90.0, 90.0),
    "tibia": (-90.0, 90.0),
}

# --- Motion clamps (the one place speeds and angles are bounded) ---------
SPEED_MIN = 0.0
SPEED_MAX = 1.0  # normalised walk speed
TURN_ANGLE_MIN_DEG = 1.0
TURN_ANGLE_MAX_DEG = 180.0
TURN_DEFAULT_ANGLE_DEG = 90.0
TURN_RATE_MAX_DEG_S = 20.0  # max yaw rate; PLACEHOLDER, limited by STEP_LENGTH_MAX_M
STEP_LENGTH_MAX_M = 0.05  # max stride (stance travel per foot per step); PLACEHOLDER
STEP_HEIGHT_M = 0.03  # swing height; PLACEHOLDER
GAIT_FULL_LIFT_STRIDE_M = 0.01  # below this stride the swing height shrinks proportionally
BODY_HEIGHT_SIT = 0.06  # m, PLACEHOLDER
# The zero pose is the stand pose: femur horizontal, tibia vertical, so the
# neutral foot hangs TIBIA_LENGTH below the body plane.
BODY_HEIGHT_STAND = TIBIA_LENGTH
FALL_TILT_DEG = 50.0

# --- Simulation model (URDF and PyBullet; PLACEHOLDER values) -------------
BODY_THICKNESS_M = 0.04
BODY_MASS_KG = 0.6
LINK_RADIUS_M = 0.008
LINK_MASS_KG = {"coxa": 0.03, "femur": 0.05, "tibia": 0.05, "foot": 0.01}
FOOT_RADIUS_M = 0.01  # contact sphere; its lowest point is the foot target
GROUND_FRICTION = 1.0
FOOT_FRICTION = 1.0
JOINT_MAX_FORCE_NM = 3.0  # servo torque limit
JOINT_MAX_VELOCITY_RAD_S = 6.0  # servo speed limit
JOINT_POSITION_GAIN = 0.3
JOINT_VELOCITY_GAIN = 0.3  # low sim damping: real servos do not resist their own motion
SIM_GRAVITY = 9.81
SIM_SPAWN_CLEARANCE_M = 0.002  # spawn this far above the stand height

# --- Controller (body/controller.py) ---------------------------------------
VELOCITY_RAMP_S = 0.5  # time to ramp a velocity command from zero to its maximum
SIT_STAND_TRANSITION_S = 1.5  # PLACEHOLDER
SETTLE_S = 0.8  # held pose -> neutral stance before a sit/stand transition; PLACEHOLDER
RESUME_BLEND_S = 0.5  # held pose -> gait output when walking resumes after a stop
WAVE_LEG = "RF"
WAVE_DURATION_S = 3.0
WAVE_BLEND_S = 0.5  # raise and lower time at each end of the wave
WAVE_FREQUENCY_HZ = 1.5
WAVE_FEMUR_DEG = 60.0  # raised femur angle
WAVE_TIBIA_DEG = 20.0
WAVE_COXA_AMPLITUDE_DEG = 25.0
FOOT_TARGET_MAX_SPEED_M_S = 0.4  # no foot target may move faster than this (walk, ramps, blends)

# --- Manual control (scripts/teleop.py) ----------------------------------
TELEOP_SPEED_SCALE_DEFAULT = 0.5  # fraction of the max speed
TELEOP_SPEED_SCALE_MIN = 0.1
TELEOP_SPEED_SCALE_STEP = 0.1

# --- Timing and bridge ---------------------------------------------------
PHYSICS_HZ = 240.0  # PyBullet step, driven by wall-clock time
CONTROL_HZ = 50.0  # control tick, driven by wall-clock time
MAX_PHYSICS_CATCHUP_STEPS = 12  # caps catch-up after a stall (~50 ms of sim)
GAIT_PERIOD_S = 1.0  # PLACEHOLDER, one full tripod cycle
GAIT_SWING_FRACTION = 0.5  # share of the cycle a foot is in swing (0 < f <= 0.5)
# Stance feet travel one stride in the stance share of the cycle, so the
# fastest body speed is the max stride over the stance time.
GAIT_MAX_SPEED_M_S = STEP_LENGTH_MAX_M / ((1.0 - GAIT_SWING_FRACTION) * GAIT_PERIOD_S)

MAX_MESSAGE_AGE_S = 0.5  # older messages are dropped (a stale stop still runs)
WATCHDOG_TIMEOUT_S = 1.0  # walking/turning with no command or heartbeat
HEARTBEAT_HZ = 5.0
COMMAND_QUEUE_MAXSIZE = 8  # drop-oldest when full
STATUS_QUEUE_MAXSIZE = 64

SIM_HEADLESS = False  # PyBullet DIRECT mode; overridden by --headless
SIM_REALTIME = True  # --no-realtime is for fast tests only

# --- Router --------------------------------------------------------------
ROUTER_THRESHOLD = 85  # rapidfuzz fuzz.ratio, 0-100
ROUTER_MAX_WORDS = 4  # after filler removal
ROUTER_FILLERS = ("please", "can you", "could you", "hexa", "hey", "now", "just")
STOP_WORDS = ("stop", "halt", "freeze")  # match anywhere, highest priority
# phrase -> (action, params)
ROUTER_PHRASES: dict[str, tuple[str, dict[str, object]]] = {
    "walk forward": ("walk", {"direction": "fwd", "speed": 0.5}),
    "go forward": ("walk", {"direction": "fwd", "speed": 0.5}),
    "walk back": ("walk", {"direction": "back", "speed": 0.5}),
    "turn left": ("turn", {"direction": "left", "angle_deg": TURN_DEFAULT_ANGLE_DEG}),
    "turn right": ("turn", {"direction": "right", "angle_deg": TURN_DEFAULT_ANGLE_DEG}),
    "sit down": ("sit", {}),
    "stand up": ("stand", {}),
    "wave": ("wave", {}),
}

# --- Audio ---------------------------------------------------------------
AUDIO_SAMPLE_RATE = 16000  # Vosk expects 16 kHz mono
AUDIO_BLOCKSIZE = 4000  # 250 ms
MIC_DEVICE: int | None = None
SPEAKER_DEVICE: int | None = None
SPEAK_TAIL_S = 0.4  # STT stays gated this long after TTS ends
UTTERANCE_QUEUE_MAXSIZE = 8
TTS_QUEUE_MAXSIZE = 8
TTS_CLEAR_MAX_S = 0.1

# --- Models --------------------------------------------------------------
VOSK_MODEL_PATH = VOSK_DIR / "vosk-model-small-en-us-0.15"
PIPER_BINARY = "piper"  # standalone binary, called as a subprocess
PIPER_MODEL_PATH = PIPER_DIR / "en_US-amy-low.onnx"
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:1.5b"
OLLAMA_TIMEOUT_S = 20.0
CHAT_MAX_TOKENS = 80
CHAT_HISTORY_TURNS = 4

# --- Test tolerances -----------------------------------------------------
IK_TOLERANCE_M = 1e-4
WALK_TEST_MAX_TILT_DEG = 10.0  # sim walk tests: roll and pitch must stay under this
WALK_TEST_HEIGHT_TOL_M = 0.02  # sim walk tests: body height within this of stand height
WALK_TEST_SPEED_TOL = 0.25  # sim walk tests: measured motion within 25 % of commanded
WALK_TEST_POSITION_DRIFT_M = 0.05  # sim walk tests: unwanted displacement over 10 s
WALK_TEST_HEADING_DRIFT_DEG = 5.0  # sim walk tests: unwanted heading change over 10 s
HOLD_TEST_MAX_TILT_DEG = 5.0  # sim hold tests: roll/pitch while a pose is held
HOLD_TEST_MAX_BODY_SPEED_M_S = 0.02  # sim hold tests: body must be at rest after 3 s


def clamp(value: float, low: float, high: float) -> float:
    """Return *value* limited to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def mount_yaw_rad(leg: str) -> float:
    """Math yaw (counter-clockwise from +X, radians) of a leg's mount.

    The only place the clockwise mount-angle convention is converted.
    """
    return -math.radians(LEG_MOUNT_ANGLES_DEG[leg])
