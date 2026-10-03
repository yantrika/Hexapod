# plan.md — hexa revised architecture and build plan

Status: **plan only, no code written.** Work proceeds one step at a time, each step gated on approval. Where this file conflicts with older docs, this file wins. Revision 2 applies the owner's answers to the first round of open questions (section 12).

## 1. Goal

A talking hexapod: a fully offline voice loop (Vosk STT, rule-based router with Ollama chat fallback, Piper TTS) that drives a 6-leg, 18-DOF body. It is built and verified in PyBullet first, then moved to a Raspberry Pi 5 with real servos by swapping one backend class. No cloud services, no paid tools.

## 2. Architecture

Two OS processes plus threads inside the voice/brain process. They share nothing except two `multiprocessing` queues.

```
 ┌───────────────────── Process 1: voice/brain (main.py) ─────────────────────┐
 │                                                                            │
 │  [mic] → STT thread ──utterance_q──▶ Worker thread ──▶ router ──┐          │
 │            ▲  (drops results          (router / chat)           │ chat     │
 │            │   while `speaking`                │                ▼          │
 │            │   or in tail)                     │          Ollama (HTTP)    │
 │            │                                   │ reply text                │
 │       speaking: threading.Event ◀── TTS thread ◀── tts_q (clearable)       │
 │                                      (Piper subprocess → speaker)          │
 │                                                                            │
 │  bridge.send: Worker/heartbeat ─▶ command_queue ┐   ┌◀ status_queue ─ Status│
 │  (stop also sets stop_event) ────▶ stop_event ──┤   │                       │
 │  Status thread ◀──────────────────────────────────┼───┘   listener → tts_q │
 └──────────────────────────────────────────────────┼───────────────────────┘
                                          mp.Queue  │  mp.Queue
 ┌──────────────────────────────────────────────────▼───────────────────────┐
 │ Process 2: body (body/process.py)       command_queue (brain → body)      │
 │                                         status_queue  (body → brain)      │
 │  fixed 50 Hz control tick (wall-clock):                                   │
 │   drain queue → drop stale → arbitrate (stop > latest motion) →           │
 │   watchdog check → controller → gait → kinematics (IK) →                  │
 │   backend.set_joint_targets() → backend.advance(wall dt) → status out     │
 │                                                                           │
 │   HexapodBackend (ABC) ── SimBackend (PyBullet, 240 Hz, GUI or DIRECT)    │
 │                        └─ ServoBackend (Pi 5, Step 11)                    │
 └───────────────────────────────────────────────────────────────────────────┘
```

Rules:
- Voice/brain never touches joint angles; body never touches audio or text.
- Inside process 1, threads talk only via `queue.Queue` (plus the one `speaking` `threading.Event`).
- Nothing outside `body/sim_backend.py` / `body/servo_backend.py` knows which backend is loaded.
- `main.py` runs voice/brain in the main process, spawns the body process, and shuts both down on exit; if the body dies, the brain announces an error and exits.

Threads in process 1:

| Thread | Reads | Writes | Job |
|---|---|---|---|
| `stt` | mic stream | `utterance_q` | Vosk recognition; discards results while gated (section 4, Step 8) |
| `worker` | `utterance_q` | `command_queue`, `tts_q` | router; on no match, Ollama chat |
| `tts` | `tts_q` | speaker, `speaking` Event | Piper subprocess + playback; `clear()` cancels |
| `status` | `status_queue` | `tts_q`, posture cache | turns body status into speech |
| `heartbeat` | posture cache | `command_queue` | sends `heartbeat` at `HEARTBEAT_HZ` while the body is walking or turning |

Why Piper: it is a standalone binary that bundles its own runtime, so we need no Python ML runtime (onnxruntime, torch) in our venv. It stays a subprocess. Future option, **not now**: whisper.cpp tiny/base for the chat path if Vosk accuracy is poor on open conversation.

## 3. Message schema

Defined once in `bridge.py` as frozen dataclasses (picklable, so `mp.Queue` works). `timestamp` is `time.monotonic()`, which is system-wide on Linux, so it is comparable across the two processes on one host.

```python
@dataclass(frozen=True)
class Command:            # brain -> body, on command_queue
    action: str           # stand | sit | walk | turn | wave | stop | heartbeat
    params: dict[str, Any]
    seq: int              # brain-side counter, strictly increasing
    timestamp: float

@dataclass(frozen=True)
class Status:             # body -> brain, on status_queue
    status: str           # accepted | rejected | done | busy | fallen | error
    ref_seq: int | None   # seq of the command this answers, None if unsolicited
    detail: dict[str, Any]  # rejected: {"reason": ...}; error: {"message": ...}
    seq: int              # body-side counter
    timestamp: float
```

Command params:

| action | params | notes |
|---|---|---|
| `walk` | `{"direction": "fwd"\|"back", "speed": 0.0-1.0}`, plus optional `"strafe"` and `"yaw"` (floats in [-1.0, 1.0]) | continuous until `stop`, a new motion command, or watchdog. See "Walk with strafe and yaw" below |
| `turn` | `{"direction": "left"\|"right", "angle_deg": 1-180}` | finishes itself, then `done` |
| `stand`, `sit`, `wave` | `{}` | `wave` finishes itself, then `done` |
| `stop` | `{}` | preempts everything |
| `heartbeat` | `{}` | refreshes the watchdog only; never answered |

**Walk with strafe and yaw (Step 5b, backward compatible).** `walk` gains two optional params: `strafe` (+1 = left) and `yaw` (+1 = counter-clockwise), each in [-1.0, 1.0] and scaled by `speed` like the forward component. `direction` is required only when neither `strafe` nor `yaw` is given; with them it may be omitted (no forward component), so `{"strafe": 1.0, "speed": 0.5}` is a pure sideways walk and `{"direction": "fwd", "strafe": 1.0, "yaw": 0.5}` combines all three. A message without them behaves exactly as before. The controller maps them to one `set_velocity(vx, vy, yaw_rate)` and clamps to the max speed and max yaw rate (`gait.limit_command`); neither the bridge nor any front end clamps. Out-of-range or non-finite `strafe`/`yaw`/`speed` are `rejected(invalid_params)`. Such a walk is continuous like any walk: it needs heartbeats, the watchdog ramps it to zero, and latest-wins applies. A walk with all components zero ramps a walk down to a halt (the body then reports `done` for the walk), which is how the control window handles key release. The router and the voice never send `strafe` or `yaw`; only manual front ends (the control window, `bridge_cli`) do.

