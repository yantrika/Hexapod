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
FALL_CLEAR_TILT_DEG = 25.0  # a fallen body counts as upright again below this (hysteresis)

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
BRIDGE_DRAIN_LIMIT = 4 * COMMAND_QUEUE_MAXSIZE  # max messages read per tick (flood guard)
BODY_START_TIMEOUT_S = 20.0  # waiting for the body process to report ready (PyBullet start-up)
BODY_SHUTDOWN_TIMEOUT_S = 3.0  # join time before terminate, then kill
PARENT_CHECK_TICKS = 5  # the body checks that its parent is alive every this many ticks

# PyBullet window (GUI mode only; the window never draws text, see AGENTS.md)
GUI_CAMERA_DISTANCE_M = 0.65  # PLACEHOLDER: the robot is about 0.55 m across with legs out
GUI_CAMERA_YAW_DEG = 40.0
GUI_CAMERA_PITCH_DEG = -30.0
GUI_FOLLOW_CAMERA = True  # keep the robot in view while it walks
GUI_CAMERA_HZ = 10.0  # follow-camera update rate
GUI_SHADOWS = True  # PyBullet shadow rendering; False is cheaper on weak GPUs

SIM_HEADLESS = False  # PyBullet DIRECT mode; overridden by --headless
SIM_REALTIME = True  # --no-realtime is for fast tests only

# --- Router --------------------------------------------------------------
ROUTER_THRESHOLD = 85  # rapidfuzz fuzz.ratio, 0-100
ROUTER_MAX_WORDS = 4  # after filler removal
ROUTER_FILLERS = ("please", "can you", "could you", "hexa", "hey", "now", "just")
# Stop words match anywhere (whole words / phrases), highest priority. The accepted tradeoff:
# "I can't stop laughing" and "do not stop talking" stop the robot (stopping errs on the safe side).
STOP_WORDS = ("stop", "halt", "freeze", "whoa", "hold still", "stay still")
_WALK_FWD = ("walk", {"direction": "fwd", "speed": 0.5})
_WALK_BACK = ("walk", {"direction": "back", "speed": 0.5})
_TURN_LEFT = ("turn", {"direction": "left", "angle_deg": TURN_DEFAULT_ANGLE_DEG})
_TURN_RIGHT = ("turn", {"direction": "right", "angle_deg": TURN_DEFAULT_ANGLE_DEG})
# A single word must match a phrase here exactly; two or more words are fuzzy-matched.
# phrase -> (action, params). Numbers are not parsed in v1; the router never sends strafe or yaw.
ROUTER_PHRASES: dict[str, tuple[str, dict[str, object]]] = {
    "walk": _WALK_FWD,
    "walk forward": _WALK_FWD,
    "walk ahead": _WALK_FWD,
    "go forward": _WALK_FWD,
    "move forward": _WALK_FWD,
    "walk back": _WALK_BACK,
    "walk backward": _WALK_BACK,
    "go back": _WALK_BACK,
    "go backward": _WALK_BACK,
    "move back": _WALK_BACK,
    "back up": _WALK_BACK,
    "turn left": _TURN_LEFT,
    "turn right": _TURN_RIGHT,
    "sit": ("sit", {}),
    "sit down": ("sit", {}),
    "stand": ("stand", {}),
    "stand up": ("stand", {}),
    "get up": ("stand", {}),
    "wave": ("wave", {}),
    "wave hello": ("wave", {}),
    "wave hi": ("wave", {}),
}
# Exact-only aliases (never fuzzed, so "I sat down" stays chat): ASR-typical near-forms.
ROUTER_ALIASES: dict[str, tuple[str, dict[str, object]]] = {
    "waves": ("wave", {}),
    "sat down": ("sit", {}),
}

# --- Brain (typed-text and voice front ends) -------------------------------
VOICE_WALK_MAX_S = 10.0  # a walk started by text/voice stops being kept alive after this

# --- Audio ---------------------------------------------------------------
AUDIO_SAMPLE_RATE = 16000  # Vosk expects 16 kHz mono
AUDIO_BLOCKSIZE = 4000  # 250 ms
MIC_DEVICE: int | None = None
SPEAKER_DEVICE: int | None = None
SPEAK_TAIL_S = 0.4  # STT stays gated this long after TTS ends
UTTERANCE_QUEUE_MAXSIZE = 8
TTS_QUEUE_MAXSIZE = 8  # utterances waiting to be synthesized
TTS_PREFETCH_SIZE = 1  # synthesized clips waiting for the speaker: sentence N+1 renders during N
TTS_CLEAR_MAX_S = 0.1  # playback.clear() returns within this
TTS_SYNTH_TIMEOUT_S = 10.0  # one sentence must be synthesized within this, else Piper is restarted
TTS_START_TIMEOUT_S = 20.0  # extra allowance for the first sentence (model load, about 2.6 s here)
TTS_STOP_TIMEOUT_S = 2.0  # how long a closing Piper gets to exit before it is killed
TTS_SHUTDOWN_TIMEOUT_S = 5.0  # playback.shutdown() joins its threads for at most this long
TTS_POLL_S = 0.005  # playback thread wake-up period while a speaking tail is running
PHRASES_DIR = ASSETS_DIR / "phrases"  # pre-rendered WAVs, gitignored like the models
# Fixed phrases and short fillers, rendered once by scripts/prerender_phrases.py and played with
# no synthesis wait. name -> text. The dialogue step (9) speaks these by name.
TTS_PHRASES: dict[str, str] = {
    "okay": "Okay.",
    "cant_do_that": "I can't do that.",
    "already_sitting": "I'm already sitting.",
    "already_standing": "I'm already standing.",
    "standing_up": "Okay, I am standing up.",
    "sitting_down": "Okay, I am sitting down.",
    "walking": "Okay, walking.",
    "turning": "Okay, turning.",
    "waving": "Hello!",
    "stopped": "Stopped.",
    "fell_over": "Oops, I fell over.",
    "didnt_catch": "Sorry, I didn't catch that.",
    "hmm": "Hmm.",
    "one_moment": "One moment.",
    "let_me_think": "Let me think.",
}

# --- Models --------------------------------------------------------------
VOSK_MODEL_PATH = VOSK_DIR / "vosk-model-small-en-us-0.15"
PIPER_BINARY = PIPER_DIR / "piper" / "piper"  # standalone binary, ONE long-lived subprocess
PIPER_MODEL_PATH = PIPER_DIR / "en_US-amy-low.onnx"
# Optional isolation of Piper from the control loop (measured: unconstrained, its two compute
# threads take about 2.2 cores and double the body tick time on the dev laptop).
PIPER_NICE = 0  # extra niceness for the Piper process (0 = none)
PIPER_CPU_LIST: str | None = None  # taskset CPU list, e.g. "3"; None = any CPU
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
BODY_TEST_STOP_MAX_S = 0.1  # body process: send stop -> done status, even with a full queue
BODY_TEST_FLOOD_TICK_RATIO = 2.0  # body process: mean tick under a flood vs. idle, at most
BODY_TEST_ACCEPT_MAX_S = 0.06  # body process: send -> accepted (3 control ticks)


def clamp(value: float, low: float, high: float) -> float:
    """Return *value* limited to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def mount_yaw_rad(leg: str) -> float:
    """Math yaw (counter-clockwise from +X, radians) of a leg's mount.

    The only place the clockwise mount-angle convention is converted.
    """
    return -math.radians(LEG_MOUNT_ANGLES_DEG[leg])
