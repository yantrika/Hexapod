# Development

Setup, layout, tests, how to check a feature in the 3D window, lint, and how to add or change
things. Rules for AI agents are in `../AGENTS.md`.

## Setup

Python 3.11 on the dev machine (the Pi runs 3.12; CI runs both). Linux.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-sim.txt   # requirements-pi.txt on the Pi
scripts/fetch_models.sh        # Piper, Vosk (US; --all-models adds Indian English), fixed phrases
```

Models go in `assets/` (not committed). Tests that need a model or PyBullet skip themselves when
it is missing. The simulator's robot file (`assets/urdf/hexapod.urdf`) is generated from
`config.py` the first time a simulator starts and whenever it is stale; it is not committed.

## Layout

```
main.py            the app (HexaApp from brain/app.py): --gui --web --backend ...
config.py          every constant, in sections (below)
bridge.py          Command / Status messages, queues, stop_event
body/              the body process
  process.py         50 Hz loop, BodyProcess handle, make_backend
  controller.py      state machine, all clamps       gait.py        pure foot planner
  kinematics.py      IK (knee-up) / FK               poses.py       stand and sit poses
  arbitration.py     stop wins, latest wins          clock.py       swappable clock, fixed-rate loop
  backend.py         HexapodBackend contract + the one hard-limit clamp
  sim_backend.py     PyBullet     dryrun_backend.py  logs targets    urdf.py   URDF generator
  servo_backend.py   real servos  servo_calibration.py  servo_drivers.py  servo_safety.py
brain/             router, stt_decision, voice_loop, brain_loop, motion_keeper, status_hub,
                   event_hub, dialogue, chat (+ sentences, context), persona, arya, memory,
                   extractor, output_check, app, startup, logsetup, transcript_log, commandline
voice/             audio (only audio input), stt (Vosk + self-hearing gate), tts (Piper),
                   playback (only audio output), ptt
web/               protocol (validation), session (PIN, controller, deadman), server, static/index.html
vision/            camera, detector (YuNet + bearing), tracker (IoU), process (events), recognizer (P4 stub),
                   events, stats
