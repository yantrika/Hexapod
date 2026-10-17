# Architecture

How the pieces fit, the rules that keep the robot safe, and the numbers we measured. For wiring
and servos see `HARDWARE.md`; for what is still to build see `ROADMAP.md`.

## Two processes

```
 BRAIN process (threads)                               BODY process
 ┌──────────────────────────────────────────┐        ┌────────────────────────────┐
 │ microphone -> STT thread (Vosk)          │        │ control loop, 50 Hz        │
 │            -> router / chat worker       │ command│  - drain the queue         │
 │ speaker  <- playback thread (Piper)      │ ──────▶│  - drop stale, stop wins   │
 │ status hub thread <─────────────────────────────── │  - watchdog                │
 │ phone page server (web/), heartbeat      │ status │  - controller -> gait ->   │
 └──────────────────────────────────────────┘ queue  │    kinematics -> backend   │
                                                      └────────────────────────────┘
```

- The **brain** hears, understands, speaks and chats. It never touches a joint angle.
- The **body** moves the legs. It never touches audio or text.
- The only link is `bridge.py`: `command_queue` (brain to body), `status_queue` (body to brain)
  and a shared `stop_event` that makes "stop" fast. Both queues are small and drop the oldest item
  when full; `stop` cannot be lost because `stop_event` carries it independently.
- The body is one of three backends behind `HexapodBackend` (`body/backend.py`):
  `sim_backend.py` (PyBullet, 240 Hz), `dryrun_backend.py` (no physics, no hardware, logs the
  joint targets; used on the Pi before servos) and `servo_backend.py` (real servos). Nothing
  outside the backends knows which one is loaded, and pybullet is imported lazily.

### The window and the real robot share one path

```
 voice / typed text / phone page / keys
        -> brain (router, motion keeper) -> Bridge -> body: arbitration -> Controller
        -> gait -> kinematics -> HexapodBackend.set_joint_targets (hard clamp)
        -> PyBullet window   |   dry-run log   |   servo backend (slew, pulse window, driver)
```

Everything above the backend is the same code in all three cases. A feature that works in the
PyBullet window (commands, stop, watchdog, chat gating, persona, phone page) therefore exercises the
same router, Bridge, controller and gait that drive the hardware. What the window does **not**
test: servo calibration and signs, the slew limit on real pulses, power and brown-outs, the kill
switch and signal shutdown on real servos (see `HARDWARE.md`).

## Data flow

```
 mic ──▶ AudioSource ──▶ [push-to-talk gate] ──▶ [self-hearing gate] ──▶ Vosk (free text + grammar)
 (voice/audio.py)        (voice/ptt.py)          (voice/stt.py)            │ partial with a stop word: STOP now
                                                                            ▼ final
                                                              brain/stt_decision.py ──▶ brain/router.py
                      ┌──────────── stop / command ────────────────────────────────────────┤
                      ▼                                                                    │ chat / persona
              brain/brain_loop.py ──▶ bridge.py ──▶ BODY process                           ▼
              (+ motion_keeper heartbeats)                                      only if the body is idle:
                      ▲ statuses                                                brain/chat.py ──▶ Ollama
              brain/status_hub.py ◀── status queue ◀── BODY                         │ sentences (output check)
                      ├──▶ brain/dialogue.py ─ pre-rendered phrases ─────────▶ voice/playback.py ──▶ speaker
                      └──▶ web page, control window, logs/hexa.log
```

`main.py` builds this (`brain/app.py`, class `HexaApp`) and owns start and stop order. SIGINT,
SIGTERM and SIGHUP stop the robot, silence it, cancel chat, join the threads and end the body.

"Walk forward": audio blocks reach Vosk (the self-hearing gate drops them while Arya speaks);
Vosk runs a free-text and a grammar recognizer; a stop word in a partial result stops the robot at
once; at the end of the sentence `stt_decision.decide()` picks the text to trust; `router` returns
stop, command, chat or persona; `brain_loop` sends the command and `motion_keeper` sends
heartbeats so the walk continues; the body answers with statuses; `dialogue` speaks a
pre-rendered phrase from the status (never from assumption).

Chat: only when the body is idle, otherwise "tell me after I stop" and the LLM is not called. The
reply streams in; every finished sentence passes `output_check` and goes to the speaker queue. A
command, a stop or any motion cancels the reply and clears the speech. If the LLM is down Arya
says "I can't think right now".

## Messages (`bridge.py`)

Frozen dataclasses (picklable for `multiprocessing`); `timestamp` is `time.monotonic()`.

