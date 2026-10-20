"""Central configuration for the hexapod: the single source of truth.

Sections, in file order: paths and logging, body (geometry, limits, simulation, postures), gait,
safety and timing (bridge, watchdog, queues), voice (router, audio, speech models, chat), web
(the phone page), persona (Arya, memory), hardware (the Pi servo backend). No other module
should hard-code these values (see ``docs/DEVELOPMENT.md``).

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

# === Paths and logging ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets"
URDF_DIR = ASSETS_DIR / "urdf"
URDF_PATH = URDF_DIR / "hexapod.urdf"
VOSK_DIR = ASSETS_DIR / "vosk"
PIPER_DIR = ASSETS_DIR / "piper"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
PERSONA_DIR = DATA_DIR / "persona"  # arya.toml (the character) and limits.toml (the word lists)
MEMORY_DB_PATH = Path(os.environ.get("HEXA_MEMORY_DB") or DATA_DIR / "memory" / "arya.db")
# ... gitignored, never synced to the Pi (HEXA_MEMORY_DB moves it: the tests use a temp file)
LOG_FILE = LOG_DIR / "hexa.log"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 1_000_000  # logs/hexa.log rotates at this size ...
LOG_BACKUP_COUNT = 3  # ... keeping this many old files (hexa.log.1 ...)

# === Body =========================================================================================

# --- Geometry (project spec) ---
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

# --- Speed and posture bounds (the one place speeds and angles are bounded) ---
SPEED_MIN = 0.0
SPEED_MAX = 1.0  # normalised walk speed
TURN_ANGLE_MIN_DEG = 1.0
TURN_ANGLE_MAX_DEG = 180.0
TURN_DEFAULT_ANGLE_DEG = 90.0
BODY_HEIGHT_SIT = 0.06  # m, PLACEHOLDER
# The zero pose is the stand pose: femur horizontal, tibia vertical, so the
# neutral foot hangs TIBIA_LENGTH below the body plane.
BODY_HEIGHT_STAND = TIBIA_LENGTH

# --- Simulation model (URDF and PyBullet; PLACEHOLDER values) ---
BODY_THICKNESS_M = 0.04
LINK_RADIUS_M = 0.008
# MASSES ARE PLACEHOLDERS, NOT MEASURED: weigh the real body (battery, Pi, boards, wiring) and
# every servo + bracket + printed part, then replace these numbers. Every torque and power
# figure from scripts/torque_report.py scales with them (see docs/HARDWARE.md).
BODY_MASS_KG = 0.6  # PLACEHOLDER: plate, Pi 5, battery, PCA9685 boards, wiring
LINK_MASS_KG = {  # PLACEHOLDER, per link, including the servo that moves it
    "coxa": 0.03,
    "femur": 0.05,
    "tibia": 0.05,
    "foot": 0.01,
}
# Total robot mass (PLACEHOLDER while the masses above are): body plus six legs.
TOTAL_MASS_KG = BODY_MASS_KG + len(LEG_NAMES) * sum(LINK_MASS_KG.values())
# Servo sizing: the rated (stall) torque must be at least the simulated peak times this factor.
TORQUE_SAFETY_FACTOR = 2.0
FOOT_RADIUS_M = 0.01  # contact sphere; its lowest point is the foot target
GROUND_FRICTION = 1.0
FOOT_FRICTION = 1.0
JOINT_MAX_FORCE_NM = 3.0  # servo torque limit; PLACEHOLDER, not a real servo spec
JOINT_MAX_VELOCITY_RAD_S = 6.0  # servo speed limit
JOINT_POSITION_GAIN = 0.3
JOINT_VELOCITY_GAIN = 0.3  # low sim damping: real servos do not resist their own motion
SIM_GRAVITY = 9.81
SIM_SPAWN_CLEARANCE_M = 0.002  # spawn this far above the stand height

# --- Simulation loop and window ---
PHYSICS_HZ = 240.0  # PyBullet step, driven by wall-clock time
MAX_PHYSICS_CATCHUP_STEPS = 12  # caps catch-up after a stall (~50 ms of sim)
SIM_HEADLESS = False  # PyBullet DIRECT mode; overridden by --headless
# PyBullet window (GUI mode only; the window never draws text, see AGENTS.md)
GUI_CAMERA_DISTANCE_M = 0.65  # PLACEHOLDER: the robot is about 0.55 m across with legs out
GUI_CAMERA_YAW_DEG = 40.0
GUI_CAMERA_PITCH_DEG = -30.0
GUI_FOLLOW_CAMERA = True  # keep the robot in view while it walks
GUI_CAMERA_HZ = 10.0  # follow-camera update rate
GUI_SHADOWS = True  # PyBullet shadow rendering; False is cheaper on weak GPUs

# --- Postures and wave (body/controller.py) ---
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

# --- Manual control (scripts/teleop.py) ---
TELEOP_SPEED_SCALE_DEFAULT = 0.5  # fraction of the max speed
TELEOP_SPEED_SCALE_MIN = 0.1
TELEOP_SPEED_SCALE_STEP = 0.1

# === Gait =========================================================================================

# Alternating tripod gait: each group is 120 deg apart and swings together.
TRIPOD_A = ("RF", "RR", "LM")
TRIPOD_B = ("RM", "LR", "LF")

# Stride and swing bounds (PLACEHOLDER values)
TURN_RATE_MAX_DEG_S = 20.0  # max yaw rate; PLACEHOLDER, limited by STEP_LENGTH_MAX_M
STEP_LENGTH_MAX_M = 0.05  # max stride (stance travel per foot per step); PLACEHOLDER
STEP_HEIGHT_M = 0.03  # swing height; PLACEHOLDER
GAIT_FULL_LIFT_STRIDE_M = 0.01  # below this stride the swing height shrinks proportionally

GAIT_PERIOD_S = 1.0  # PLACEHOLDER, one full tripod cycle
GAIT_SWING_FRACTION = 0.5  # share of the cycle a foot is in swing (0 < f <= 0.5)
# Stance feet travel one stride in the stance share of the cycle, so the
# fastest body speed is the max stride over the stance time.
GAIT_MAX_SPEED_M_S = STEP_LENGTH_MAX_M / ((1.0 - GAIT_SWING_FRACTION) * GAIT_PERIOD_S)

# === Safety and timing ============================================================================

CONTROL_HZ = 50.0  # control tick, driven by wall-clock time
FALL_TILT_DEG = 50.0
FALL_CLEAR_TILT_DEG = 25.0  # a fallen body counts as upright again below this (hysteresis)
MAX_MESSAGE_AGE_S = 0.5  # older messages are dropped (a stale stop still runs)
WATCHDOG_TIMEOUT_S = 1.0  # walking/turning with no command or heartbeat
HEARTBEAT_HZ = 5.0
COMMAND_QUEUE_MAXSIZE = 8  # drop-oldest when full
STATUS_QUEUE_MAXSIZE = 64
BRIDGE_DRAIN_LIMIT = 4 * COMMAND_QUEUE_MAXSIZE  # max messages read per tick (flood guard)
BODY_START_TIMEOUT_S = 20.0  # waiting for the body process to report ready (PyBullet start-up)
BODY_SHUTDOWN_TIMEOUT_S = 3.0  # join time before terminate, then kill
PARENT_CHECK_TICKS = 5  # the body checks that its parent is alive every this many ticks

# Which body backend main.py starts. The environment can never select the real servos.
BACKENDS = ("sim", "dryrun", "servo")  # body backends main.py can start (servo = real Pi hardware)
_ENV_BACKEND = os.environ.get("HEXA_BACKEND", "sim")  # the Pi build sets dryrun (no pybullet)
ENV_REQUESTS_SERVO = _ENV_BACKEND == "servo"  # refused: servo needs --backend servo
BACKEND_DEFAULT = "sim" if ENV_REQUESTS_SERVO else _ENV_BACKEND  # never the real servos

# === Voice (router, brain, audio, speech models, chat) ============================================

# --- Router ---
ROUTER_THRESHOLD = 85  # rapidfuzz fuzz.ratio, 0-100
ROUTER_MAX_WORDS = 4  # after filler removal
ROUTER_FILLERS = ("please", "can you", "could you", "hexa", "arya", "hey", "now", "just")
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

# --- Brain (typed-text and voice front ends) ---
VOICE_WALK_MAX_S = 10.0  # a walk started by text/voice stops being kept alive after this
VOICE_PUMP_S = 0.05  # the voice worker serves heartbeats and statuses at least this often
# Speech after a command comes from the body's STATUS (brain/dialogue.py), never from assumption.
DIALOGUE_SPEAK_DONE = False  # say "Done." when an action finishes (silent by default)
DIALOGUE_THROTTLE_S = 2.0  # the same phrase is not repeated within this many seconds
STATUS_SUBSCRIBER_MAXSIZE = 64  # per subscriber; the oldest status is dropped when it is full
STATUS_HUB_POLL_S = 0.05  # the hub's single reader wakes this often to notice a shutdown

# --- Audio and listening ---
AUDIO_SAMPLE_RATE = 16000  # Vosk expects 16 kHz mono
AUDIO_BLOCKSIZE = 4000  # 250 ms
MIC_DEVICE: int | None = None
MIC_QUEUE_BLOCKS = 16  # the mic callback only copies into this bounded queue (about 4 s)
STT_READ_TIMEOUT_S = 0.2  # the STT thread wakes this often to notice a shutdown
STT_ERROR_BACKOFF_S = 0.5  # pause after a source or recognizer error, then carry on
SPEAKER_DEVICE: int | None = None
SPEAK_TAIL_S = 0.4  # STT stays gated this long after TTS ends
UTTERANCE_QUEUE_MAXSIZE = 8
# Listening mode. "ptt": push-to-talk, the recognizers get audio only between press() and
# release() (a voice "stop" works only while listening). "always": listen all the time, deaf
# while hexa speaks.
LISTEN_MODE = "ptt"
PTT_TAIL_S = 0.5  # after release the recognizer keeps listening this long, then the utterance ends
# Instant filler: no sentence ready this long after a chat question goes to the LLM -> play ONE
# pre-rendered filler (the next of these, in turn) so the user never waits in silence.
FILLER_DELAY_S = 0.8
FILLER_PHRASES = ("hmm", "let_me_think", "one_moment")

# --- Speech output (Piper) ---
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
    "sure": "Sure.",
    "done": "Done.",
    "something_wrong": "Something went wrong.",
    "cant_think": "I can't think right now.",
    "tell_me_after_stop": "Tell me after I stop.",
    # Persona and memory answers that need no name in them
    "persona_blocked": "Let's talk about something else.",
    "say_yes_to_confirm": "Say yes to confirm.",
    "forget_done": "Okay, I have forgotten you.",
    "forget_cancelled": "Okay, I will keep remembering you.",
    "remember_nothing": "I don't remember anything about you yet.",
    "mode_calm": "Okay, calm mode.",
    "mode_funny": "Funny mode on.",
    "mode_roast": "Roast mode, you asked for it.",
    "memory_off": "My memory is not working right now.",
}

# --- Speech models ---
# TO REPLACE THE SPEECH MODEL: change the directory name here (real names:
# https://alphacephei.com/vosk/models), then run scripts/fetch_models.sh. That is all.
# Short name -> directory under assets/vosk. The default is the one the robot uses and the only
# one fetch_models.sh downloads; the others are fetched only with --all-models.
VOSK_MODELS = {
    "us": "vosk-model-small-en-us-0.15",  # the chosen model (owner decision)
    "in": "vosk-model-small-en-in-0.4",  # Indian English: slower, not used, kept for comparison
}
VOSK_MODEL_DEFAULT = "us"
# Command grammar: a second recognizer on the same audio, limited to the router's phrases, aliases,
# stop words and fillers plus "[unk]". It rescues commands the free-text recognizer mishears
# ("sit" heard as "said"), but it also FORCES a match on ordinary speech, so its result is only
# used when it passes the guards in brain/stt_decision.py. Tune the numbers with stt_check.py.
STT_USE_GRAMMAR = True
STT_STOP_CONF = 0.5  # a grammar stop word at least this confident stops at once (safe side)
STT_GRAMMAR_CONF = 0.75  # mean word confidence a grammar command needs
STT_GRAMMAR_EXTRA_WORDS = 1  # free text may have this many words more than the grammar phrase
STT_STOP_EXTRA_WORDS = 2  # the same for a stop word (a false stop is harmless, a miss is not)
TRANSCRIPT_LOG = LOG_DIR / "transcripts.jsonl"  # one JSON line per final result (dataset)
VOSK_MODEL_PATH = VOSK_DIR / VOSK_MODELS[VOSK_MODEL_DEFAULT]
PIPER_BINARY = PIPER_DIR / "piper" / "piper"  # standalone binary, ONE long-lived subprocess
# TO REPLACE THE SPEAKING VOICE: change PIPER_VOICE (a path in https://huggingface.co/rhasspy/
# piper-voices without the extension), delete assets/phrases/*.wav, run scripts/fetch_models.sh.
PIPER_VOICE = "en/en_US/amy/low/en_US-amy-low"
PIPER_MODEL_PATH = PIPER_DIR / (PIPER_VOICE.rsplit("/", 1)[-1] + ".onnx")
# Optional isolation of Piper from the control loop (measured: unconstrained, its two compute
# threads take about 2.2 cores and double the body tick time on the dev laptop).
PIPER_NICE = 0  # extra niceness for the Piper process (0 = none)
PIPER_CPU_LIST: str | None = None  # taskset CPU list, e.g. "3"; None = any CPU

# --- Chat: the LLM is ONLY for conversation, never in the command path ---
# Real-model speed on the dev laptop (no AVX): qwen2.5:0.5b makes about 1.5 tokens/s, so chat is
# developed against FakeChat here and the real model is timed on the Pi 5. TO REPLACE THE MODEL:
# change OLLAMA_MODEL and pull it yourself: docker exec -it ollama ollama pull <name>
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:0.5b"  # 1.5b only after 0.5b works (owner)
OLLAMA_TIMEOUT_S = 20.0  # connect timeout and the longest silence between streamed tokens
OLLAMA_KEEP_ALIVE = "10m"  # how long Ollama keeps the model in memory after a reply
CHAT_MAX_TOKENS = 80  # hard cap on a reply (Ollama num_predict)
CHAT_TEMPERATURE = 0.7
CHAT_HISTORY_TURNS = 4  # rolling history: the last N (user, reply) pairs
CHAT_MAX_SENTENCE_CHARS = 160  # a longer sentence is split at a clause boundary for the TTS
CHAT_SYSTEM_PROMPT = (
    "You are Arya, a small friendly six-legged robot. Answer in one or two short sentences. "
    "Use plain spoken words only: no lists, no markdown, no emoji. "
    "You can walk, sit, stand, wave and turn, and nothing else; never claim other abilities "
    "and never pretend to have done a movement. If asked to move, say you will do it when "
    "asked directly."
)

# === Web (the phone page) =========================================================================

WEB_PORT = 8765
WEB_PIN: str | None = None  # None: HEXA_WEB_PIN, else a random 6-digit PIN printed at start
WEB_PIN_MAX_FAILURES = 5  # wrong PINs from one address within WEB_PIN_WINDOW_S lock it out ...
WEB_PIN_WINDOW_S = 60.0
WEB_PIN_LOCKOUT_S = 60.0  # ... for this long
WEB_DEADMAN_S = 0.3  # a moving robot gets `stop` if no move message arrives within this
WEB_CLIENT_SEND_HZ = 10.0  # the page repeats a held move at this rate (the server tells it)
WEB_WALK_SPEED = 0.5  # `speed` of a walk when the page sends none
WEB_TICK_S = 0.05  # server loop: deadman check and status relay
WEB_MIN_FORWARD_S = 0.05  # an unchanged move is forwarded as a heartbeat at most this often
WEB_MAX_MESSAGE_BYTES = 1024  # larger text messages are refused (room for a full `say`)
WEB_MAX_INVALID = 20  # this many rejected messages close the connection
WEB_PING_S = 5.0  # WebSocket keepalive ping (a vanished phone is noticed, then `stop`)
WEB_CLOSE_S = 1.0  # a client that ignores the close handshake is dropped after this
WEB_SENT_MEMORY = 64  # command seqs remembered to label statuses
# --- Phone page: hold-to-talk with the robot's microphone -------
WEB_PTT_MAX_S = 10.0  # the SERVER releases push-to-talk after this long, whatever the page does
WEB_SAY_MAX_CHARS = 120  # longest typed `say` text
WEB_SAY_MIN_INTERVAL_S = 0.3  # at most one typed `say` per this (a flood is refused)
WEB_EVENT_QUEUE_SIZE = 64  # events waiting for the phone: the oldest is dropped when full
# --- Phone page: the PHONE's microphone (main.py --audio-source phone) -------
# Format of the binary frames: raw little-endian PCM, 16 kHz mono (what Vosk wants), sent while a
# `ptt_press` that announced it is held. Anything else is refused, never converted.
WEB_AUDIO_RATE = AUDIO_SAMPLE_RATE
WEB_AUDIO_CHANNELS = 1
WEB_AUDIO_FORMAT = "s16le"
WEB_AUDIO_SAMPLE_BYTES = 2  # s16le
WEB_AUDIO_MIN_FRAME_MS = 20  # shorter frames are accepted but charged as this much
WEB_AUDIO_MAX_FRAME_MS = 100  # longer frames are refused
WEB_AUDIO_MAX_FRAME_BYTES = WEB_AUDIO_RATE * WEB_AUDIO_MAX_FRAME_MS // 1000 * WEB_AUDIO_SAMPLE_BYTES
WEB_AUDIO_MAX_RATE = 1.5  # at most this many seconds of audio per second (real time is 1.0) ...
WEB_AUDIO_BURST_S = 1.0  # ... plus this much saved up (a Wi-Fi stall delivers late frames at once)
WEB_AUDIO_QUEUE_BLOCKS = 128  # network source queue; the oldest block is dropped when it is full
WEB_AUDIO_ERROR_INTERVAL_S = 1.0  # at most one audio error message to the phone per this
WEB_PARTIAL_MIN_INTERVAL_S = 0.25  # at most one partial-text event to the phone per this

# === Persona (Arya), memory and context ===========================================================

# --- Persona routes ---
# Persona routes: handled by code in the brain, never by the LLM, and only after the stop
# check, so every command phrase above routes exactly as before. Matched on the normalized text
# after filler removal ("Arya, roast mode please" -> "roast mode").
# Vosk small US hears "Arya" as "aria" (measured with Piper speech: 0 of 24 as "arya", 20 of 24
# as "aria" or "are you"), so a LEADING "aria" is dropped like the filler "arya" ("aria sit
# down"). Only at the start, so a person called Aria ("my name is aria") keeps the name. The
# command grammar lists "arya" (through the fillers) and hears it every time; "aria" is NOT in
# the grammar.
ROUTER_WAKE_VARIANTS = ("aria",)
# "calm" is heard as "come" (the free text said "aria come mode" 3 times of 3).
PERSONA_MODE_HEARD_AS = {"come": "calm"}
# The ONE exception to "a stop word anywhere stops": these whole utterances switch back to calm
# mode instead (they are about the roast, not the robot). "stop roasting and walk" is still a stop.
PERSONA_STOP_ROAST_PHRASES = ("stop roasting", "stop the roast", "stop roasting me", "stop roast",
                              "no more roasting", "no more roast")
PERSONA_FORGET_PHRASES = ("forget me", "forget about me")
PERSONA_RECALL_PHRASES = ("what do you remember about me", "what do you know about me",
                          "what do you remember", "do you remember me")
PERSONA_YES_WORDS = ("yes", "yeah", "yep", "yup", "yes please", "yes forget me", "confirm",
                     "yes i am sure", "i am sure")
PERSONA_NO_WORDS = ("no", "nope", "cancel", "never mind", "no thanks", "dont")
# "I am X" is only a name when X is not one of these (or an -ing word): "I am hungry" is chat.
ROUTER_NAME_STOPWORDS = (
    "a", "an", "the", "not", "so", "very", "too", "just", "here", "there", "back", "home", "fine",
    "good", "great", "okay", "ok", "well", "bad", "sad", "happy", "angry", "tired", "bored", "sick",
    "hungry", "thirsty", "sorry", "sure", "ready", "done", "late", "early", "new", "old", "alone",
    "confused", "lost", "cold", "hot", "busy", "free", "right", "wrong", "going", "coming", "in",
    "on", "at", "to", "from", "with", "about", "afraid", "scared", "curious", "excited", "nervous",
    "worried", "serious", "joking", "kidding", "listening", "talking", "later", "tomorrow", "maybe",
    "now", "please", "anything", "whatever", "something", "nothing", "everything", "yes", "no",
    "guest", "arya", "hexa", "robot", "nobody", "somebody", "someone", "stop", "forget", "sit",
    "stand", "walk", "wave", "turn", "go", "back", "up", "down", "left", "right", "forward",
)

# The persona only changes how Arya TALKS. Commands, the router and safety are identical in every
# mode. The word lists and the character sheet live in data/persona/*.toml, not in code.
PERSONA_FILE = PERSONA_DIR / "arya.toml"
PERSONA_LIMITS_FILE = PERSONA_DIR / "limits.toml"
PERSONA_MODES = ("calm", "funny", "roast")
PERSONA_DEFAULT_MODE = "calm"
PERSONA_EXAMPLES_IN_PROMPT = 2  # example exchanges in the system prompt (it must stay short)
PERSONA_OUTPUT_CHECK = True  # every reply sentence is checked before the TTS (hard limits)
GUEST_NAME = "guest"  # the speaker until someone says their name
MOOD_STATES = ("neutral", "happy", "bored", "busy")
MOOD_HAPPY_S = 120.0  # a greeting keeps Arya happy this long
MOOD_BORED_AFTER_S = 300.0  # nobody spoke for this long: bored (until the next utterance)
MOOD_BUSY_COMMANDS = 5  # this many motion commands within MOOD_BUSY_WINDOW_S: busy
MOOD_BUSY_WINDOW_S = 60.0
# Memory (SQLite, owned by the brain only; schema version in `meta`)
MEMORY_MAX_FACTS_PER_PERSON = 30  # the oldest fact is dropped when a new one would exceed this
MEMORY_MAX_PEOPLE = 50
MEMORY_MAX_EMBEDDINGS_PER_PERSON = 10  # face samples kept per person (enrolment collects fewer)
MEMORY_NAME_MAX_CHARS = 24
MEMORY_FACT_MAX_CHARS = 100
MEMORY_SUMMARY_MAX_CHARS = 300
# Context builder: approximate tokens by characters, trimmed oldest-first (brain/context.py)
CHAT_CONTEXT_MAX_TOKENS = 600  # the whole prompt except the model's reply
CONTEXT_CHARS_PER_TOKEN = 4
CONTEXT_MAX_FACTS = 6  # facts about the speaker put into one prompt
CONTEXT_MAX_HISTORY_TURNS = 6
# Memory extractor: after the conversation goes idle, ask the chat backend for facts (JSON)
EXTRACTOR_IDLE_S = 45.0
EXTRACTOR_MIN_TURNS = 2  # fewer user turns than this are not worth a model call
EXTRACTOR_MAX_FACTS_PER_RUN = 3
EXTRACTOR_MAX_TURNS = 8  # the last N turns are shown to the extractor
EXTRACTOR_POLL_S = 1.0
# "forget me" asks for a yes first; the answer must come within this long
FORGET_CONFIRM_S = 20.0

# === Vision (the webcam process, vision/; events only, never images) ==============================

VISION_DIR = ASSETS_DIR / "vision"  # models: scripts/fetch_vision_models.py (gitignored)
VISION_YUNET_FILE = VISION_DIR / "face_detection_yunet_2023mar.onnx"
VISION_SFACE_FILE = VISION_DIR / "face_recognition_sface_2021dec.onnx"
VISION_CAMERA_INDEX = 0  # cv2.VideoCapture device index (--camera on the check scripts)
VISION_CAPTURE_WIDTH = 640  # what the camera is asked for; frames are downsized for detection
VISION_CAPTURE_HEIGHT = 480
VISION_FPS = 4.0  # frames per second the loop reads and detects ...
VISION_PAUSED_FPS = 1.0  # ... and while pause_event is set (the LLM is generating)
VISION_FPS_RANGE = (0.5, 15.0)  # bounds for either rate
VISION_DETECT_WIDTH = 320  # frames are resized to this width before detection (aspect kept)
VISION_SCORE_THRESHOLD = 0.7  # YuNet face score; lower finds more faces and more false ones
VISION_NMS_THRESHOLD = 0.3
VISION_TOP_K = 5  # at most this many faces per frame
VISION_CV_THREADS = 2  # cv2.setNumThreads in the vision process (the LLM needs the other cores)
VISION_HFOV_DEG = 60.0  # horizontal field of view of the webcam; measure yours (spec sheets lie)
VISION_TRACK_IOU = 0.3  # a detection continues a track when the boxes overlap at least this much
VISION_APPEAR_FRAMES = 2  # a face must be seen in this many frames in a row before person_appeared
VISION_LOST_FRAMES = 4  # a person is gone after this many frames in a row without their face
VISION_BEARING_MAX_HZ = 2.0  # face_bearing events per second, per track ...
VISION_PAUSED_BEARING_HZ = 0.5  # ... and while pause_event is set
VISION_EVENT_QUEUE_SIZE = 32  # bounded; the oldest event is dropped when the consumer is behind
VISION_COMMAND_QUEUE_SIZE = 16  # brain -> vision commands (set_known_faces, enroll, ...)
VISION_RETRY_BACKOFF_S = (1.0, 30.0)  # camera error: first retry delay, doubled up to the second
VISION_STATS_S = 5.0  # the vision process reports fps, detection time, CPU and RAM this often
VISION_MATCH_THRESHOLD = 0.363  # SFace cosine score for "same person" (>=): the value OpenCV's own
# face_detect sample and the OpenCV Zoo use (their L2 threshold is 1.128); tune with face_check.py
VISION_VOTE_WINDOW = 5  # a name needs VOTE_NEEDED of the last VOTE_WINDOW matches to agree
VISION_VOTE_NEEDED = 3
VISION_UNKNOWN_AFTER = 5  # good-quality looks without a name, then person_unknown (once per track)
VISION_RECHECK_S = 5.0  # an identified track is matched again this often (new/unnamed: every frame)
VISION_MIN_FACE_PX = 48  # quality gate: faces with a shorter box side (detection frame) are skipped
VISION_BLUR_MIN = 30.0  # quality gate: Laplacian variance of the 96x96 grey crop, below: blurry
VISION_FRONTAL_MAX = 0.30  # quality gate: nose offset from the eye midpoint / eye distance
VISION_ENROLL_SAMPLES = 5  # enrolment collects this many good crops (kept as embeddings only) ...
VISION_ENROLL_TIMEOUT_S = 15.0  # ... within this long
VISION_ENROLL_GAP_S = 0.4  # at least this long between two samples, so they differ a little
VISION_ENROLL_LOST_FRAMES = 3  # enrolment fails when the face is missing this many frames in a row
VISION_ENROLL_MULTI_FRAMES = 2  # ... or when more than one face is seen this many frames in a row

# === Hardware (the Pi servo backend; board-level numbers only) ====================================

# Per-joint numbers (channel, centre, sign, offset, pulse window, calibrated) are NOT here: they
# live in the calibration file data/calibration/servo_cal.json (body/servo_calibration.py).
SERVO_CAL_FILE = DATA_DIR / "calibration" / "servo_cal.json"
SERVO_PWM_HZ = 50  # PCA9685 and GPIO PWM frequency (analog servo: TowerPro MG995)
PCA9685_I2C_BUS = 1  # /dev/i2c-1 (SDA1 = GPIO2 = pin 3, SCL1 = GPIO3 = pin 5)
PCA9685_ADDRESS = 0x40  # one board, all address jumpers open
PCA9685_OE_GPIO: int | None = None  # Pi GPIO wired to the PCA9685 OE pin (None: OE not wired)
GPIO_CHIP_PATH = "/dev/gpiochip4"  # header GPIO chip of the Pi 5 (RP1); check with `gpiodetect`
GPIO_PWM_CHIP = 2  # /sys/class/pwm/pwmchipN of the Pi 5 (RP1); needs dtoverlay=pwm-2chan
GPIO_PWM_PINS = (12, 13)  # hardware PWM0 / PWM1 = pins 32 / 33 (the two rear coxas)
SERVO_US_PER_DEG = 2000.0 / 180.0  # MG995: 500..2500 us over about 180 deg (PiHexa18 reference)
SERVO_PULSE_ABS_MIN_US = 450  # nothing is ever sent outside this window, whatever the file says
SERVO_PULSE_ABS_MAX_US = 2550
SERVO_SLEW_DEG_S = 120.0  # an output never moves faster than this (measured in wall time)
SERVO_SLEW_MAX_DT_S = 0.1  # one slew step covers at most this much time (a stalled loop)
SERVO_STAGGER_S = 0.3  # delay between legs when the servos are attached (one leg at a time)
SERVO_IDLE_RELEASE_S = 10.0  # sitting with no command this long releases every channel
SERVO_SHUTDOWN_SIT_TIMEOUT_S = 4.5  # a signal shutdown sits for at most this long, then releases
SERVO_SIT_TOLERANCE_DEG = 1.0  # "at the sit pose" for the idle release
SERVO_JOG_STEP_US = 5  # servo_jog fine step (the coarse step is SERVO_JOG_COARSE_US)
SERVO_JOG_COARSE_US = 25
TERMINAL_KEY_HOLD_S = 0.6  # terminal teleop: a movement key counts as held this long after a press
DRYRUN_LOG_FILE = LOG_DIR / "dryrun.log"  # per-leg joint targets the gait would send (tail -f it)
DRYRUN_LOG_HZ = 2.0  # at most this many lines per second while the targets are changing ...
DRYRUN_LOG_EPS_DEG = 0.5  # ... a change smaller than this on every joint is not "moving"
DRYRUN_REST_S = 0.4  # ... and one "at rest" line once the targets stopped changing this long
DRYRUN_LOG_MAX_BYTES = 1_000_000  # dryrun.log rotates at this size, keeping DRYRUN_LOG_BACKUPS
DRYRUN_LOG_BACKUPS = 2


def clamp(value: float, low: float, high: float) -> float:
    """Return *value* limited to the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def mount_yaw_rad(leg: str) -> float:
    """Math yaw (counter-clockwise from +X, radians) of a leg's mount.

    The only place the clockwise mount-angle convention is converted.
    """
    return -math.radians(LEG_MOUNT_ANGLES_DEG[leg])