Examples:

```python
Command("walk", {"direction": "fwd", "speed": 0.5}, seq=41, timestamp=812.304)
Command("stop", {}, seq=42, timestamp=813.020)
Status("accepted", ref_seq=41, detail={}, seq=17, timestamp=812.322)
Status("rejected", ref_seq=43, detail={"reason": "already_in_state", "state": "sitting"}, seq=18, timestamp=815.1)
Status("done",     ref_seq=44, detail={}, seq=19, timestamp=818.9)
Status("fallen",   ref_seq=None, detail={"roll_deg": 71.0}, seq=20, timestamp=820.2)
```

Rejection reasons (closed set): `stale`, `superseded`, `already_in_state`, `invalid_params`, `invalid_state` (e.g. `walk` while sitting), `unknown_action`, `fallen`. `stop` is never rejected: it is idempotent and always answered `accepted` then `done`. `busy` means "valid, but a sit/stand/wave transition is in progress" (only `stop` interrupts a transition). The brain speaks from the status it receives (for example "I'm already sitting"), never from assumption.

**Bridge handle.** `bridge.make_bridge()` returns one object holding `command_queue`, `status_queue` and a shared `stop_event` (`multiprocessing.Event`). The brain only calls `bridge.send(command)`; for `action == "stop"` it sets `stop_event` **and** enqueues the normal message. The body only calls `bridge.drain()` / `bridge.report(status)`.

## 4. Safety model

1. **Clamps, two limit tiers**: `config.clamp` plus `SPEED_*`, `TURN_*`, `JOINT_HARD_LIMITS_DEG` and `GAIT_SOFT_LIMITS_DEG` are the only bounds.
   - *Hard limits* (servo range, ±90° in the clean joint frame) are enforced in one clamp layer: `HexapodBackend.set_joint_targets()` is a concrete template method that clamps and then calls the backend-specific `_apply()`, so both backends share it. Kinematics also returns `None` for solutions outside the hard limits.
   - *Soft gait limits* (coxa about ±30°) are used by the gait planner only, to keep legs from colliding with neighbours.
   - The body validates every command on ingress (out-of-range params are clamped or rejected `invalid_params`, per action). The brain does not need to be trusted.
2. **Stale-message drop**: any message older than `MAX_MESSAGE_AGE_S` is dropped (command: `rejected(stale)`; heartbeat: silently). **A stale `stop` is still honoured**; stopping late beats not stopping.
3. **Drain and arbitration, every control tick**:
   - Drain `command_queue` completely.
   - **Stop fast path**: at the start of each tick the body checks `stop_event`; if set, it clears the event, then stops immediately, before draining. The queued `stop` message arrives later and is handled as an idempotent duplicate.
   - If any `stop` is present in the drained batch: execute it now, discard every other message in the batch (each gets `rejected(superseded)`), reply `accepted`, then `done`.
   - **Stop semantics**: stop interrupts any action, including a sit/stand/wave transition, and **holds the current pose** (joint targets frozen, gait phase retained). The body enters the `holding` state; it does not return to stand.
   - Otherwise motion commands (`walk`, `turn`, `stand`, `sit`, `wave`) are **latest-wins**; earlier ones get `rejected(superseded)`.
   - While a `sit`/`stand`/`wave` transition runs, any new motion command is answered `busy` (not queued); only `stop` interrupts it.
   - Heartbeats only refresh the watchdog.
4. **Watchdog**: while walking or turning, if no command or heartbeat arrives within `WATCHDOG_TIMEOUT_S`, the body stops itself (same hold-pose behaviour as `stop`) and emits `done` with `detail={"reason": "watchdog"}`. The brain's heartbeat thread sends at `HEARTBEAT_HZ` while it believes the body is moving, so a hung or dead brain stops the robot.
5. **Fall handling**: if body tilt exceeds `FALL_TILT_DEG`, the body halts the gait, emits `fallen`, and rejects all motion commands (`rejected(fallen)`) except `stop` until reset (sim: re-spawn; hardware: manual).
6. **Queues are small and bounded.** `command_queue` has a small `COMMAND_QUEUE_MAXSIZE`; when full, `bridge.send` drops the **oldest** queued message (motion commands are latest-wins anyway) and enqueues the new one. `stop` cannot be lost this way because `stop_event` carries it independently of the queue. Heartbeats are skipped, not queued, when the queue is full.

## 5. Robot spec and frame convention

- **Body frame**: +X forward, +Y left, +Z up (right-handed). Metres, radians internally; `_DEG` suffix for degrees.
- **Mount angles** are measured **clockwise as seen from above, 0° = +X** (forward). Math yaw (counter-clockwise, as PyBullet/URDF use) is `yaw = -mount_angle`. `config.py` provides one helper, `mount_yaw_rad(leg)`, so this conversion lives in exactly one place. This replaces the old counter-clockwise convention in `config.py`.
- 6 legs × 3 joints (coxa yaw, femur pitch, tibia pitch) = 18 DOF. Leg order is clockwise from front-right: `RF, RM, RR, LR, LM, LF`.

| Leg | RF | RM | RR | LR | LM | LF |
|---|---|---|---|---|---|---|
| Mount angle (° clockwise from +X) | 30 | 90 | 150 | 210 | 270 | 330 |

Left mirrors right: `angle(L·) = 360 − angle(R·)` for the same position (RF 30 ↔ LF 330, RM 90 ↔ LM 270, RR 150 ↔ LR 210). A unit test asserts this.

