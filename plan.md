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
├── web/                      # phone control page (Step 12a): protocol.py, session.py, server.py, static/index.html
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
- **Verify**: `scripts/fetch_models.sh`; `python scripts/say.py "hello, I am hexa"` (audible); `pytest tests/test_playback.py tests/test_tts.py`
- **Tests** (fake synthesizer): items play in order; `clear()` drops pending items and aborts the current one within `TTS_CLEAR_MAX_S`; `speaking` is set during playback and cleared after; thread exits cleanly on shutdown. `clear()` is implemented now so Step 10 only wires it.
- **Done when**: speech is audible, `clear()` works, and no zombie `piper` process remains after exit.
- **As built**: `voice/tts.py` (`TtsEngine`, `AudioClip`, `PiperEngine`: one long-lived process in `--output_dir` mode, which prints one WAV path per input line, a clean end-of-utterance marker; a death or a hang beyond `TTS_SYNTH_TIMEOUT_S` is logged, Piper is restarted once, that sentence fails with `TtsError`; Piper exits on stdin EOF so it dies with its parent) and `voice/playback.py` (`AudioSink`, `SoundDeviceSink`, `Playback`: synthesis worker + playback thread, `TTS_PREFETCH_SIZE`, `clear()`, `speaking` with `SPEAK_TAIL_S`). Pre-rendered phrases: `config.TTS_PHRASES`, `scripts/prerender_phrases.py`. `scripts/say.py`, `scripts/measure_voice.py`. `clear()` cannot interrupt a sentence Piper is already rendering (killing it would cost a restart); the result is discarded when it ends, so a new sentence after a `clear()` can wait up to one sentence's synthesis time. Measured on the dev laptop: `clear()` median 1.0 ms, worst 4.0 ms of 20 (bound 100 ms); the body's tick time roughly doubles while Piper synthesizes (see Risks).