scripts/           tools you run (below)
data/              persona/*.toml, calibration/servo_cal.json, memory/ (private, ignored)
deploy/            hexa.service (dry-run unit; not installed)
tests/             body/ brain/ voice/ web/ vision/ scripts/ sim/ + fakes.py, vision_fakes.py, conftest.py
```

Scripts:

| Script | Use |
|---|---|
| `fetch_models.sh`, `prerender_phrases.py` | models and spoken phrases |
| `sim_demo.py` | stand/sit/scripted motion in the window (`--script "walk:5,turn:90,stop"`, `--headless`) |
| `teleop.py`, `control_window.py`, `joint_jog.py` | keyboard drive, key/button window, joint sliders |
| `cli.py` | typed commands: through the router (default) or raw Bridge commands (`--raw`) |
| `say.py`, `mic_check.py`, `stt_check.py`, `web_check.py` | check speech out, microphone, recognition, the phone page (`--talk-wav FILE` streams a WAV as the phone's microphone) |
| `fetch_vision_models.py`, `vision_check.py`, `vision_bench.py` | vision models (SHA-256 checked); webcam check with `--debug-preview`; headless fps/CPU numbers, `--load N` busy workers |
| `persona_eval.py` | a fixed conversation per persona mode against a chat model |
| `measure_chat.py`, `measure_voice.py` | time Ollama; time Piper, Vosk and the body under voice load |
| `torque_report.py` | servo torque/speed needs from the sim (writes to `.tmp/`) |
| `servo_jog.py`, `servo_center.py`, `servo_teleop.py` | servo calibration, horn fitting, keyboard drive of the real robot |
| `sync_to_pi.sh`, `build_wheelhouse.sh`, `pi_dryrun.sh` | put the code on the Pi, build its one wheel, run it in tmux |

## Tests

```bash
pytest                          # everything not marked timing, audio or llm
pytest tests/brain              # one folder
pytest tests/brain/test_router.py::test_name
pytest tests/sim                # needs PyBullet; run before merging any motion change
pytest -m pybullet              # the PyBullet-dependent tests that live in other folders
pytest -m timing | -m audio -s | -m llm -s
ruff check . && mypy .          # run both before committing
```

If a machine overheats during the full suite, run it in smaller batches (a folder at a time).

| Marker | Meaning |
|---|---|
| `timing` | wall-clock bound; needs a quiet machine |
| `audio` | real Piper binary and voice, no speaker |
| `llm` | a running Ollama with the configured model pulled |
| `pybullet` | builds the simulated body; skipped where PyBullet is not installed |

Tests are written against fakes (`tests/fakes.py`: fake clock, fake backend; `tests/servo_fakes.py`:
fake I2C/sysfs/gpiod). Inject clocks instead of sleeping. Tests never touch the real memory database
(`conftest.py` points `HEXA_MEMORY_DB` at a temp file). Real-process tests (`main.py`, the body
process, the phone page) wait for events, not fixed sleeps. Router tests must keep their negative
cases ("I sat down for lunch", "turn up the music"). Kinematics: test `FK(IK(p)) ≈ p`, never
`IK(FK(a)) == a`.

**CI** (`.github/workflows/ci.yml`) runs ruff, mypy and `pytest` on Python 3.11 and 3.12 with no
PyBullet, no models and no hardware: roughly 1470 tests run and about 30 skip. It does **not**
exercise the simulator, so run `pytest tests/sim` locally before merging a change to motion,
gait, kinematics, the controller or the backend contract.

## Testing a feature in the window (and on the phone page)

Features are checked in the simulator because the hardware is not always there. The window shows
the same controller, gait and Bridge the real robot uses (`ARCHITECTURE.md`), so what works here
works on the dry-run body and, after calibration, on the servos. Needs a display; on an old Intel
GPU prefix `MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330`. The window is a viewer
only (no side panels, no text drawn in it).

| Feature | Command | Look for |
|---|---|---|
| Walk, turn, strafe, stop | `python scripts/sim_demo.py --script "stand,walk:5,turn:90,strafe:3,stop,sit"` | alternating tripods, a smooth turn, stop holds the pose, no tipping |
| Drive by hand | `python scripts/control_window.py` (click it; Space = stop) or `python scripts/teleop.py` | keys map to motion; release stops; the speed keys change the pace |
| Joints and limits | `python scripts/joint_jog.py` | each joint moves in its sensible direction and stays inside its limits |
| Typed language | `python scripts/cli.py --gui`, type `walk forward`, `stop`, `sit down`, `I sat down for lunch` | commands go to the body, chat sentences do not move it; statuses print |
| The whole app | `python main.py --gui --web --chat fake` (add `--no-mic --no-speak` without audio) | push-to-talk with Enter, or type in the phone page's box: a spoken or typed "walk forward" moves the window; "stop" always stops |
| Chat gating | with the app above, say `walk forward`, then ask a question | while walking: "tell me after I stop", no chat; after `stop`: a scripted reply |
| Persona | `--mode roast`, or the page's Persona buttons; say "roast mode", "my name is Sam", "forget me" | the mode changes only how it talks; "forget me" asks for a yes; "stop roasting" while walking also stops the walk |
| Phone page | `python main.py --gui --web --chat fake`, open the printed address, enter the PIN | hold an arrow: the robot walks in the window; release, close the tab or lock the phone: it stops; a second device is refused; Talk and the text box work |
| Without a phone | `python scripts/web_check.py --pin <PIN> --forward 1 --seconds 2` | the same page protocol driven from a script |
| Phone microphone | `python main.py --web --audio-source phone --chat fake --no-speak`, then `python scripts/web_check.py --pin <PIN> --talk-wav sit_down.wav` | the robot sits; `heard`, `route` and `said` events print; a WAV saying "stop" stops a walk |
| Servo tools without hardware | `python main.py --backend dryrun` and `tail -f logs/dryrun.log` | the per-leg joint targets the gait would send |

A headless test (in `tests/sim/test_sim_backend.py`) checks the pieces the window depends on: a
fresh checkout generates its URDF, the URDF loads in PyBullet, the camera constants exist, and
`--gui` is refused with `--backend dryrun|servo`. On a machine with a display,
`python scripts/sim_demo.py --pose stand --duration 2` opens the window for two seconds and exits 1
(with the reason) if it cannot, so a broken viewer is noticed.

## Phone app protocol

For whoever builds or changes the phone app (the Flutter app is not in this repo). It speaks to the
server of `web/server.py` over one WebSocket, `ws://<robot>:8765/ws?pin=<PIN>`. Plain `ws://` is
enough: the app is not a browser, so no HTTPS and no certificate. The robot must run with
`--audio-source phone` (and `--lan`); with the default `--audio-source robot` the app is refused
phone audio with a clear error. A wrong PIN is HTTP 401, a lockout 429, another phone in control
409. There is one controller; the PIN guards the control.

**Server to app (JSON text frames).** `hello` first: `audio` is `{"available": true, "rate": 16000,
"channels": 1, "format": "s16le", "min_frame_ms": 20, "max_frame_ms": 100}` or `{"available":
false, "reason": ...}`, and `ptt.max_s` is the listening limit. Then `status`, `deadman`, `error`
(`reason`) and these events: `listening` (`on`), `partial` (`text`, phone audio only, at most four
a second), `heard` (`text`, the final text), `route` (`route`: stop | command | chat | persona,
`action`, `text`, `early_stop`), `said` (`text`, each sentence of Arya's reply) and `ptt_timeout`
(the server ended the press after `ptt.max_s`).

**App to server.**

| Message | When |
|---|---|
| `{"action":"ptt_press","audio":{"rate":16000,"channels":1,"format":"s16le"}}` | the talk button goes down. The audio object must be exactly this; anything else (another rate, stereo, another format, an extra field) is rejected. Without it the press is refused in phone mode. |
| binary frames of raw little-endian 16-bit PCM, 16 kHz, mono | while pressed, 20-100 ms each (640-3200 bytes, whole samples), paced like a microphone. Shorter frames are accepted but count as 20 ms; longer ones are refused. |
| `{"action":"ptt_release"}` | the button goes up (stop the audio first, then send it) |
| `{"action":"stop"}` | the STOP button, any time. It also ends listening. |

Limits: audio is accepted at most 1.5 times faster than real time (plus 1 s of saved-up burst);
extra frames are dropped and one `error` (throttled to one a second) tells the app. Frames outside
a press (the last ones in flight after a release, or after the 10 s limit) are dropped and counted,
never heard. Frames from anyone but the controller are dropped. 20 rejected messages or malformed
frames close the connection (a `stop` goes out first, as on every disconnect). Nothing the app
sends can reach the body except through the router and the controller's own rules.

**What the app must do.**

- Hold-to-talk: on press send `ptt_press` and start recording 16 kHz mono PCM16, sending each chunk
  as a binary frame (re-chunk to about 50 ms if the plugin's chunks are not 20-100 ms); on release
  stop the recorder, then send `ptt_release`.
- Release (stop the recorder, send `ptt_release`) on app background, lost focus, a pointer
  cancel and a lost connection. The server also releases on a disconnect and sends `stop`.
- Show listening state (`listening`), the partial and final text (`partial`, `heard`), the route
  result (`route`) and Arya's reply (`said`); show `error` reasons.
- Keep a STOP button visible on every screen.
- Android: `<uses-permission android:name="android.permission.RECORD_AUDIO"/>`, ask at runtime
  with a clear reason; allow cleartext traffic to local addresses only (a `network_security_config`
  with `cleartextTrafficPermitted` for the robot's address or `.local`/private ranges, not for all
  hosts). iOS: `NSMicrophoneUsageDescription` and `NSLocalNetworkUsageDescription` in `Info.plist`.

## Lint, types, commits

`ruff` (line length 100) and `mypy` (strict defs) are configured in `pyproject.toml`; both must pass.
`snake_case` files and symbols, `PascalCase` classes, `UPPER_SNAKE_CASE` constants, 4 spaces for
Python and 2 for markup/config, full-word identifiers. Conventional Commits
(`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`), imperative, subject under 72
characters. A pull request states the summary, why, what you tested, and screenshots or a short
clip for a simulation change. Hardware work notes the power setup.

## `config.py`

Sections in file order: filesystem, body (geometry, joint limits, motion clamps, sim model,
controller), gait, safety and timing (bridge, watchdog, queues), voice (router, listening, audio,
models, chat), web (phone page), persona (modes, memory, context, extractor), hardware (servo
backend, dry-run log), GUI. Never hard-code a value that belongs there; bound values with
`config.clamp`. Changing geometry: edit the body section, then `pytest tests/body/test_kinematics.py
tests/body/test_urdf.py tests/test_config.py` (the URDF regenerates itself) and re-check walking
in the window.

## Changing things

| Change | Edit | Then |
|---|---|---|
| Vosk model | `VOSK_MODELS` / `VOSK_MODEL_DEFAULT` (find the real name at alphacephei.com/vosk/models) | `scripts/fetch_models.sh`, `python scripts/stt_check.py --model us`. The grammar needs `graph/HCLr.fst` and `graph/Gr.fst`, else `STT_USE_GRAMMAR = False` |
| Piper voice | `PIPER_VOICE` | `rm assets/phrases/*.wav && scripts/fetch_models.sh`, `python scripts/say.py "Hello"` |
| Spoken phrases | `TTS_PHRASES` (one sentence each) | `python scripts/prerender_phrases.py --force`; a status-to-phrase mapping is a small table in `brain/dialogue.py` |
| Voice commands | `ROUTER_PHRASES`, `ROUTER_ALIASES` (exact-only), `STOP_WORDS`, `ROUTER_FILLERS`, `ROUTER_THRESHOLD`; the Vosk grammar is built from these | add a test for the phrase and a negative test; `stt_check.py` for your voice |
| Recognizer trust numbers | `STT_STOP_CONF`, `STT_GRAMMAR_CONF`, `STT_GRAMMAR_EXTRA_WORDS` | `stt_check.py` once, then `--replay logs/stt_check-us.jsonl` after each change; first 0 dangerous false positives and 0 missed stops |
| Microphone / speaker | `MIC_DEVICE`, `SPEAKER_DEVICE` | `scripts/mic_check.py` lists devices |
| Walk, sit, wave | gait and clamp constants (`STEP_LENGTH_MAX_M`, `GAIT_PERIOD_S`, `BODY_HEIGHT_SIT`, `WAVE_*`, `GAIT_SOFT_LIMITS_DEG`) | watch it with `sim_demo.py`; `pytest tests/body tests/sim` |
| Safety timing | `WATCHDOG_TIMEOUT_S` (keep it above the heartbeat period), `HEARTBEAT_HZ`, `MAX_MESSAGE_AGE_S`, `FALL_TILT_DEG` | `pytest tests/body` |
| Chat model | `OLLAMA_MODEL` (local models only, never `-cloud`); you pull it | `python scripts/measure_chat.py --model <name>` (bar: 3 tokens/s, first token under 5 s). The code only knows `ChatBackend` in `brain/chat.py` |
| Arya's character, modes, blocked words | `data/persona/arya.toml` (prompt under 2200 characters), `data/persona/limits.toml` | `pytest tests/brain/test_output_check.py`, add a table line per word |

A new voice or brain feature: put the file in the right package, add `tests/<package>/test_<name>.py`
(fakes, injected clock), keep the audio-in/audio-out and "LLM never commands" rules, run ruff and
mypy, then check it in the window (above). A new top-level folder must be mentioned in
`README.md` or this file (`tests/test_docs.py` checks that).

## Persona evaluation

Model quality does not depend on the machine, so any machine with Ollama can score candidate chat
models:

```bash
ollama pull qwen2.5:1.5b && ollama pull llama3.2:1b && ollama pull gemma3:1b
python scripts/persona_eval.py --backend ollama --model qwen2.5:1.5b,llama3.2:1b,gemma3:1b --out persona_eval.jsonl
# Ollama on another machine: add --ollama-url http://<host>:11434
```

It runs 20 fixed cases per mode (normal, "seriously", help, and bait) and prints a table of
sentences, blocked sentences by category and bait cases that were blocked; read the JSONL for
quality. Tokens/s, first token and RAM still have to be measured on the Pi (`measure_chat.py`).

## Putting code on the Pi

`scripts/sync_to_pi.sh [--assets] [--dry-run]` (never `--delete`; `data/memory/` and the Pi's
measured `data/calibration/servo_cal.json` are never overwritten). Pi setup, running and testing
are in `HARDWARE.md`.