**One conversion point.** `config.mount_yaw_rad(leg)` is the only place the clockwise convention becomes math yaw. The leg-frame to body-frame transform (`kinematics.leg_to_body()`) and `scripts/generate_urdf.py` both call it; nothing else may negate or offset a mount angle. A test (Step 1) asserts that RF's neutral foot target lands on the **right** side of the body (x > 0, y < 0) and LF's on the left (x > 0, y > 0), with RM/LM at y of opposite sign and x ≈ 0.

- **Tripod groups**: A = `RF, RR, LM`; B = `RM, LR, LF`. Each group is spaced 120° apart and swings together; A and B are 180° out of phase.
- **Dimensions** (from the current spec): body radius 0.12 m, coxa 0.05 m, femur 0.09 m, tibia 0.13 m.
- **Knee branch**: IK always returns the **knee-up** solution (knee above the line from hip to foot).
- **Joint frame**: sim and kinematics use a clean frame where 0 = neutral and there are no offsets or per-side sign flips. Servo centre, sign and offset (the tibia included) live in a calibration table inside `body/servo_backend.py` only (Step 11).
- **Joint limits, two tiers**: *hard* limits ±90° per joint (hobby servos span about 180°), enforced in the backend clamp layer; *soft* gait limits (coxa ±30°) used by the gait planner.

## 6. Folder structure

Flat top-level packages (no `src/`). Python modules are `snake_case`.

```
hexa/
├── AGENTS.md  CLAUDE.md  README.md  plan.md
├── pyproject.toml            # ruff, mypy, pytest config (Step 0)
├── requirements*.txt         # existing: base, dev, sim, pi
├── config.py                 # single source of truth (section 7)
├── bridge.py                 # Command/Status dataclasses, Bridge handle (queues + stop_event), validation
├── main.py                   # launcher: spawns body process, runs voice/brain; --headless, --no-voice
├── body/
│   ├── backend.py            # HexapodBackend ABC + hard-limit clamp layer
│   ├── sim_backend.py        # PyBullet implementation (+ headless DIRECT mode)
│   ├── servo_backend.py      # Pi servo implementation + calibration table (Step 11)
│   ├── kinematics.py         # leg FK/IK, leg_to_body() (pure functions, numpy)
│   ├── poses.py              # STAND_ANGLES (all zero) and SIT_ANGLES from IK
│   ├── urdf.py               # build_urdf(): URDF text generated from config
│   ├── gait.py               # tripod gait: phase -> foot targets (pure)
│   ├── controller.py         # sit/stand/walk/turn/wave/stop state machine
│   ├── arbitration.py        # drain, stale drop, stop priority, latest-wins (pure, testable)
│   ├── clock.py              # injectable monotonic clock + fixed-rate accumulator
│   └── process.py            # body process entry point, 50 Hz loop, watchdog, status out
├── brain/
│   ├── router.py             # fuzzy command router (pure)
│   ├── chat.py               # Ollama client
│   └── dialogue.py           # status -> spoken phrase; heartbeat sender; posture cache
├── voice/
│   ├── audio.py              # device selection, mic stream
│   ├── stt.py                # Vosk thread + self-hearing gate
│   ├── tts.py                # Piper subprocess wrapper
│   └── playback.py           # TTS playback queue thread: say(), clear(), `speaking` Event
├── assets/{urdf,vosk,piper}/ # urdf generated by scripts/generate_urdf.py; models gitignored
├── scripts/
│   ├── fetch_models.sh       # Vosk + Piper downloads (Step 7/8)
│   ├── generate_urdf.py      # writes assets/urdf/hexapod.urdf via body/urdf.py (--check verifies)
│   ├── sim_demo.py           # holds stand/sit in the sim (Step 2)
│   └── walk_demo.py          # forward, turn in place, strafe at the control rate (Step 3)
├── tests/                    # test_<module>.py mirroring the packages; fakes.py, walk_harness.py
└── logs/
```

## 7. `config.py` constants

Existing names that are replaced: `BODY_COMMAND_QUEUE`, `BODY_TELEMETRY_QUEUE` (queues are objects, not named), `COMMAND_TIMEOUT_S` (becomes `MAX_MESSAGE_AGE_S` + `WATCHDOG_TIMEOUT_S`), `JOINT_LIMITS` in radians (becomes `JOINT_HARD_LIMITS_DEG` + `GAIT_SOFT_LIMITS_DEG`), and `LEG_MOUNT_ANGLES_DEG` values. Defaults marked **P** are placeholders to tune.

**Filesystem**: `PROJECT_ROOT`, `ASSETS_DIR`, `URDF_DIR`, `URDF_PATH = URDF_DIR/"hexapod.urdf"`, `VOSK_DIR`, `PIPER_DIR`, `LOG_DIR`, `LOG_FILE`, `LOG_LEVEL = "INFO"`.

**Geometry**
- `BODY_RADIUS = 0.12`, `COXA_LENGTH = 0.05`, `FEMUR_LENGTH = 0.09`, `TIBIA_LENGTH = 0.13`
- `LEG_NAMES = ("RF","RM","RR","LR","LM","LF")`, `JOINTS_PER_LEG = ("coxa","femur","tibia")`, `DOF = 18`, `JOINT_NAMES` (flat leg-major order, e.g. `RF_coxa`)
- `LEG_MOUNT_ANGLES_DEG = {"RF":30,"RM":90,"RR":150,"LR":210,"LM":270,"LF":330}` (clockwise from +X)
- `TRIPOD_A = ("RF","RR","LM")`, `TRIPOD_B = ("RM","LR","LF")`
- `KNEE_BRANCH = "up"`
- helper `mount_yaw_rad(leg)`

