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
from pathlib import Path

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
TURN_RATE_MAX_DEG_S = 60.0  # PLACEHOLDER
STEP_LENGTH_MAX_M = 0.05  # PLACEHOLDER
STEP_HEIGHT_M = 0.03  # PLACEHOLDER
BODY_HEIGHT_SIT = 0.06  # m, PLACEHOLDER
BODY_HEIGHT_STAND = 0.11  # m, PLACEHOLDER
FALL_TILT_DEG = 50.0

# --- Timing and bridge ---------------------------------------------------
PHYSICS_HZ = 240.0  # PyBullet step, driven by wall-clock time
CONTROL_HZ = 50.0  # control tick, driven by wall-clock time
MAX_PHYSICS_CATCHUP_STEPS = 12  # caps catch-up after a stall (~50 ms of sim)
GAIT_PERIOD_S = 1.0  # PLACEHOLDER, one full tripod cycle

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


def clamp(value: float, low: float, high: float) -> float:
    """Return *value* limited to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def mount_yaw_rad(leg: str) -> float:
    """Math yaw (counter-clockwise from +X, radians) of a leg's mount.

    The only place the clockwise mount-angle convention is converted.
    """
    return -math.radians(LEG_MOUNT_ANGLES_DEG[leg])