| `Command.action` | params | notes |
|---|---|---|
| `walk` | `direction` fwd/back, `speed` 0-1; optional `strafe`, `yaw` in [-1, 1] (+1 = left / counter-clockwise) | continuous until stop, a new motion command or the watchdog; clamped in the controller only |
| `turn` | `direction` left/right, `angle_deg` 1-180 | finishes, then `done` |
| `stand`, `sit`, `wave` | none | `busy` while a transition runs |
| `stop` | none | preempts everything; never rejected |
| `heartbeat` | none | refreshes the watchdog only |

`Status.status` is `accepted | rejected | done | busy | fallen | error`, with `ref_seq` (the command
it answers) and `detail`. Rejection reasons are a closed set: `stale`, `superseded`,
`already_in_state`, `invalid_params`, `invalid_state`, `unknown_action`, `fallen`.

## Safety rules (these hold in every mode and on every backend)

| Rule | Where |
|---|---|
| **`stop` always wins**, even over old messages; it holds the current pose | `body/arbitration.py`, `bridge.py` |
| Messages older than `MAX_MESSAGE_AGE_S` are dropped (a stale stop still runs) | `body/process.py` |
| **Watchdog**: walking or turning with no command or heartbeat for `WATCHDOG_TIMEOUT_S` stops the robot | `body/controller.py` |
| The body validates every command on arrival and does not trust the brain | `bridge.py`, `body/controller.py` |
| Two joint-limit tiers: hard (`JOINT_HARD_LIMITS_DEG`, one clamp in `HexapodBackend.set_joint_targets`) and soft gait limits | `body/backend.py`, `body/gait.py` |
| A fallen robot refuses all motion except `stop` | `body/controller.py` |
| Voice and brain never touch joint angles | `brain/`, `voice/` |
| **The LLM is never in the command path**: the router decides, only a `chat` result reaches the LLM, and only while the body is idle | `brain/router.py`, `brain/voice_loop.py` |
| Perception and the LLM can trigger only `stand`, `wave` and small in-place turns, never walking, only when idle | `brain/behaviour.py` (not built yet) |
| A grammar match is not trusted unless it is an exact known phrase; confidence alone is never the guard | `brain/stt_decision.py` |
| The robot ignores the microphone while it speaks | `voice/stt.py`, `voice/playback.py` |
| Only `voice/audio.py` touches audio input; only `voice/playback.py` touches audio output | `voice/` |
| Phone page: PIN, one controller, a deadman that sends `stop`, `stop` on every disconnect, no joint data | `web/session.py`, `web/protocol.py` |
| Real servos: refused unless `--backend servo` and a typed `yes`; every joint must be calibrated; one pulse path with an absolute window and a slew limit; staggered attach; powers up sitting; idle release; signals end in sit-then-release | `main.py`, `body/servo_*.py` |
| Persona hard limits in every mode, checked in the prompt and in code on every sentence | `brain/output_check.py`, `data/persona/limits.toml` |
| Privacy: facts only after validation; "forget me" deletes everything in one transaction; the memory database never leaves the machine; no face data in logs | `brain/memory.py`, `brain/arya.py` |

Body tick: stop fast path (`stop_event`) first, then drain the queue; a `stop` in the batch wins and
the rest are `rejected(superseded)`; other motion commands are latest-wins; a running
sit/stand/wave answers `busy`. Push-to-talk: a voice "stop" works only while listening; the
control window STOP button and Space are the always-available stop.

## Threads (brain process)

| Thread | Reads | Writes |
|---|---|---|
| `voice-stt` | microphone blocks | recognised results |
| `voice-worker` | recognised results, typed text | commands to the body, spoken phrases |
| `tts-synth`, `tts-playback` | sentences, audio clips | speaker, the `speaking` flag |
| `status-hub` | the body's status queue | every subscriber's own bounded queue |
| `dialogue`, `motion-keeper` | their status subscription | phrases, heartbeats |
| `chat-reply` | the LLM's streamed reply | sentences to the speaker queue |
| web server (own event loop) | phone messages | Bridge commands, voice controls |

Threads talk only through `queue.Queue` and one shared `speaking` event. Every queue is bounded and
drops the oldest item when full, so nothing can block the robot or the microphone.

## Arya (persona, memory, vision)

The persona `calm` (default), `funny` or `roast` changes only how Arya talks; commands, the router
and safety are identical in every mode. Built: `brain/persona.py` (prompt from
`data/persona/arya.toml`), `brain/arya.py` (mode, speaker, mood, forget flow), `brain/memory.py`
(the only code that opens `data/memory/arya.db`, SQLite, mode 600, thread-safe),
`brain/context.py` (prompt under a token budget), `brain/extractor.py` (facts from idle
conversations, validated by code), `brain/output_check.py` (hard limits on every sentence before
the TTS). The router runs first and does not know the persona: persona requests ("my name is X",
"forget me", "roast mode") are `RouteResult.kind == "persona"`, are looked at only after the stop
check and never reach the body or the LLM. One exception: the whole utterance "stop roasting"
switches to calm and, if the body is moving, sends a real `stop` first.