### Step 8 — STT, self-hearing protection, voice-to-body wiring
- **Goal**: spoken commands move the simulated robot, and the robot cannot hear itself.
- **Files**: `voice/audio.py`, `voice/stt.py`, `scripts/fetch_models.sh` (Vosk part), `main.py` (voice commands only), `tests/test_stt_gate.py`.
- **AudioSource interface (required)**: between audio capture and Vosk sits a small `AudioSource` interface in `voice/audio.py` that yields 16 kHz mono int16 blocks (`AUDIO_SAMPLE_RATE`, `AUDIO_BLOCKSIZE`), with `start()`/`stop()`/`read(timeout)`. Step 8 ships the local-microphone implementation (`MicSource`, sounddevice) and a fake/file source for tests. `stt.py` depends only on the interface, never on sounddevice, so a network-stream source (the Step 12 web UI's WebSocket audio) can be added later with no change to STT, the gate or the router.
- **Status subscribers (required, built with the voice loop)**: the body's `status_queue` has ONE reader, a status hub in the brain (`brain/status_hub.py`) that fans every status out to any number of subscribers (dialogue/speech, the motion keeper, CLI, control window, the web UI later); each subscriber has its own small drop-oldest queue so a slow subscriber never blocks the others or the body. Nothing else reads `status_queue` directly.
- **Verify**: `scripts/fetch_models.sh`; `pytest tests/test_stt_gate.py`; `python main.py --headless` then say "walk forward", "stop" (check log); manual self-hearing check below.
- **Self-hearing protection**: audio captured while `speaking` is set is DISCARDED before it reaches Vosk. `Playback` already keeps `speaking` set until the sound ends plus `SPEAK_TAIL_S`, so the tail lives there and the gate adds none. The mic stream is still read while gated; a block is also dropped if speaking was set when the previous block arrived (the edge block), and the recognizer is reset before the first clean block so audio from before the gap never leaks through.
- **Tests** (fake clock): results inside the speaking window and the tail are discarded; the first result after the tail is accepted; gate state survives rapid speak/clear cycles.
- **Manual check**: make hexa say a command phrase (e.g. "walk forward") through the speakers; the log must show no recognised utterance and no command sent.
- **Done when**: a spoken "walk forward" then "stop" works end to end in the sim, and the manual self-hearing check passes.
- **As built**: `voice/audio.py` (`AudioSource`, `MicSource` with a drop-oldest queue, `FileSource`, `QueueSource`, `list_input_devices`), `voice/stt.py` (`VoskStt`, `SttEvent`, `SelfHearingGate`), `brain/status_hub.py` (`StatusHub`, `Subscription`), `brain/brain_loop.py` (`BrainLoop`, moved out of `brain_cli`, now fed by a hub subscription), `brain/voice_loop.py` (`VoiceLoop`: STT thread + router worker; only finals are routed, a stop word in a partial stops at once, "okay" acknowledgement). Scripts: `mic_check.py`, `stt_check.py`, `voice_cli.py`, `measure_voice.py --stt`. `control_window` and `brain_cli` read statuses through the hub; `bridge_cli` (standalone raw-Bridge tool) still reads the Bridge directly and must not run beside a hub. Models: small US and small Indian English Vosk (`config.VOSK_MODELS`).
- **Step 8b (command grammar)**: one Vosk model, two recognizers on the same blocks (free text and a grammar built from `config` by `voice/stt.py::command_grammar()`), word confidences on both, both reset together by the gate. `brain/stt_decision.py::decide()` picks the text to route: grammar stop (confidence >= `STT_STOP_CONF`, free text not much longer), else a grammar result that is exactly one known phrase/alias that the short free text agrees with and whose mean confidence >= `STT_GRAMMAR_CONF`, else the free text. A stop word in the free text always stops. Measured on Piper speech: the grammar rescues the Indian model's "stop" (free heard "start"); on ordinary speech it forces matches (Indian model: "what is the weather today" became "walk forward could hey" at confidence 1.0, "tell me a joke" became "whoa" at 0.71), so confidence is never the guard; the exact-phrase and word-count guards are. Every final is logged to `logs/transcripts.jsonl`. `scripts/stt_check.py` repeats each phrase 3 times, shows both results, the deciding rule and the summary (command accuracy, chat routed correctly, dangerous chat-to-motion false positives, missed stops), and saves `logs/stt_check-<model>.jsonl` so thresholds can be re-tuned with `--replay`. **Owner decision (after `stt_check`)**: the small US model `vosk-model-small-en-us-0.15` is THE speech model; the Indian English model is not used (its run was incomplete; 58/69 commands but slow). Results on the owner's voice, US model, 117 attempts: commands 50/69 (72%), stops 14/18 (78%), chat routed correctly 30/30, 0 dangerous false positives, 0 wrong motion commands; missed stops were 3 x "halt" and 1 x "freeze" (so "stop" is the reliable stop word; two-word commands are reliable, single words are not). Replay of the saved run (`stt_check.py --replay`) at `STT_GRAMMAR_CONF` 0.5 / 0.6 / 0.75 / 0.9: command accuracy 51/69 (74%) / 50 (72%) / 50 (72%) / 47 (68%), stops 14/18 and chat 30/30 at all values, dangerous 0 and wrong motion 0 at all values. A lower value was to be adopted only with +5 points of command accuracy; 0.5 gives +1.4, so `STT_GRAMMAR_CONF` stays 0.75. **The Step 8b benchmark of the Indian model is deferred: optional, not removed** (`fetch_models.sh --all-models`, then `stt_check.py --model in`).

### Step 9 — Status-driven speech, chat fallback, full `main.py`
- **Goal**: the brain speaks from body status; open conversation goes to Ollama.
- **Files**: `brain/dialogue.py`, `brain/chat.py`, `main.py`, `tests/test_dialogue.py`, `tests/test_chat.py`.
- **Status subscribers**: `dialogue.py` is one subscriber of the status hub introduced in Step 8 (not a second reader of `status_queue`); `brain_cli` moves onto the hub too. Test that two subscribers each see every status and that a stalled subscriber does not delay the other.
- **Verify**: `pytest tests/test_dialogue.py tests/test_chat.py`; `ollama serve` running, then `python main.py` and talk.
- **Tests**: each status maps to the right phrase (`rejected/already_in_state` gives "I'm already sitting"); heartbeats are sent only while the body is walking or turning; chat uses `requests` against a stubbed server, honours `OLLAMA_TIMEOUT_S`, and degrades to a spoken apology when Ollama is down.
- **Done when**: full loop works in the sim (commands, status speech, chat) and killing the brain stops a walking body within `WATCHDOG_TIMEOUT_S`.
- **As built**: `brain/chat.py` (`ChatBackend` interface: `stream(messages)` yields chunks, `cancel()`; `OllamaChat` over HTTP with `requests` streaming, cancel by socket shutdown (wakes a blocked read in about 1 ms), `warm_up()`; `FakeChat` with configurable first-token latency and tokens/s; `ChatResponder`: one reply at a time on its own thread, sentence-by-sentence to playback, rolling history of `CHAT_HISTORY_TURNS`, cancel closes the stream and calls `playback.clear()`, a cancelled reply is not remembered, a backend error speaks the pre-rendered "I can't think right now"), `brain/sentences.py` (pure splitter: any chunking gives the same sentences, abbreviations/decimals, markdown and emoji stripped, overlong sentences cut at a clause), `brain/dialogue.py` (status to pre-rendered phrase, throttled, `done` silent by default), `BrainLoop.body_idle()` and sent-command listeners, `VoiceLoop(chat=...)`, `scripts/voice_cli.py --chat ollama|fake|off`, `scripts/measure_chat.py`. Config: `CHAT_*`, `OLLAMA_*`, `DIALOGUE_*`, five new pre-rendered phrases (`sure`, `done`, `something_wrong`, `cant_think`, `tell_me_after_stop`). The Step 8 immediate "okay" was removed: the robot speaks from the body's status, never from assumption.
- **Safety rules (as built)**: the LLM is only for chat and never in the command path (the router decides); `ChatBackend` is the only interface to a model; chat runs only while the body is idle (no held walk or turn, no sit/stand/wave transition, tracked from the statuses with a duration fallback), otherwise "tell me after I stop" and the LLM is not called; a command, a stop or any motion cancels the reply, so the microphone stays free for "stop".
- **Real-model decision**: on the dev laptop (no AVX) `qwen2.5:0.5b` in Docker with all cores makes about 1.5 tokens/s (below the 3 tokens/s bar), so by the rule set for this step chat is developed against `FakeChat` and the real model is timed on the Pi 5 or another machine. Numbers are in Risks. Real-LLM timing is a Pi 5 task (Step 11 checklist, including the `llm` test): on this laptop `qwen2.5:0.5b` measured 1.5 tokens/s with a peak of 90 C. **Run rule (owner, Step 10 correction): `scripts/cool_run.py` with its default kill limit is the DEFAULT for every run; one process at a time; the Ollama container stays stopped unless a test needs it. An unguarded run happens only when the owner explicitly asks for it in that message (`cool_run.py --unguarded`) and must still log the temperature every 2 s and print the peak. The earlier "full CPU, no guard" standing rule is removed.**

### Step 10 — Push-to-talk, barge-in and an instant filler
- **Goal**: the user decides when hexa listens, can interrupt it mid-sentence, and a slow LLM never leaves a silent gap.
- **Files**: `voice/ptt.py` (`PushToTalk`, the pure `ptt_step`), `brain/voice_loop.py`, `voice/playback.py` (`clear(skip_tail=True)`), `brain/chat.py` (filler), `scripts/voice_cli.py`, `scripts/measure_ptt.py`, `tests/test_ptt.py`, `tests/test_barge_in.py`, `tests/test_filler.py`.
- **Listening mode** (`config.LISTEN_MODE`, default `"ptt"`; `"always"` keeps the Step 8 behaviour unchanged).
  - **PTT** is the primary mode. `PushToTalk` is thread-safe with `press()` and `release()`; its state machine (idle, listening, tail) is a pure function with an injected clock (`ptt_step`). While PTT is not active the recognizers are NOT fed (mic blocks are read and dropped, so Vosk spends no CPU). Release finalizes the utterance after `PTT_TAIL_S`; a press during the tail continues the same utterance; a press while already listening is ignored.
  - `voice_cli` in ptt mode: Enter toggles listening on and off (a terminal has no key-release events) and prints LISTENING / IDLE; typing `stop` sends stop at once. True hold-to-talk comes with the phone page (Step 12), which calls `press()` / `release()`.
- **Barge-in**: pressing PTT while hexa speaks calls `playback.clear()` (and drops the speaking tail so the mic is open at once), cancels the chat stream (a cancelled reply is not added to history) and starts listening. In `"always"` mode nothing changes: deaf during speech plus tail, no stop-word spotting during speech.
- **Instant filler**: if a chat utterance went to the LLM and no sentence is ready within `FILLER_DELAY_S` (about 0.8 s), one pre-rendered filler ("hmm", "let me think", "one moment") is played, at most once per reply; not played if the first sentence is ready in time; cancelled by barge-in. It never plays while the body is moving (chat is not called then).
- **Safety statement**: in ptt mode a voice "stop" only works WHILE LISTENING. The control window STOP button and Space are the always-available stop (the `voice_cli` banner says so; `voice_cli` also takes a typed `stop`). In `"always"` mode the voice stop works at all times except while hexa speaks.
- **Wake word is DEFERRED to Step 11 on the Pi** (OpenWakeWord is not installed now).
- **Tests** (no real LLM, no mic): the PTT state machine with a fake clock (press, release, tail, double press, press during speech); recognizers get zero blocks when PTT is off and all blocks while on; barge-in (`clear` within `TTS_CLEAR_MAX_S`, chat cancelled, history unchanged, listening starts); filler (slow FakeChat gives exactly one, fast gives none, cancelled by barge-in, none when the body is moving); mode switch through config; `"always"` regression tests; clean shutdown.
- **Verify**: `pytest tests/test_ptt.py tests/test_barge_in.py tests/test_filler.py`; `python scripts/voice_cli.py --chat fake`, press Enter, speak, press Enter.
- **Done when**: interrupting stops speech within `TTS_CLEAR_MAX_S`, no audio is decoded while PTT is idle, and hexa does not interrupt itself.
- **As built**: `voice/ptt.py` (`ptt_step`, `PushToTalk` with press/change listeners, `make_ptt`), `VoiceLoop(ptt=...)` (`_ptt_gate`, `barge_in`, counters `blocks_idle`, `barge_ins`), `Playback.clear(skip_tail=True)`, `SelfHearingGate.forget()`, `ChatResponder` filler (timer made before the reply thread; cancelled by the first sentence, by cancel and by the end of the stream), `voice_cli` (`--listen`, Enter toggles, typed `stop`, LISTENING / IDLE), `scripts/measure_ptt.py`. Measured on the dev laptop (headless body, real speech mix, nice 19): Vosk process CPU 1 % with ptt idle (0 of 32 blocks fed), 37 % while listening and 23 % in always mode (one run each, noisy: earlier runs gave 13-21 %); body tick mean 6-8 ms in all three (about 50 ticks/s); peak temperature 81 C.

### Step 10b — Sim v0.1 wrap-up (tag `v0.1-sim`)
- **Goal**: one entrypoint and a documented, tested simulation release. No servo or hardware code.
- **Files**: `main.py`, `brain/app.py` (`HexaApp`), `brain/startup.py`, `brain/logsetup.py`, `tests/test_app.py`, `tests/test_main_smoke.py`, `tests/test_startup.py`, `tests/test_logsetup.py`, docs.
- **`main.py`**: flags `--gui|--headless`, `--listen ptt|always`, `--chat fake|ollama|off`, `--no-speak`, `--no-mic`, `--audio-file`, `--model`, `--log-level`, `--log-file`. SIGINT and SIGTERM give a clean shutdown (stop the robot, clear playback, cancel chat, join the threads, `Bridge.close()`, body process last: terminate, then kill) and exit 0; a startup problem exits 1 with one line per problem; a runtime failure exits 2. No TTY is assumed (systemd): push-to-talk is driven through `HexaApp.set_listening()` / `toggle_listening()` (the phone page calls these in Step 12); on a terminal Enter toggles listening and `stop` + Enter sends stop.
- **Startup checks** (`brain/startup.py`): Vosk model, Piper binary and voice, pre-rendered phrases, microphone and speaker (skipped with `--no-mic` / `--no-speak`), Ollama reachable and the model pulled (only with `--chat ollama`).
- **Logging**: `logs/hexa.log`, size-based rotation (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`), one INFO line per status, per heard text and per route result, per spoken phrase.
- **Smoke test** (default suite, skips without models): `main.py` as a real process with a Piper-rendered "sit down" plus a chat sentence as an audio file, real Vosk, headless body, FakeChat: it sits, the chat sentence gets the fake reply, exit code 0, the body process is gone. SIGTERM and SIGINT tests too.
- **Docs**: README quickstart, `docs/ARCHITECTURE.md` (data flow, the safety rules, the measured numbers, known limits).
- **Release**: full suite in batches through the guard, tag `v0.1-sim`. Then wait for the owner's word on Step 11.

### Step 10c — Servo and power sizing from the sim
- **Goal**: numbers to choose servos and a supply. No servo or hardware code.
- **Files**: `scripts/torque_report.py`, `tests/test_torque_report.py`, `docs/TORQUE_REPORT.md` (generated), power method and BOM checklist in `docs/HARDWARE.md`. `config.py`: masses marked PLACEHOLDER, `TOTAL_MASS_KG`, `TORQUE_SAFETY_FACTOR = 2.0`. `SimBackend` gained `max_force_nm`, `velocity_gain`, `on_step` and `joint_states()` (measurement hooks; behaviour unchanged by default).
- **Method**: stand, sit, wave (through the real `Controller`), walk, strafe and turn at the maximum command (gait loop); applied motor torque sampled at every physics step; each motion run with the force cap raised to 30 N*m (the real need) and with the normal 3 N*m cap (does anything saturate). Per joint type: peak, RMS, p99, peak speed, angle range; kg*cm and the safety factor; seconds per 60 degrees; sensitivity to mass (0.7x, 1.0x, 1.3x) and body height; joints near peak at the same time; the walk again with velocity gain 1.0.
- **Limits stated in the report**: no gear friction or backlash, lowered sim velocity gain (understates the peak), placeholder masses, flat floor.

### Step 11 — Real hardware on the Pi 5 (staged 11a-11f; each stage is approved on its own)
- **Goal**: `ServoBackend` drives the real robot with no other code changed. It is done in six stages so that the software, the voice and the web page are proven on the Pi against the SIMULATED body before any servo is powered. **No servo code is written before stage 11e is approved.**
- **The Pi (facts)**: Raspberry Pi 5, 8 GB, **Ubuntu LTS Server (aarch64), NOT Raspberry Pi OS**. Reached with `ssh hexa-pi` (user `hexa`, host `hexa.local`, key auth set up). The Python version is whatever the LTS ships and may differ from the dev laptop's 3.11 (Ubuntu 24.04 ships 3.12): check `pyproject.toml` and that every wheel (PyBullet, Vosk, Piper, websockets, sounddevice) exists for aarch64 on that Python BEFORE relying on it; a missing wheel means a source build (slow, needs `build-essential`) or a decision, not a quiet workaround.
- **Ubuntu notes**: there is no desktop audio server: use ALSA directly (`alsa-utils`, `libportaudio2`; the Pi 5 has no analog jack, so a USB microphone and speaker or a USB headset; the user must be in the `audio` group; check devices with `arecord -l`, `aplay -l` and `scripts/mic_check.py`). I2C is off by default: it needs `dtparam=i2c_arm=on` in `/boot/firmware/config.txt` (not `/boot/config.txt`), the `i2c-dev` module, `i2c-tools`, and the user in the `i2c` group; the device is `/dev/i2c-1`. All of that is owner-run (see the rules).
- **Rules for working on the Pi (owner, Step 11)**:
  - Claude NEVER uses `sudo` on the Pi. Anything that needs it: give the owner the exact command and they run it in their own session.
  - Claude touches only `~/hexa` on the Pi. No changes to `/boot/firmware`, I2C, services, `ufw` or users from Claude. Nothing is enabled at boot (no systemd unit, no cron `@reboot`) until the owner asks for it in a later stage.
  - Run rule (owner, stage 2): no heat guard on the laptop or on the Pi; log the temperature with `cool_run.py --unguarded`. On the Pi, one process at a time, and read `vcgencmd measure_temp` or `/sys/class/thermal` before and after heavy runs.

#### Stage 11a: software bring-up on the Pi, simulated body only (BUILT; real mic/speaker tests and Ollama timing deferred)
- **As built** (Ubuntu 24.04.5, kernel 6.8 raspi, Python 3.12.3, 4 x Cortex-A76, 7.8 GiB, Wi-Fi only):
  - Owner-run apt packages (verified read-only with `dpkg -s`): `python3.12-venv python3-dev build-essential alsa-utils libportaudio2 portaudio19-dev libsndfile1 ffmpeg`. No `unzip` on the Pi: `fetch_models.sh` falls back to Python's `zipfile`.
  - `scripts/sync_to_pi.sh [--dry-run] [--assets]`: rsync to `~/hexa` (no `--delete`; excludes `.git`, `.venv`, `assets/`, `logs/`, `wheelhouse/`, `.tmp/`, caches, wav/log/jsonl). `--assets` also sends, over the LAN, the default Vosk model, the Piper voice `.onnx`/`.json`, `assets/phrases` and `assets/urdf` (never the x86 Piper binary). `wheelhouse/*.whl` is always sent.
  - `body/dryrun_backend.py` (`DryRunBackend`: same clamp layer, no physics, no hardware; writes what the gait would send to the servos to its own rotated file `logs/dryrun.log` as per-leg angles in degrees (coxa femur tibia): one `FIRST` line, at most `DRYRUN_LOG_HZ` = 2 `MOVING` lines a second while the targets change by more than `DRYRUN_LOG_EPS_DEG`, and one `REST` line when they stopped for `DRYRUN_REST_S`; nothing while it stands still; `tail -f logs/dryrun.log`) and `main.py --backend sim|dryrun` (`HEXA_BACKEND` sets the default; `--gui` with dryrun, or `sim` without pybullet, are startup errors). `body/process.py::make_backend` imports lazily, so the body starts without pybullet (`tests/test_dryrun_backend.py` proves it in a subprocess). This is NOT servo code.
  - `requirements-pi.txt`: no pybullet, `--only-binary=:all:`, `--find-links wheelhouse`, `srt==3.5.3`. `srt` (a Vosk dependency) has no wheel on PyPI: `scripts/build_wheelhouse.sh` builds it ON THE LAPTOP (`pip wheel srt==3.5.3 --no-deps`, py3-none-any) into the gitignored `wheelhouse/` and `sync_to_pi.sh` sends it. sha256 `ef8936f0c7d6c3623e7d748ecf71a72bf2b525ebd4c062202f3019a045d8d9b3` (`srt-3.5.3-py3-none-any.whl`). pip on the Pi runs with `TMPDIR=~/hexa/.tmp` so nothing lands in `/tmp`. Every other dependency has an aarch64 cp312 wheel (numpy 2.5.3 15.7 MB, vosk 0.3.45 2.4 MB, rapidfuzz 3.14.6 1.4 MB, websockets 17.2, pytest 9.1.1, ...: about 22 MB).
  - `scripts/fetch_models.sh` is architecture-aware: it downloads only what is missing and only the Piper archive for this machine; the real asset name on the 2023.11.14-2 release is `piper_linux_aarch64.tar.gz` (26.0 MB), checked against the release page.
  - Tests: the six test files that import pybullet skip cleanly without it (`pytest.importorskip`), three physics tests carry `@pytest.mark.pybullet` (skipped when pybullet is missing), and without pybullet `tests/conftest.py` sets `HEXA_BACKEND=dryrun` so the app/web tests run on the dry-run body. The `llm` tests skip with "no Ollama" (`pytest -m llm`: 7 skipped).
  - `scripts/cool_run.py` knows the Pi (device-tree model): sensor `cpu-thermal` from `/sys/class/thermal`, **kill limit 78 C** (the firmware starts throttling at 80 C and again at 85 C; kernel critical 110 C), start below 62 C, and `vcgencmd get_throttled` is printed at the end. Per the owner (stage 2) the Pi is NOT run under the guard either ("it can handle it"): the first Pi measurement pass was made guarded before that message, the web measurement, the last test run and the LAN run unguarded (log only, `--unguarded`). The 78 C profile stays in the script for whenever it is wanted.
  - `scripts/pi_dryrun.sh start [PIN] | log | status | attach | stop`: runs hexa in a tmux session named `hexa` (dry-run body, `--lan`, `--no-mic --no-speak --chat fake`), survives the ssh session closing, prints the PIN and page address, `stop` sends Ctrl-C (a clean shutdown) and closes the session.
  - `deploy/hexa.service`: a systemd unit (dry-run backend) that is written, NOT installed and NOT enabled. A unit with a hardware backend must never be enabled at boot until the kill switch and calibration (11e/11f) are done.
  - New measuring scripts: `scripts/measure_e2e.py` (end of a spoken "sit down" WAV to the first joint change) and `scripts/measure_lan.py` (phone-page latency from another machine over the Wi-Fi).
- **Test counts (junit XML)**: Pi 972 collected, **962 passed, 10 skipped (pybullet/llm), 0 failed**, 12 deselected (timing/audio/llm markers); peak 51 C, `get_throttled=0x0` before and after. Laptop (unguarded batches): 1042 passed in four batches plus one docs-listing failure that was fixed and re-run (17 passed); `ruff` and `mypy` clean; peaks 84-90 C.
- **Measured on the Pi 5 (dry-run body, guarded, `get_throttled=0x0` after every run) against the dev laptop (Core i3 M380)**:

| Measurement | Pi 5 | Dev laptop |
|---|---|---|
| Body tick work, standing / walking (mean, worst) | 0.09 ms (0.1) / 0.42 ms (0.7) at 50 ticks/s | 7-8 ms with the simulator (not comparable: the Pi has no physics) |
| Body tick work while Piper is busy, standing / walking | 0.68 ms (worst 6.4) / 1.52 ms (worst 8.4), 50 ticks/s held | 22 / 30 ms, ticks fell to 31-37 per s |
| Piper real-time factor, time to first audio | 0.12, median 0.29 s (worst 0.40 s) | 0.6-1.0, 0.6 s for one word, 1.8 s for a short sentence |
| Piper CPU / RAM while busy | 381 % of one core, 152 MB peak | about 2 cores |
| Vosk alone, 1 recognizer: silence / speech | 8 % / 14 % of a core; decode real-time factor 0.04 / 0.07 | 15-22 % of a core; 0.07-0.11 |
| Vosk, 2 recognizers (free + grammar): silence / speech | 13 % / 17 %; decode factor 0.07 / 0.10; RSS 172 MB | 21-23 %; 178 MB |
| Vosk with the body: ptt idle / ptt listening / always (process CPU) | 0 % / 22 % / 11 % of a core; tick 0.10-0.35 ms | 1 % / 37 % / 23 %; tick 6-8 ms |
| End of speech to command sent ("walk forward", `measure_voice --stt --latency`, 3 trials) | 0.84 s; command to first foot-target change 0.19-0.20 s; total 1.03-1.04 s (earlier run: 0.75-1.01 s, total 0.94-1.20 s) | 0.86 s; 0.26 s; 1.12 s |
| End to end: "sit down" WAV end to first joint change (`measure_e2e.py`, 5 trials) | median 900 ms, max 911 ms (5/5 trials; an earlier run gave 868 ms) | not measured |
| Phone page on the Pi, localhost: press to first joint change (20) | median 39.4 ms, p95 39.8 ms | not recorded in this form |
| Phone page from the laptop over Wi-Fi (`measure_lan.py`, 20 trials): button to answer | median 51 ms, p95 56 ms, max 62 ms; connect + handshake 209 ms; ping avg 8.9 ms (max 13.5 ms) | n/a |
| Release (`ptt_release`) to command sent, fake STT (20) | median 701 ms (500 ms of it is `PTT_TAIL_S`) | see 12b |
| Server idle CPU: nobody connected / one idle client | web thread 0.00 % / 0.10 %, whole brain process 0.3 % | n/a |
| Idle temperature / peaks | idle 43-44 C; peak 67 C (Piper busy); fan trips 50/60/67.5/75 C | idle 59 C, peaks 84-90 C |

  The Pi is far faster than the laptop on every voice number; the end-to-end delay after you stop speaking (about 0.9 s) is dominated by Vosk's wait for silence, not by compute. The Pi's Wi-Fi address changed once (10.159.38.235 to .234): use `hexa.local` or read the URL `--lan` prints.
- **Measured without any audio device** (owner: no USB mic or speaker available): everything above runs with `--no-mic --no-speak`; Piper is timed synthesizing to memory (no playback) by `measure_voice.py --body`; Vosk is timed on generated speech and on a Piper-rendered "sit down" WAV through `FileSource` (`measure_voice.py --stt --latency`, `measure_e2e.py`).
- **Owner plan for the next session (written step by step in `docs/HOW_TO.md`, "Work on the Raspberry Pi")**: connect the Pi to a faster Wi-Fi (netplan procedure with the old network kept as a fallback), install Ollama by hand under `~/ollama` (no boot service), pull both models, time them with `measure_chat.py --with-voice`, then run the full suite on the Pi. `measure_chat.py` now reports the RAM of the `ollama` processes when there is no Docker container. `scripts/pi_dryrun.sh` accepts `HEXA_CHAT=ollama` to chat with the real model on the page.
- **Deferred (owner has no fast Wi-Fi yet), with the same numbers to record later**: install Ollama (owner-run: the official arm64 script also enables a boot service, which conflicts with "nothing at boot": choose the manual tarball or Docker), pull `qwen2.5:0.5b` and `qwen2.5:1.5b`, then run `pytest -m llm` and `scripts/measure_chat.py` on the Pi for tokens/s, first-token time, RAM and temperature (bar: 3 tokens/s and a first token within 5 s). Until then chat stays on `FakeChat` (`--chat fake`).
- **Deferred (needs the owner present with USB devices; none was plugged in, `arecord -l` lists no capture device and `aplay -l` only the two HDMI outputs)**: `scripts/mic_check.py`, `scripts/say.py` and the real-speaker checks of 11b.
- **Done when**: the non-`audio`/`timing`/`llm` suite passes on the Pi (done), the headless dry-run body starts, walks and stops through the bridge (done: `main.py --backend dryrun`, the web page over the LAN, `measure_e2e.py`), and the idle CPU and temperature are reported (done).

#### Stage 11b: voice on the Pi (still the simulated body)
- `scripts/fetch_models.sh` for the ARM Piper and the Vosk models, `scripts/mic_check.py`, `stt_check.py`, `voice_cli.py`, then `main.py` with the real microphone and speaker in ptt and always mode. Barge-in and the self-hearing gate are judged with the real speaker and microphone positions.
- **Pi voice checklist (deferred from the dev laptop)**:
  - Real-LLM timing: run `pytest -m llm` (`tests/test_chat_llm.py`) and `scripts/measure_chat.py` on the Pi (qwen2.5:0.5b, then 1.5b). The bar is 3 tokens/s and a first token within 5 s. On the dev laptop `qwen2.5:0.5b` measured 1.5 tokens/s with a 90 C peak, so it was never judged real-time there.
  - Re-measure Piper's load on the body (Risks), Vosk CPU in ptt and always mode.
  - Wake word (OpenWakeWord) as a third listening mode, deferred from Step 10.
- **Done when**: spoken "walk forward" / "stop" drive the sim on the Pi, Piper and Vosk loads are measured, and the real-LLM numbers are recorded (or chat stays on `FakeChat` with the reason).

#### Stage 11c: phone page on the Pi
- `main.py --web --lan` on the Pi; the page from a real phone on the same Wi-Fi. This also closes the open Step 12a / 12b item: multi-touch, pointercancel, long-press menu, screen lock, Wi-Fi loss, hold-to-talk with the robot's microphone. A firewall (`ufw`) rule, if one is needed, is an owner-run command.
- **Done when**: every item of the 12a "NOT verified on a real phone" note is checked on a phone and the result is written into the 12a status line.

#### Stage 11d: hardware readiness (checklist and sizing only, no code)
- **Hardware-readiness checklist (all must be true BEFORE any servo code is written; checklist only)**:
  - [ ] Raspberry Pi 5 (8 GB preferred) with the **active cooler**, a good 5 V / 5 A supply for the Pi itself, a microSD or NVMe with the OS installed, SSH working.
  - [ ] **PCA9685** 16-channel PWM driver(s): two boards for 18 channels (addresses set, I2C enabled, `i2cdetect` shows both).
  - [ ] **18 servos** of the type the URDF assumes (torque and travel checked), 3 per leg, each labelled with leg and joint (coxa, femur, tibia).
  - [ ] A **separate high-current 5-6 V supply** for the servos (sized for the stall current of 18 servos, with margin). **The Pi never powers the servos.**
  - [ ] **Common ground** between the servo supply, the PCA9685 boards and the Pi.
  - [ ] A **kill switch in the servo power line** (physical, within reach), plus a bulk capacitor near the boards. Tested: switching it off removes all servo power while the Pi keeps running.
  - [ ] A stand or blocks to hold the robot **off the ground** for the first power-up and calibration.
  - [ ] **Calibration procedure written** for the table in `body/servo_backend.py` (per-joint channel, centre pulse, sign, offset, pulse range): servos centred with horns off; horns fitted at neutral; sign checked per side; limits found with the robot held; each value written into the table (never into `config.py`) and the table committed with a date.
  - [ ] USB microphone and speaker (or headset) chosen; `requirements-pi.txt` installs on the Pi; `scripts/fetch_models.sh` fetches the ARM Piper.
  - [ ] `main.py --headless --no-speak --no-mic` already runs on the Pi against the simulated backend (proves the software stack before hardware).
- **Parked from Step 10d (branch `step-10d-mg995`, commit `4f7af4c`)**: when hardware work starts, revisit the joint speed limit (`JOINT_MAX_SPEED_DEG_S` in the `HexapodBackend` clamp layer) and the MG995 sizing from that branch, and check leg length and total mass against the MG995 stall torque BEFORE buying or cutting a frame.
- **Power (required)**: the 18 servos need a **separate high-current 5-6 V supply**, with a common ground to the Pi. **The Pi must never power the servos.** A physical power switch is the emergency stop.
- **Done when**: every box above is ticked by the owner; the Pi-software items (last two boxes) are already proven by 11a and 11b.

#### Stage 11e: I2C, PCA9685 and `ServoBackend` (servo code starts here, on approval)
- Owner enables I2C on the Pi (their `sudo`), `i2cdetect -y 1` shows both PCA9685 boards. Then: `body/servo_backend.py` with its calibration table (per-joint centre, sign, offset, channel, pulse range; never in `config.py`), `scripts/calibrate_servos.py`, `tests/test_servo_backend.py` against a fake driver. Nothing is enabled at boot.
- **Tests**: clean-frame angle to servo angle round trip for every joint; the ±90° hard limits map inside the servo's physical range; left-side sign flips come only from the table; the joint slew limit applies.
- **Done when**: tests pass, the calibration is run with the robot held off the ground and the dated table committed.

**Day-1 bench checklist (first day with real hardware)**: written now, NO servo code until the owner says the hardware is on the bench. Rules: no `sudo` from Claude (give the commands), only `~/hexa` is touched, nothing enabled at boot. Stop at the first step that does not match.
1. **I2C check (owner-run `sudo`, Claude lists the exact commands)**: `sudo apt install i2c-tools`; I2C enabled with `dtparam=i2c_arm=on` in `/boot/firmware/config.txt` and a reboot; `i2c-dev` loaded; user in the `i2c` group (already is); `/dev/i2c-1` exists. With the PCA9685 powered from the Pi's 3.3 V logic side ONLY (no servo power connected), `i2cdetect -y 1` shows it at **0x40** (default, all address jumpers open). A second board needs a different address (solder jumpers A0-A5, for example 0x41): the owner tells Claude how it is jumpered, and `i2cdetect` must show both. Nothing else on the bus may answer at those addresses.
2. **Servo power rail, wired before any servo is plugged in**: the servo supply goes through a **fuse** then the **kill switch** to the PCA9685 V+ terminals; **common ground** between the supply, the PCA9685 and the Pi; a **large electrolytic capacitor** (about 1000 uF per 4-6 servos, rated above the supply voltage) across V+ and GND at each board; the **Pi powered from its own supply**, never from the servo rail. Check with a multimeter, servos unplugged: kill switch off = 0 V on V+, on = the supply voltage (5-6 V, not above the servo rating), polarity correct, no short between V+ and GND, the fuse is the right rating.
3. **One servo first**: horn REMOVED (nothing attached to the shaft), mounted on the bench, plugged into one channel of the first board. Signal only at its **measured centre** (the pulse that puts it at mid travel, measured, not assumed to be 1500 us), power on through the kill switch, check it holds quietly and the supply current is low. Then slow moves a few degrees either side, then step out to the range limit in small steps, listening for stall or buzzing and watching the current, never past the range the servo and the hard limits allow. Kill switch within reach throughout.
4. **Only then calibration of that joint**: centre, sign, offset and pulse range for that one joint go into the calibration table in `body/servo_backend.py` (written only after the owner approves 11e); repeat servo by servo with the robot held off the ground.

**Where to wire and what to measure**: `docs/HARDWARE.md` now has the Pi header pins (3V3 pin 1, SDA1 pin 3, SCL1 pin 5, GND pin 6), the PCA9685 pins, a proposed channel assignment (right side on `0x40`, left side on `0x41`) and fill-in tables for the minimum, centre and maximum pulse and the mechanical angle range per servo and per joint. The owner fills them in on the bench.

**Answers the owner owes before servo code (all required)**:
- **PCA9685**: how many boards, and each board's address and how it is jumpered (for example 0x40 and 0x41); which channel each of the 18 servos uses (leg, joint) and the channel order of the wiring.
- **MG995**: how many servos in hand (need 18 plus spares), whether they are genuine MG995 (metal gear, and the measured pulse range and centre of a sample).
- **Servo supply**: type (battery chemistry and cell count, or a mains power supply), voltage and **amps** (the continuous and the peak stall current it can deliver), and the wire gauge to the boards.
- **Fuse and kill switch**: ratings (fuse amps and type, switch current and voltage rating) and where in the line each sits.
- **Frame and legs**: the actual coxa, femur and tibia lengths, the body radius and leg mount angles (compare with `config.py`), the weight with battery, and how the horns are mounted (angle at neutral, left/right mirroring).

#### Stage 11f: ground tests
- With the robot on a stand first, then on the ground, starting with `stand`; kill switch tested.
- **Done when**: stand, sit, walk and stop behave like the sim, the kill switch removes servo power while the Pi keeps running, and pulling the brain process stops the robot via the watchdog.

### Step 12 — Phone web UI (12a control page, 12b robot-mic hold-to-talk, 12c phone mic later)
- **Goal**: drive and talk to the robot from a phone on the local Wi-Fi.
- **Shared rules (12a and 12b)**: the web server runs in the brain process and talks to the body ONLY through the `Bridge`, exactly like `scripts/control_window.py`; it is a status-hub subscriber, not a second reader of `status_queue`. It never imports the controller, the gait or a backend (a test enforces it). Clamps stay in the controller. The page never sends raw joint data. One controller at a time. Stop on disconnect, with the body watchdog as the last line of defence.

#### Step 12a — Control page, no audio (hardware-free, built against the sim)
- **Dependency**: `websockets` (pure Python, no sub-dependencies). It is ALREADY installed because `vosk` requires it, so nothing new is downloaded (free, offline). One `serve()` call answers plain HTTP (`process_request` returns the static page) and the WebSocket on the same port; aiohttp or starlette+uvicorn would add several packages for no gain. `requirements.txt` lists it explicitly (`websockets>=13`, the asyncio API). The page is ONE static `web/static/index.html` (inline JS and CSS, no build step, no CDN, no external fonts).
- **Switches**: `main.py --web` (port `config.WEB_PORT`), binds `127.0.0.1`; `--lan` (implies `--web`) binds `0.0.0.0` and prints the URL(s) to open on the phone.
- **Files**: `web/__init__.py`, `web/protocol.py` (pure: message validation and walk-parameter building), `web/session.py` (pure, injected clock: PIN check with rate limiting, single controller, server deadman, stop on disconnect), `web/server.py` (websockets server thread, status relay), `web/static/index.html`, `scripts/web_check.py` (headless WebSocket client), `scripts/measure_web.py`, `tests/test_web_protocol.py`, `tests/test_web_session.py`, `tests/test_web_server.py`, `tests/test_web_app.py`; `config.py` gets the `WEB_*` constants; `HexaApp` gets the web server option.
- **Auth**: PIN from `HEXA_WEB_PIN` or `config.WEB_PIN`; if neither is set a random 6-digit PIN is generated at start and printed on the terminal only (never logged, never committed). Compared with `hmac.compare_digest` on the WebSocket handshake (`?pin=`), with a per-client-address lockout after `WEB_PIN_MAX_FAILURES` wrong tries. A second client while one is in control is refused at the handshake (HTTP 409). The `Origin` header must match the `Host` (blocks other websites from driving the robot through the phone's browser).
- **Messages (page to server, JSON)**: `walk {forward, strafe, yaw, speed?}` (numbers, clamped to [-1, 1], missing = 0; ONE combined message for all held buttons), `stop`, `stand`, `sit`, `wave`. Anything else, an unknown field, a non-number, NaN or a bool is rejected with an error reply and nothing reaches the bridge. The server turns the first `walk` into a bridge `walk` and each unchanged repeat into a bridge `heartbeat`. `stop` always goes through `bridge.send(stop)` (the `stop_event` fast path).
- **Safety on the server**: the page repeats the walk message at about 10 Hz while any move button is held; if none arrives within `WEB_DEADMAN_S` (0.3 s) while moving the server sends `stop`; a closed socket sends `stop`; the page itself sends `stop` on pointercancel, visibilitychange and blur.
- **Page**: hold-to-move buttons with pointer events (multi-touch, so forward + turn together), stand / sit / wave, a large always-visible STOP, a live status line from the status hub, no scroll, no text selection, no long-press menu on the controls.
- **Tests** (fake bridge, fake clock, no real network where possible): validation table; combined buttons give one walk with strafe and yaw; deadman; disconnect; hidden page; wrong PIN; rate limit; second controller refused; STOP works with a walk queued; no controller/gait/backend import; default bind `127.0.0.1`; one real-process test (headless body moves, disconnect stops it, clean exit).
- **Limits**: plain HTTP on a LAN, so use it only on a network you trust until 12c adds HTTPS.
- **Done when**: tests pass, `scripts/web_check.py` drives the headless body, and the measurements (press to first foot-target change, idle CPU, peak temperature) are reported.
- **Status**: approved, tagged `v0.2-web-a`. **NOT yet verified on a real phone**: the page's touch handling (multi-touch, pointercancel, long-press menu, screen lock, Wi-Fi loss) has only been exercised through the protocol and server tests, never on a real touch screen. Check it on a phone before trusting it with hardware.

#### Step 12b — Hold-to-talk with the ROBOT's microphone (no browser audio, no HTTPS)
- **Scope (revised)**: the page gets a hold-to-talk button, a typed "say" box and a live log. The audio is the robot's own microphone (`MicSource`), so you speak near the robot; the phone sends only button messages. No browser audio, no HTTPS, no certificate. Phone-microphone audio over HTTPS (self-signed certificate, network `AudioSource`, Vosk on the Pi) moves to **Step 12c (later, optional)**.
- **Protocol (page to server)**: adds `ptt_press`, `ptt_release` (no fields) and `say {text}` (a string, 1 to `WEB_SAY_MAX_CHARS` characters after stripping, no control characters). They go to `HexaApp.set_listening()` and `VoiceLoop.submit_text()`; they are NOT bridge messages, the body schema does not change, and unknown fields are still rejected. `WEB_MAX_MESSAGE_BYTES` grows to fit a full `say`.
- **Typed text = spoken text**: `VoiceLoop.submit_text(text)` queues a `typed` event on the router worker, which routes it with `route()` and acts on it exactly like a final result (a command goes to the bridge, a stop goes out first, chat runs only while the body is idle, otherwise "tell me after I stop" and no LLM call). A typed stop works even when not listening. At most one `say` per `WEB_SAY_MIN_INTERVAL_S`.
- **Safety on the SERVER** (`web/session.py`): a press is forced to release after `WEB_PTT_MAX_S` (10 s); a disconnect, a `stop` message (the page sends it on hidden, blur and pagehide, and its STOP button sends it plus `ptt_release`) and server shutdown release listening. Only the controller can press; the server releases only what the web pressed. In ptt mode a voice "stop" works only while listening, and the page says so next to the button.
- **Availability**: with `--no-mic` or `--listen always` (or `--audio-file`) there is nothing to hold: the server tells the page why (`hello.ptt`), the button is disabled with that reason, and a press is answered with an error. The text box still works.
- **Events to the page**: a bounded drop-oldest `brain/event_hub.py` (like the `StatusHub`): `listening {on}`, `heard {text}`, `route {...}`, `said {text}` (every sentence or phrase hexa speaks, via `LoggedPlayback`), plus the existing `status` messages. The server pump drains its subscription into the client's bounded outbox, so a slow phone never blocks the voice loop (publishing never waits).
- **Page**: big hold-to-talk button (pointer events; release on pointerup, pointercancel, lost capture, blur, visibilitychange), LISTENING indicator, scrolling log (heard, decided, said, status), text box with send, the note that a voice "stop" works only while listening.
- **Files**: `brain/event_hub.py`, `tests/test_event_hub.py`, `tests/test_web_voice.py`; changes in `config.py`, `web/protocol.py`, `web/session.py`, `web/server.py`, `web/static/index.html`, `brain/voice_loop.py`, `brain/app.py`, `scripts/web_check.py`, `scripts/measure_web.py`, `README.md`, `docs/`.
- **Tests** (fake app, fake clock): message validation; auto-release at the maximum; disconnect and hidden page release; events in order and oldest dropped for a slow client; typed text takes the spoken path (stop, chat while walking); `--no-mic` disables ptt; second controller rejected; one real-body test with a `FileSource` WAV (press, audio fed, release, the robot sits).
- **Done when**: tests pass; measurements (release to command-sent latency, idle CPU, peak temperature) are reported; the README explains the phone use and that the microphone is the robot's.

#### Step 12c — Phone microphone over HTTPS (later, optional)
- A push-to-talk button that streams 16 kHz mono audio over a WebSocket into the network `AudioSource` (Vosk on the Pi, no browser or cloud recognition). HTTPS with a self-signed certificate generated by a script, never committed (the browser microphone needs a secure origin); the page explains the one-time warning. Control takeover only with the PIN. Optional Pi-hosted Wi-Fi hotspot for demos, with the same HTTPS and PIN rules.
- **Done when**: from a phone, push-to-talk "walk forward" works over HTTPS and the wrong PIN is refused.

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
| Robot hears itself and obeys | `speaking` gate plus tail (Step 8); push-to-talk and barge-in (Step 10) |
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
| Piper slows the body (measured headless, Piper kept 100 % busy: body mean tick 10.6 ms standing to 22 ms, 12.5 ms walking to 30 ms, tick rate 50 to 31-37 per s; with the GUI viewer open walking goes 14.6 ms to 90 ms, 11 ticks per s, and Piper's real-time factor rises to 1.9). `nice` for Piper, `taskset` for Piper (ORT sets its own thread affinity) and pinning the body to the other core did not help | Pre-rendered phrases cost no CPU at speaking time; the gait is wall-clock driven so a slow tick degrades smoothness, not speed; real speech is mostly idle. To be re-measured on the Pi 5 (4 real cores). If it still hurts: one onnxruntime thread (piper1-gpl Python API) or a smaller voice. **Owner decision (Step 7 approved): option 1, lean on pre-rendered phrases and short replies, re-measure on the Pi 5 in Step 11, no design change now; `PIPER_NICE` and `PIPER_CPU_LIST` stay off by default.** |
| Ollama chat is too slow and too hot on the dev laptop (measured, `qwen2.5:0.5b`, Ollama 0.35.1 in Docker, all cores, headless body; the container limited to 1 core was not measured) | Median 1.5 tokens/s (1.2-1.9) against the 3 tokens/s bar; first token 0.4-1.6 s once loaded (a cold load took over 20 s and tripped `OLLAMA_TIMEOUT_S`, so `voice_cli` warms the model up at start); container RSS about 500 MB; temperature peaked at 90 C (the guard killed two earlier runs at 85 and 79 C; the owner then asked for runs without a kill threshold; the laptop did not shut down). Body mean tick: 7.6 ms idle, 7.6 ms with Vosk (2 recognizers) listening and Piper loaded and idle, **15.5-16.7 ms (worst 130-205 ms) while the LLM generates** on top of that: under the 20 ms line but about 2.2x the idle cost. Decision: develop against `FakeChat` here; time the real model, and 1.5b, on the Pi 5 (Step 11); never use `-cloud` models (offline rule) |
| The dev laptop overheats and powers off (`acpitz` critical 87 C; idles near 59 C; measured with Vosk the run peaked at 79 C) | `scripts/cool_run.py` waits until cool and kills the job at 82 C; short runs with pauses; one heavy process at a time; test files singly. Vosk's second (grammar) recognizer measured: 21-23 % of a core instead of 13-19 %, body tick unchanged (7.0 vs 6.6 ms), same 178 MB |
| Vosk cost and voice latency (dev laptop, headless body walking, measured Step 8) | Vosk alone: 15-22 % of a core, decode real-time factor 0.07-0.11. Body mean tick: 7.4 ms with no voice, 8.7 ms with Vosk on silence (under the 20 ms line), 34 ms with Vosk plus Piper busy (the Piper cost again). Spoken "walk forward": end of speech to command sent median 0.86 s (worst 0.89 s; Vosk waits for silence, plus up to one 250 ms block), command sent to first foot-target change 0.26 s, total 1.12 s. Options if it feels slow: smaller `AUDIO_BLOCKSIZE` (up to 150 ms back), a shorter Vosk endpoint, a command grammar |
| Piper slow on the dev laptop (measured, Core i3 M380, no AVX, `en_US-amy-low`) | AVX count 0 but Piper runs. One process per sentence: about 1.5 s model load per call, end-to-end real-time factor 1.4 (ruled out). Long-lived process: startup about 2.6 s once, then real-time factor 0.6-1.0, first audio 0.6 s for one word, 1.8 s for a short sentence, 4.2 s for a 4 s one (Piper emits a sentence only when it is done). So Piper is ALWAYS a long-lived process; fixed phrases and fillers are pre-rendered; replies are split into sentences with prefetch; the Pi 5 (NEON, faster core) is expected to be well under 1.0 (Step 7) |
| Piper/Vosk models missing on a fresh checkout | `scripts/fetch_models.sh`; startup check that names the missing file |

## 11. Out of scope

Vision, SLAM, navigation or obstacle avoidance; IMU balance control and rough terrain; gaits other than tripod; wake-word detection, speaker identification, multi-language; cloud STT/TTS/LLM (and browser speech recognition); a web control panel before Step 12; mechanical design and CAD; battery management; over-the-air updates. Barge-in, hardware and the phone web UI are in scope but only at Steps 10, 11 and 12.

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