**Limits and clamps**
- `JOINT_HARD_LIMITS_DEG = {"coxa": (-90, 90), "femur": (-90, 90), "tibia": (-90, 90)}` (servo range in the clean frame)
- `GAIT_SOFT_LIMITS_DEG = {"coxa": (-30, 30), "femur": (-90, 90), "tibia": (-90, 90)}` (**P**; femur/tibia tuned in Steps 2–3; each must lie inside the hard limits)
- `SPEED_MIN = 0.0`, `SPEED_MAX = 1.0`
- `TURN_ANGLE_MIN_DEG = 1`, `TURN_ANGLE_MAX_DEG = 180`, `TURN_RATE_MAX_DEG_S = 20` (**P**; the gait's max yaw rate, limited by the max stride)
- `STEP_LENGTH_MAX_M = 0.05` (**P**; max stride), `STEP_HEIGHT_M = 0.03` (**P**; swing height)
- `BODY_HEIGHT_SIT = 0.06` (**P**), `BODY_HEIGHT_STAND = TIBIA_LENGTH = 0.13` (the zero pose is the stand pose, so the neutral foot hangs `TIBIA_LENGTH` below the body plane)
- `FALL_TILT_DEG = 50`
- helper `clamp(value, low, high)`

**Simulation model** (all **P**): `BODY_THICKNESS_M = 0.04`, `BODY_MASS_KG = 0.6`, `LINK_RADIUS_M = 0.008`, `LINK_MASS_KG = {coxa 0.03, femur 0.05, tibia 0.05, foot 0.01}`, `FOOT_RADIUS_M = 0.01`, `GROUND_FRICTION = 1.0`, `FOOT_FRICTION = 1.0`, `JOINT_MAX_FORCE_NM = 3.0`, `JOINT_MAX_VELOCITY_RAD_S = 6.0`, `JOINT_POSITION_GAIN = 0.3`, `JOINT_VELOCITY_GAIN = 0.3` (low sim damping; 1.0 acted as viscous drag and cost about 20 % of walking speed), `SIM_GRAVITY = 9.81`, `SIM_SPAWN_CLEARANCE_M = 0.002`

**Timing and bridge**
- `PHYSICS_HZ = 240`, `CONTROL_HZ = 50`
- `MAX_PHYSICS_CATCHUP_STEPS = 12` (caps catch-up after a stall to about 50 ms of sim time)
- `GAIT_PERIOD_S = 1.0` (**P**), `GAIT_SWING_FRACTION = 0.5` (0 < f ≤ 0.5), `GAIT_MAX_SPEED_M_S = STEP_LENGTH_MAX_M / ((1 − GAIT_SWING_FRACTION) × GAIT_PERIOD_S) = 0.1` (derived)
- `MAX_MESSAGE_AGE_S = 0.5`
- `WATCHDOG_TIMEOUT_S = 1.0`, `HEARTBEAT_HZ = 5.0`
- `COMMAND_QUEUE_MAXSIZE = 8` (drop-oldest), `STATUS_QUEUE_MAXSIZE = 64`
- `SIM_HEADLESS = False` (overridden by `--headless`), `SIM_REALTIME = True`

**Router**
- `ROUTER_THRESHOLD = 85` (rapidfuzz `fuzz.ratio`, 0–100)
- `ROUTER_MAX_WORDS = 4` (after filler removal)
- `ROUTER_FILLERS = ("please","can you","could you","hexa","hey","now","just")`
- `STOP_WORDS = ("stop","halt","freeze")`
- `ROUTER_PHRASES`: phrase → `(action, params)`, e.g. `"walk forward"`, `"go forward"`, `"walk back"`, `"turn left"`, `"turn right"`, `"sit down"`, `"stand up"`, `"wave"`

**Audio**
- `AUDIO_SAMPLE_RATE = 16000`, `AUDIO_BLOCKSIZE = 4000` (250 ms; was 8000), `MIC_DEVICE = None`, `SPEAKER_DEVICE = None`
- `SPEAK_TAIL_S = 0.4` (STT stays gated this long after TTS ends)
- `UTTERANCE_QUEUE_MAXSIZE = 8`, `TTS_QUEUE_MAXSIZE = 8`, `TTS_CLEAR_MAX_S = 0.1`

**Paths and models**
- `VOSK_MODEL_PATH = VOSK_DIR/"vosk-model-small-en-us-0.15"`
- `PIPER_BINARY = "piper"`, `PIPER_MODEL_PATH = PIPER_DIR/"en_US-amy-low.onnx"`
- `OLLAMA_URL = "http://127.0.0.1:11434"`, `OLLAMA_MODEL = "qwen2.5:1.5b"`, `OLLAMA_TIMEOUT_S = 20`, `CHAT_MAX_TOKENS = 80`, `CHAT_HISTORY_TURNS = 4`

**Test tolerances**: `IK_TOLERANCE_M = 1e-4`, `WALK_TEST_MAX_TILT_DEG = 10`, `WALK_TEST_HEIGHT_TOL_M = 0.02`, `WALK_TEST_SPEED_TOL = 0.25`, `WALK_TEST_POSITION_DRIFT_M = 0.05`, `WALK_TEST_HEADING_DRIFT_DEG = 5`.

**Not in `config.py`**: servo calibration (per-joint centre, sign, offset, channel, pulse range) lives in a table inside `body/servo_backend.py` (Step 11). `config.py` stays hardware-agnostic.

## 8. Build steps

Each step is small, ends green (`pytest`, `ruff check .`, `mypy .`), and waits for approval before the next. "Verify" commands run from the repo root with `.venv` active.

### Step 0 — Config and tooling
- **Goal**: revised `config.py` and a working lint/type/test setup.
- **Files**: `config.py`, `pyproject.toml`, `tests/test_config.py`.
- **Verify**: `pytest tests/test_config.py`, `ruff check .`, `mypy .`
- **Tests**: left/right mount angles mirror; tripod groups partition the 6 legs and are each 120° spaced; `DOF == 18`; soft gait limits lie inside the hard limits, and hard limits are ±90°; `mount_yaw_rad` matches the clockwise convention for all 6 legs.
- **Done when**: all three commands pass and no constant from section 7 is missing.

### Step 1 — Kinematics
- **Goal**: pure 3-DOF leg FK/IK in the leg frame.
- **Files**: `body/kinematics.py`, `tests/test_kinematics.py`.
- **Verify**: `pytest tests/test_kinematics.py`
- **Tests**:
  - `FK(IK(point)) ≈ point` within `IK_TOLERANCE_M` on 100 seeded random points, sampled in a box around the neutral stance foot position that is known to be reachable (this replaces `IK(FK(angles)) == angles`, which is not unique).
  - IK always returns the knee-up branch (check knee height above the hip-foot line for every sampled point).
  - Unreachable points (too far, too close, below the joint limits) return `None`.
  - Returned angles always respect `JOINT_HARD_LIMITS_DEG`.
  - `leg_to_body()` check: RF's neutral foot target has x > 0 and y < 0 (right side), LF's has x > 0 and y > 0, RM/LM have opposite y signs, and each left foot is the mirror image (y negated) of its right counterpart.
- **Done when**: tests pass, IK never raises on any finite input, and the RF-on-the-right test passes.

### Step 2 — Backend ABC, URDF, PyBullet stand/sit
- **Goal**: robot appears in PyBullet and holds stand and sit poses.
- **Files**: `body/backend.py`, `body/sim_backend.py`, `body/poses.py`, `body/urdf.py`, `scripts/generate_urdf.py`, `assets/urdf/hexapod.urdf`, `scripts/sim_demo.py`, `tests/test_backend.py`, `tests/test_urdf.py`, `tests/test_sim_backend.py`; config gains `BODY_HEIGHT_STAND = TIBIA_LENGTH`, `JOINT_NAMES` and the simulation-model constants.
- **Verify**: `python scripts/generate_urdf.py --check`; `python scripts/sim_demo.py --headless --pose stand`; `python scripts/sim_demo.py --pose stand` (GUI, manual check; see the GUI note below); `pytest tests/test_backend.py tests/test_urdf.py tests/test_sim_backend.py`
- **Tests**: foot centres in PyBullet match `FK` + `leg_to_body` within 1 mm on 20 seeded random joint sets (this also proves the URDF axis signs); after 3 s standing, height within 5 mm of `BODY_HEIGHT_STAND`, roll/pitch under 2°, joint velocities near zero; commands outside the hard limits are clamped; sit then stand returns to the stand pose; `advance()` follows wall-clock time and caps catch-up; the URDF matches config (axes, mount yaw, limits, link lengths) and the committed file is not stale; ABC cannot be instantiated and `ServoBackend` raises `NotImplementedError`.
- **Done when**: headless runs hold stand and sit, tests pass, and the GUI shows the same on a machine whose OpenGL supports it.
- **GUI note**: PyBullet's GUI needs OpenGL 3.3+ shaders. On the dev laptop (Intel HD Graphics "ILK", OpenGL 2.1) the plain GUI aborts with `GLSL 1.50 is not supported`; it starts with Mesa's `MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330`. Tests never use the GUI.

### Step 3 — Tripod gait
- **Goal**: pure gait planner (phase + body velocity to six foot targets and joint angles) that walks the sim robot forward, turns it in place and strafes it.
- **Files**: `body/gait.py`, `tests/test_gait.py`, `tests/walk_harness.py`, `tests/test_walk_sim.py`, `scripts/walk_demo.py`. Also: `SimBackend.advance()` now returns the simulated seconds actually stepped; the URDF's ground contact moved to a sphere on the tibia link (see Step 3 findings); new gait constants in `config.py`.
- **Planner**: input is `phase` in [0, 1), `BodyVelocity(vx, vy, yaw_rate)` (m/s, m/s, rad/s, body frame, turning about the body centre) and `GaitParams` (defaults from config). Tripod A swings in group phase [0, `GAIT_SWING_FRACTION`), B half a cycle later. A foot's stance velocity is `−(v + yaw_rate × p)` for its neutral position `p`; stance is a straight line at ground height, swing is a cycloid in the horizontal and `H sin²(πs)` in height, so velocity is zero at lift-off and touch-down. A zero command gives every foot at neutral with no lift (callers ramp commands). Commands are clamped to the max speed, max yaw rate and max stride. Every target goes through `kinematics.ik` and `GAIT_SOFT_LIMITS_DEG`; if one fails, `plan()` bisects to the largest feasible stride scale and logs a warning.
- **Verify**: `pytest tests/test_gait.py tests/test_walk_sim.py -s`; `python scripts/walk_demo.py --headless`; GUI: `python scripts/walk_demo.py` (laptop note in README).
- **Tests (no PyBullet)**: stance feet never above ground and swing feet lift by `STEP_HEIGHT_M`; the groups are never in swing together; zero velocity leaves every foot at neutral; stance foot moves back in a straight line at ground height by `v × stance time`; swing velocity is zero at lift-off and touch-down; a mirrored command gives mirrored targets (mirror pairs sit in opposite groups, so the mirror image at phase p equals the mirrored command at p + 0.5); all targets reachable and inside the soft limits at max speed for 8 command directions; targets continuous across the phase wrap and over the whole cycle; over-limit commands are clamped; an unreachable stride is scaled down with a warning and never yields an out-of-limit pose; the module has no PyBullet or clock.
- **Tests (sim, DIRECT)**: 10 s of sim time after a 2 s ramp-up. Forward and strafe distance within `WALK_TEST_SPEED_TOL` (25 %) of speed × time; turn rate within 25 %; roll and pitch under `WALK_TEST_MAX_TILT_DEG`; body height within `WALK_TEST_HEIGHT_TOL_M` of stand; unwanted drift under `WALK_TEST_POSITION_DRIFT_M` / `WALK_TEST_HEADING_DRIFT_DEG`; the planner never had to shrink the stride.
- **Step 3 findings (measured, before any tuning)**: the first working model walked stably (roll/pitch < 0.2°) but 22–25 % short of the commanded distance. Joint tracking was fine (0.5 mm foot error), so the stance feet were sliding in the world (about −0.023 m/s at 0.1 m/s). Causes, in order of effect: (1) servo velocity gain 1.0 acted as viscous drag between body and feet (gain 0.1–0.3 recovered about 15 %); (2) low friction (μ 1 → 100 recovered about 15 %, but not needed once (1) and (3) are fixed); (3) the ground contact was the flat end of the tibia cylinder rolling on its rim (never the foot sphere), worth about 5 %. Fixing the contact exposed a fourth problem: contact forces through the 10 g foot link made the solver jitter (stand joint speed spiking to 3.7 rad/s). The fix is a collision sphere on the tibia link and a visual-only foot link. Result: `JOINT_VELOCITY_GAIN = 0.3`, friction unchanged.
- **Done when**: all tests pass and the demo walks, turns and strafes in headless and GUI modes.

### Step 4 — Controller and body timing
- **Goal**: controller state machine, wall-clock-driven fixed loops, and manual control tools.
- **Files**: `body/controller.py`, `body/clock.py` (`Clock`, `MonotonicClock`, `ManualClock`, `FixedRateLoop`, `FixedStepper`), `scripts/teleop.py`, `scripts/joint_jog.py`, `scripts/__init__.py`, updated `scripts/sim_demo.py` (`--script`), tests (`test_controller.py`, `test_controller_sim.py`, `test_clock.py`, `test_teleop.py`, `fakes.py`). Also: `kinematics.foot_positions_body`, `SimBackend.client`, a swing height that scales with the stride (so ramps to zero have no jump), new controller and teleop constants in `config.py`, and BLAS thread pinning.
- **Controller**: `set_velocity`/`walk`/`turn`/`heartbeat`/`stand`/`sit`/`wave`/`stop`/`tick(dt)`/`drain_events()`. States: standing, sitting, moving, waving, standing_up, sitting_down, settling, holding. Velocity is slewed (`VELOCITY_RAMP_S`). `stop` while idle changes nothing; otherwise it holds the pose (targets frozen, phase kept). `walk`/`turn` from `holding` blend in over `RESUME_BLEND_S`; `sit`/`stand` from `holding` first settle to the neutral stance (`SETTLE_S`). Watchdog: no command or heartbeat within `WATCHDOG_TIMEOUT_S` ramps to zero.
- **Manual control**: `scripts/teleop.py` (W/S, A/D, Q/E, Space, 1/2/3, +/-; releasing a key zeroes that component) calls only the controller API; its key mapping is pure functions. `scripts/joint_jog.py` (dev tuning only) has 18 degree sliders going through `set_joint_targets`.
- **Verify** (one at a time, `nice -n 19`): `pytest tests/test_clock.py tests/test_controller.py tests/test_teleop.py`; `pytest tests/test_controller_sim.py -s`; `python scripts/sim_demo.py --headless --script "stand,walk,stop,sit"`.
- **Tests**: as listed in the earlier plan (phase independent of tick length, physics time follows elapsed time, busy / invalid_state rules, stop freezes targets for 10 ticks and keeps the phase, resume from the held phase, settle-first with every foot at neutral) plus: a step command never moves a foot target faster than `FOOT_TARGET_MAX_SPEED_M_S`; watchdog expiry reaches exactly zero and holds a stable stand; held poses at four gait phases stay level and at rest in the sim; distance walked is the same at 5, 10 and 20 ms ticks; teleop key mapping, speed scale, deadman and API boundaries without a GUI.
- **Step 4 findings (measured)**: BLAS thread contention made every tick about 7x slower on the dev laptop (22 ms to 3 ms per tick) and was crashing it. The walk also degrades at control ticks of 50 ms and above (0.42 m in 4 s at 20-40 ms, 0.145 m at 50 ms), so loops use nominal 20 ms ticks via `FixedStepper`.
- **Done when**: tests pass and the scripted sequence travels the same distance in the GUI and headless (0.391 m for `walk:4` in both).

### Step 5 — Bridge, arbitration, watchdog, body process
- **Goal**: the full body-side contract.
- **Files**: `bridge.py`, `body/arbitration.py`, `body/process.py`, `tests/fakes.py` (fake backend + fake clock), `tests/test_bridge.py`, `tests/test_arbitration.py`, `tests/test_watchdog.py`, `tests/test_body_process.py`.
- **Verify**: `pytest tests/test_bridge.py tests/test_arbitration.py tests/test_watchdog.py tests/test_body_process.py`; `python -m body.process --headless` (idle, `Ctrl-C` exits cleanly)
- **Tests**: dataclasses round-trip through `mp.Queue`; stale messages dropped, but a stale `stop` still executes; latest-wins among motion commands; `stop` in the same batch as a `walk` wins and the walk gets `rejected(superseded)`; `stop_event` alone (queue message withheld) stops the body within one tick; `stop` is delivered even when the command queue is full; a full queue drops the oldest message and never blocks the sender; a duplicate `stop` (event plus queue) is harmless and always `accepted`; heartbeats refresh the watchdog and get no reply; watchdog stops a walking body after `WATCHDOG_TIMEOUT_S` and not an idle one; each status kind (`accepted`, `rejected`, `done`, `busy`, `fallen`, `error`) is emitted by a test that triggers it; integration: spawn the real process headless, send `walk`, observe `accepted`, send `stop`, observe `done` within 100 ms.
- **Done when**: all pass and the process shuts down cleanly with no orphaned PyBullet server.
- **As built** (differences from the text above): the watchdog tests live in `tests/test_body_runner.py` (in-process, fake clock) and `tests/test_body_process.py` (real process). `Bridge` also carries `stop_seq` (the seq of the latest stop, so the fast path answers the stop by its own `ref_seq`, and a dropped queue message is still answered; the queued duplicate is then skipped silently), `shutdown_event` and `ready_event` (set once the backend is up; the brain waits for it, otherwise early commands would be stale). `BodyRunner` is the in-process core, `run_body` the child loop, `BodyProcess` the parent handle (spawn, `wait_ready`, `shutdown`: event, join, terminate, kill). The child ignores SIGINT, exits on shutdown, SIGTERM or when its parent pid changes. `BodyProbe` is a shared timing block for tests; `scripts/bridge_cli.py` drives the bridge by hand.

### Step 6 — Router
- **Goal**: deterministic utterance to command mapping, no audio involved.
- **Files**: `brain/router.py`, `tests/test_router.py`.
- **Verify**: `pytest tests/test_router.py`
- **Rules**: `STOP_WORDS` match anywhere, highest priority. Other commands need ≤ `ROUTER_MAX_WORDS` words after filler removal **and** `fuzz.ratio ≥ ROUTER_THRESHOLD` (not `partial_ratio`, which causes false positives). Anything else returns "chat".
- **Tests (positive)**: "sit down", "hexa please stand up", "can you wave", "walk forward", "halt", "freeze", "hexa stop".
- **Tests (negative, must not trigger a command)**: "I sat down for lunch", "I'll walk you through it", "turn up the music", "what is a good way to stand out", "can you tell me about waves".
- **Test (accepted tradeoff)**: "I can't stop laughing" does stop the robot (stop errs on the safe side).
- **Done when**: tests pass, including every negative case, with the threshold read from config.
- **As built** (Step 6): `route(text)` returns a `RouteResult` (kind `stop` | `command` | `chat`, action, params, matched phrase, score, normalised text). Stop words match whole words or phrases anywhere (`stop`, `halt`, `freeze`, `whoa`, `hold still`, `stay still`); "do not stop talking" and "I can't stop laughing" stop the robot by design. The phrase table (`ROUTER_PHRASES`, with natural variants such as bare `sit` and `stand`, which a ratio against `sit down` would miss) and `ROUTER_*` live in `config.py`. Single-word utterances must match a phrase exactly (no fuzzing: "sand" is chat); fuzzy matching applies only to two or more words; `ROUTER_ALIASES` holds exact-only near-forms ("waves", "sat down"). Because a `walk` needs heartbeats, `brain/motion_keeper.py` follows what was sent and answered and says when a `heartbeat` is due at `HEARTBEAT_HZ`; it holds a `walk` or `turn` (both need heartbeats until done) and releases it on stop, another motion command, rejected/busy/error/fallen/done for the walk, or after `VOICE_WALK_MAX_S` (the body's watchdog then ends the walk gracefully). It sends `heartbeat` messages, not repeated walks: a repeat would be answered `accepted` five times a second and would revive a walk the body had ended. `scripts/brain_cli.py` is the typed-text front end (`BrainLoop` is its testable core).

### Step 7 — TTS and playback thread
- **Goal**: Piper speech through a clearable playback queue.
- **Files**: `voice/tts.py`, `voice/playback.py`, `scripts/fetch_models.sh` (Piper part), `tests/test_playback.py`.
- **Verify**: `scripts/fetch_models.sh`; `python -m voice.playback "hello, I am hexa"` (audible); `pytest tests/test_playback.py`
- **Tests** (fake synthesizer): items play in order; `clear()` drops pending items and aborts the current one within `TTS_CLEAR_MAX_S`; `speaking` is set during playback and cleared after; thread exits cleanly on shutdown. `clear()` is implemented now so Step 10 only wires it.
- **Done when**: speech is audible, `clear()` works, and no zombie `piper` process remains after exit.

### Step 8 — STT, self-hearing protection, voice-to-body wiring
- **Goal**: spoken commands move the simulated robot, and the robot cannot hear itself.
- **Files**: `voice/audio.py`, `voice/stt.py`, `scripts/fetch_models.sh` (Vosk part), `main.py` (voice commands only), `tests/test_stt_gate.py`.
- **Verify**: `scripts/fetch_models.sh`; `pytest tests/test_stt_gate.py`; `python main.py --headless` then say "walk forward", "stop" (check log); manual self-hearing check below.
- **Self-hearing protection**: STT results (partial and final) are discarded while `speaking.is_set()` or within `SPEAK_TAIL_S` after it clears. The mic stream is still read while gated, and the recognizer is reset when the gate opens so audio captured during speech is never decoded.
- **Tests** (fake clock): results inside the speaking window and the tail are discarded; the first result after the tail is accepted; gate state survives rapid speak/clear cycles.
- **Manual check**: make hexa say a command phrase (e.g. "walk forward") through the speakers; the log must show no recognised utterance and no command sent.
- **Done when**: a spoken "walk forward" then "stop" works end to end in the sim, and the manual self-hearing check passes.

### Step 9 — Status-driven speech, chat fallback, full `main.py`
- **Goal**: the brain speaks from body status; open conversation goes to Ollama.
- **Files**: `brain/dialogue.py`, `brain/chat.py`, `main.py`, `tests/test_dialogue.py`, `tests/test_chat.py`.
- **Verify**: `pytest tests/test_dialogue.py tests/test_chat.py`; `ollama serve` running, then `python main.py` and talk.
- **Tests**: each status maps to the right phrase (`rejected/already_in_state` gives "I'm already sitting"); heartbeats are sent only while the body is walking or turning; chat uses `requests` against a stubbed server, honours `OLLAMA_TIMEOUT_S`, and degrades to a spoken apology when Ollama is down.
- **Done when**: full loop works in the sim (commands, status speech, chat) and killing the brain stops a walking body within `WATCHDOG_TIMEOUT_S`.

### Step 10 — Barge-in
- **Goal**: the user can interrupt hexa mid-sentence.
- **Files**: `voice/stt.py`, `voice/playback.py`, `brain/chat.py`, `tests/test_barge_in.py`.
- **Verify**: `pytest tests/test_barge_in.py`; manual interruption test.
- **Behaviour**: confirmed user speech during TTS calls `playback.clear()` and cancels the in-flight chat request; `stop` always works even while hexa is talking. This replaces the simple self-hearing gate during speech, so it needs a way to tell the user from hexa's own voice (default 5 in section 12).
- **Done when**: interrupting stops speech within `TTS_CLEAR_MAX_S` and hexa does not interrupt itself.

### Step 11 — Real hardware on the Pi 5
- **Goal**: `ServoBackend` drives the real robot with no other code changed.
- **Files**: `body/servo_backend.py` (with its calibration table: per-joint centre, sign, offset, channel, pulse range), `requirements-pi.txt`, `scripts/calibrate_servos.py`, `tests/test_servo_backend.py` (against a fake driver).
- **Tests**: clean-frame angle to servo angle round trip for every joint; ±90° hard limits map inside the servo's physical range; left-side sign flips come only from the table.
- **Verify**: `pytest tests/test_servo_backend.py`; calibration script with the robot held off the ground; then ground tests starting with `stand`.
- **Power (required)**: the 18 servos need a **separate high-current 5–6 V supply**, with a common ground to the Pi. **The Pi must never power the servos.** Add a physical power switch as the emergency stop.
- **Done when**: stand, sit, walk, stop behave like the sim, and pulling the brain process stops the robot via the watchdog.

## 9. Performance targets

Targets to be measured and logged, not guarantees.

| Metric | Desktop (sim) | Pi 5 |
|---|---|---|
| Control loop jitter (p95 of tick interval error) | < 2 ms | < 5 ms |
| Control tick compute time (p99) | < 10 ms | < 15 ms |
| Utterance end to command accepted by body | < 600 ms | < 900 ms |
| `stop` heard to motion halted | < 300 ms | < 400 ms |
| Command enqueue to `accepted` status | ≤ 2 control ticks (40 ms) | ≤ 3 ticks |
| Watchdog trigger accuracy | `WATCHDOG_TIMEOUT_S` + 1 tick | same |
| TTS first audio after text ready | < 500 ms | < 800 ms |
| Gait speed vs CPU load | ±5% when a second process burns one core | same |

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Gait speed varies with CPU load | Wall-clock accumulators for control and physics; test with a fake clock (Step 4) |
| Robot hears itself and obeys | `speaking` gate plus tail (Step 8), barge-in later (Step 10) |
| Fuzzy matching triggers on normal speech | Short-utterance rule, `fuzz.ratio` only, negative tests (Step 6) |
| Brain hangs while robot walks | Heartbeat plus `WATCHDOG_TIMEOUT_S` (Step 5) |
| `stop` queued behind stale messages | Full drain each tick, `stop` preempts, stale `stop` still honoured |
| Mixed-up angle conventions (clockwise mounts vs counter-clockwise URDF yaw) | One helper `mount_yaw_rad`, tested for all 6 legs (Step 0) |
| Sim joint signs differ from real servos | Clean frame in sim; all centre/sign/offset lives in the `servo_backend.py` calibration table (Step 11) |
| `stop` holds a pose with a swing leg in the air | Tripod gait always keeps 3 stance legs down; test that held poses stay upright in sim (Step 4) |
| `stop` lost on a full queue | `stop_event` fast path independent of the queue (Step 5) |
| Vosk poor on open conversation | Accept for commands; whisper.cpp tiny/base is a later option for chat only |
| Ollama slow on the Pi 5 | Small model, `CHAT_MAX_TOKENS`, `OLLAMA_TIMEOUT_S`, spoken fallback |
| Servo brown-outs or Pi reset | Separate servo supply, common ground (Step 11) |
| Piper slow on the dev laptop (measured, Core i3 M380, no AVX, `en_US-amy-low`) | AVX count 0 but Piper runs. One process per sentence: about 1.5 s model load per call, end-to-end real-time factor 1.4 (ruled out). Long-lived process: startup about 2.6 s once, then real-time factor 0.6-1.0, first audio 0.6 s for one word, 1.8 s for a short sentence, 4.2 s for a 4 s one (Piper emits a sentence only when it is done). So Piper is ALWAYS a long-lived process; fixed phrases and fillers are pre-rendered; replies are split into sentences with prefetch; the Pi 5 (NEON, faster core) is expected to be well under 1.0 (Step 7) |
| Piper/Vosk models missing on a fresh checkout | `scripts/fetch_models.sh`; startup check that names the missing file |

## 11. Out of scope

Vision, SLAM, navigation or obstacle avoidance; IMU balance control and rough terrain; gaits other than tripod; wake-word detection, speaker identification, multi-language; cloud STT/TTS/LLM; web or GUI control panel; mechanical design and CAD; battery management; over-the-air updates. Barge-in and hardware are in scope but only at Steps 10 and 11.

## 12. Open Questions

### Resolved by the owner
| Topic | Decision |
|---|---|
| Joint neutral | Clean frame, 0 = neutral, no offsets in sim/kinematics. Servo centre/sign/offset (tibia included) go in a calibration table in `servo_backend.py` only (Step 11). |
| Limit tiers | Hard (servo range, ±90°, enforced in the clamp layer) and soft gait limits (coxa about ±30°, used by the gait planner). |
| Posture actions | `sit`/`stand`/`wave` return `busy` during a transition. `stop` interrupts and holds the current pose. |
| Stop path | Shared `mp.Event` set by `bridge.send`, checked every tick, plus the normal queue message. Small command queue, drop-oldest. |
| Angle convention | Clockwise, one conversion point (`config.mount_yaw_rad`), plus a test that RF's foot target lands on the right side. |
| `CLAUDE.md` | Replaced with a short pointer to `AGENTS.md` and `plan.md`; no duplicated rules. |

### Defaults assumed (veto any)
| # | Topic | Default |
|---|---|---|
| 1 | Knee branch | Knee-up. |
| 2 | Walk semantics | `walk` is continuous and needs heartbeats; `turn` and `wave` are finite. No "walk three steps" in v1. |
| 3 | Timestamps | `time.monotonic()` across processes on one Linux host; revisit only if the bridge ever goes over a network. |
| 4 | Headless timing | DIRECT mode runs in real time like the GUI; `--no-realtime` exists for fast tests only. |
| 5 | Barge-in audio (Step 10) | Needs a headset or a mic placed away from the speaker; Vosk has no echo cancellation. Step 10 may require a headset. |
| 6 | "stop" anywhere in an utterance | Accepted: "I can't stop laughing" stops the robot (safe side). |
| 7 | Numeric params | Router does not parse numbers in v1; `turn left` uses `angle_deg = 90`. |
| 8 | Servo driver | Two 16-channel PWM boards (e.g. PCA9685); only affects Step 11. |
| 9 | URDF | Generated from `config.py` by `scripts/generate_urdf.py` (no duplicated geometry). |
| 10 | After `stop` | State is `holding`; gait phase is kept, so `walk`/`turn` resume from it; `sit`/`stand` are allowed from `holding` but first settle all six feet to the neutral stance, then run the transition; `wave` is `rejected(invalid_state)` unless standing. |