Planned (not built): a third process, VISION (webcam, face detection, recognition on new tracks
only), sends small events (`person_appeared`, `person_left`, `face_bearing`, never images) over its
own bounded queue; it never touches the Bridge, the body or audio. A rule-based behaviour layer is
the only code that turns perception into commands, and only `stand`, `wave` and small turns.

## Design decisions

- **Two processes, one narrow bridge**, so a hung brain cannot stop the control loop and the
  watchdog can stop a robot whose brain died.
- **Router decides, the LLM only chats**: a small local model is too slow and too unreliable to be
  in a safety path.
- **Clean joint frame**: kinematics, gait and the sim use 0 = neutral with no offsets or per-side
  signs. Centre, direction, offset and pulse window of each real servo live only in
  `data/calibration/servo_cal.json` (`body/servo_calibration.py`); board-level numbers are in
  `config.py`.
- **`config.py` is the single source of truth.** Leg order is `RF, RM, RR, LR, LM, LF` (clockwise
  from front-right); mount angles are clockwise from above, 0 = +X, and become math yaw only
  through `config.mount_yaw_rad`. Body frame: +X forward, +Y left, +Z up; metres and radians unless
  a name ends in `_DEG`. IK always returns the knee-up solution.
- **Wall-clock loops**: physics (240 Hz) and control (50 Hz) follow the clock, not iteration
  counts; a late loop runs several nominal 20 ms ticks, never one long one.
- **Piper is one long-lived process** (a process per sentence costs about 1.5 s of model load);
  fixed phrases are pre-rendered, never synthesized while speaking.
- **Two Vosk recognizers on the same audio** (free text for chat, a grammar built from the router
  for commands). The grammar forces matches on ordinary speech, so a result is trusted only when it
  is exactly one known phrase that the short free text agrees with.
- **The URDF is generated** from `config.py` (`body/urdf.py`) the first time the simulator needs
  it; it is never committed.

## Measured numbers

Dev laptop: Core i3 M380, 2 cores, no AVX, headless body. Pi: Raspberry Pi 5, 8 GB, Ubuntu
24.04, Python 3.12, dry-run body, no audio devices (Piper timed to memory, Vosk on generated
speech).

| What | Dev laptop | Raspberry Pi 5 |
|---|---|---|
| Body tick work, standing / walking (20 ms budget) | 7-8 ms (with the simulator) | 0.09 / 0.42 ms (no physics) |
| Body tick while Piper is busy | 10-34 ms | 0.7 / 1.5 ms |
| Body tick while the LLM generates | 15-17 ms (worst 130-205 ms) | not measured |
| Piper (`en_US-amy-low`) real-time factor, first audio | 0.6-1.0, 0.6 s (a word) to 4.2 s | 0.12, median 0.29 s |
| Vosk CPU, one recognizer / two (free + grammar) | 15-22 % / 21-23 % of a core | 8-14 % / 13-17 % |
| Vosk CPU with push-to-talk idle | 1 % | 0 % |
| End of speech to command sent ("walk forward") | 0.86 s | 0.84 s |
| Command sent to first foot-target change | 0.26 s | 0.19 s |
| "sit down" WAV end to first joint change | not measured | median 0.90 s |
| Phone page: press to first joint change (localhost / Wi-Fi) | not recorded | 39 ms / 51 ms median |
| Release to command (500 ms of it is `PTT_TAIL_S`) | see Pi | median 701 ms |
| Chat `qwen2.5:0.5b` | 1.5 tokens/s (bar: 3), first token 0.4-1.6 s | not measured yet |
| Command accuracy, small US Vosk, one owner's voice (117 tries) | commands 72 %, stops 78 %, no dangerous false positive, no wrong motion | not measured |
| Temperature | idle 56-65 C, peaks 81-90 C under load | idle 43-44 C, peak 67 C |

The end-to-end delay after you stop speaking (about 0.9 s) is dominated by Vosk waiting for
silence, not by compute. On the laptop, numpy's BLAS threads made ticks 7x slower, so
`config.py`, `body/__init__.py` and `tests/__init__.py` pin them to one thread.

## Known limits

- A voice stop works only while listening in push-to-talk mode; use the STOP button, Space, or
  type `stop`. Single words are unreliable on the small Vosk model ("stop" is the reliable stop
  word; two-word commands are reliable). Vosk has no echo cancellation: use a headset for barge-in.
- The small US model writes "Arya" as "aria"; the router drops a leading "aria". Names heard once
  are often wrong ("Priya" as "prayer"); name confirmation is planned (`ROADMAP.md`).
- The real LLM has not been timed on the Pi. No microphone or speaker has been tried on the Pi.
- The phone page is plain HTTP (trusted networks only) and has no audio of its own.
- No wake word. No vision process yet.
