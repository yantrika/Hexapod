# Every file in the project

Plain words. For each file: what it does, and **when you would edit it**.
A test (`tests/test_docs.py`) fails if a file is missing from this page, so keep it up to date when you add, rename or remove a file.

Legend: **STUB** = placeholder, not built yet. **DEV** = developer tool, not part of the robot.

## Top folder

| File | What it does | Edit it when |
|---|---|---|
| `config.py` | Every number and setting in one place (sizes, limits, speeds, timing, router words, audio, model names, thresholds). | You want to change any value. See `CONFIG.md`. |
| `bridge.py` | The two message types (`Command`, `Status`) and the `Bridge`: the only link between the brain program and the body program. Checks every command. | You add a new command or status kind. |
| `commandline.py` | Turns typed text like `walk fwd 0.5` into commands, and prints statuses. Shared by the typed front ends. | You add a typed command. |
| `main.py` | The entrypoint: starts the body and the voice/brain loop, handles Ctrl-C and SIGTERM, startup checks, rotating log. | When you want one start command. |
| `README.md` | How to run each step, with commands. | You add something runnable. |
| `AGENTS.md` | The rules for anyone (or any AI) changing the code. | A rule changes. |
| `CLAUDE.md` | Points Claude Code to `AGENTS.md` and `plan.md`. | Almost never. |
| `plan.md` | The full plan: architecture, message schema, safety, every build step, risks, measurements. | A decision changes. |
| `pyproject.toml` | Settings for `ruff` (lint), `mypy` (types) and `pytest` (including the `timing` and `audio` markers). | You change lint or test settings. |
| `requirements.txt` | Python packages the robot needs (numpy, vosk, sounddevice, rapidfuzz, requests). | You add a package. |
| `requirements-dev.txt` | `requirements.txt` plus pytest, ruff, mypy. | A dev tool changes. |
| `requirements-sim.txt` | `requirements.txt` plus PyBullet (the simulation). | The simulator changes. |
| `requirements-pi.txt` | `requirements.txt` plus the servo driver for the Raspberry Pi (not chosen yet). | Step 11, when you pick the servo board. |
| `.gitignore` | Files git must not store: `.venv`, caches, models, generated audio, logs. | You add a new generated file type. |

## `body/` : the program that moves the legs

| File | What it does | Edit it when |
|---|---|---|
| `body/process.py` | Runs the body as its own process: reads commands, runs the controller 50 times a second, sends statuses. Also `BodyProcess` (starts and stops it) and `BodyProbe` (timing numbers for tests). | You change how the body loop works. |
| `body/controller.py` | The state machine: stand, sit, wave, walk, turn, stop, fall handling, watchdog. Clamps speeds and angles. | You add a posture or change behaviour. |
| `body/arbitration.py` | Pure rule for one tick's commands: stop wins, newest motion wins, old messages are dropped. | You change command priority. |
| `body/gait.py` | The tripod walking pattern: from "how far along the step" and "how fast" to where each foot goes. | You tune walking. |
| `body/kinematics.py` | Leg maths: foot position to joint angles (and back). Picks the knee-up solution. | Leg geometry type changes. |
| `body/poses.py` | The fixed stand and sit poses, worked out from kinematics. | You change a pose. |
| `body/backend.py` | The **backend contract** (`HexapodBackend`): set 18 joint angles, advance time, read the pose. Holds the one hard-limit clamp. | You add a backend method. |
| `body/sim_backend.py` | The simulated robot in PyBullet (window or headless). | You change the simulation. |
| `body/servo_backend.py` | **STUB.** The real servo driver for the Pi. This is where the servo **pins/channels and calibration** will live (see `HARDWARE.md`). | Step 11 (hardware), and whenever you re-wire or recalibrate. |
| `body/urdf.py` | Builds the robot model file from `config.py` sizes. | The model description changes. |
| `body/clock.py` | A swappable clock (real or fake) and the fixed-rate loop timer. Lets tests run without waiting. | Rarely. |
| `body/__init__.py` | Marks the folder as a package; pins maths threads to 1. | Never. |

## `brain/` : understanding words, sending commands

| File | What it does | Edit it when |
|---|---|---|
| `brain/router.py` | Turns a sentence into stop / command / chat. Stop words work anywhere; a single word must match exactly; two or more words are fuzzy-matched. | You change how sentences are understood. (The words themselves are in `config.py`.) |
| `brain/stt_decision.py` | Decides which recognizer to trust: the free-text one or the command-grammar one, with safety guards. | You tune or change that decision. |
| `brain/voice_loop.py` | The voice loop: gate, recognizer, router, bridge, spoken "okay". Two threads. | You change what happens after speech is heard. |
| `brain/brain_loop.py` | Sends a routed command to the body and keeps a held walk alive. Shared by the typed and voice front ends. | You change how commands are sent. |
| `brain/motion_keeper.py` | Sends heartbeats while a walk or turn is held; stops after `VOICE_WALK_MAX_S`. | You change heartbeat rules. |
| `brain/event_hub.py` | Shares what hexa heard, decided and said (and the listening light) with the phone page, without ever blocking the voice loop (the oldest event is dropped). | You add an event type or listener. |
| `brain/status_hub.py` | The one reader of the body's statuses; shares each status with any number of listeners without blocking. | You add a listener type. |
| `brain/transcript_log.py` | Writes one line per recognised utterance to `logs/transcripts.jsonl`. | You change what is logged. |
| `brain/chat.py` | Chat: the `ChatBackend` interface, `OllamaChat` (local LLM over HTTP), `FakeChat` (scripted, for tests and the slow laptop) and `ChatResponder` (streams a reply, speaks it sentence by sentence, can be cancelled, keeps history). | You change the chat model or behaviour. |
| `brain/sentences.py` | Turns streamed LLM text into clean, speakable sentences (no markdown or emoji; splits overlong ones). Pure. | You change how replies are spoken. |
| `brain/dialogue.py` | After a command, speaks from the body's STATUS ("okay", "I'm already sitting"...) using pre-recorded phrases. | You change what the robot says about statuses. |
| `brain/__init__.py` | Marks the folder as a package. | Never. |

## `web/` : the phone control page (Step 12a buttons, 12b hold-to-talk and typing)

| File | What it does | Edit it when |
|---|---|---|
| `web/protocol.py` | Strict checks of the page's messages (walk, stop, stand, sit, wave, ptt_press, ptt_release, say; numbers clamped to -1..1; unknown fields refused) and the bridge `walk` they become. Pure. | You add a button or a message. |
| `web/session.py` | The PIN check with lockout, "one controller at a time", the server-side deadman, stop-on-disconnect, and hold-to-talk safety (10 s limit, release on stop or disconnect). Pure, clock injected. | You change the safety rules. |
| `web/server.py` | The server thread: the page and the WebSocket on one port, handshake checks, status line. Talks only to the Bridge. | You change the connection handling. |
| `web/static/index.html` | The whole phone page (one file, no CDN, no build step). | You change how the page looks or behaves. |
| `web/__init__.py` | Marks the folder as a package. | Never. |

## `voice/` : ears and mouth

| File | What it does | Edit it when |
|---|---|---|
| `voice/audio.py` | **The only file that touches the microphone.** `MicSource` (mic), `FileSource` (WAV file), `QueueSource` (tests), device list. | You change input handling. |
| `voice/stt.py` | Vosk speech recognition: one model, two recognizers (free text and command grammar). Also the self-hearing gate. | You change the recognizer. |
| `voice/ptt.py` | Push-to-talk: the state machine (`ptt_step`) and the thread-safe `PushToTalk` (`press()`, `release()`). Decides when the microphone is listened to. | You change the tail, add a new button (phone page, GPIO). |
| `brain/app.py` | `HexaApp`: builds and owns everything (body, hub, voice loop, dialogue, chat, speech) and the shutdown order. | You add a part to the robot. |
| `brain/startup.py` | The startup checks: one clear line per missing thing. | You add a requirement. |
| `brain/logsetup.py` | Rotating log file plus console. | You change the log format. |
| `voice/tts.py` | Piper text-to-speech as one long-lived process. Splits text into sentences. Loads pre-rendered phrases. | You change the voice engine. |
| `voice/playback.py` | **The only file that touches the speaker.** A queue you can cancel (`clear()`), prefetching the next sentence, the `speaking` flag. | You change playback. |
| `voice/__init__.py` | Marks the folder as a package. | Never. |

## `scripts/` : tools you run by hand

| File | What it does | Edit it when |
|---|---|---|
| `scripts/voice_cli.py` | Runs the whole loop: talk to the robot (microphone to body). | You change the demo. |
| `scripts/measure_chat.py` | Times the real Ollama model: first token, tokens per second, memory, temperature, body tick time (`--with-voice` adds Vosk and Piper). **DEV** (heats the laptop) | You add a measurement. |
| `scripts/torque_report.py` | Servo sizing from the sim: peak and RMS torque and speed per joint type for stand, sit, wave, walk, strafe, turn; sensitivity to mass and height; writes `docs/TORQUE_REPORT.md`. | You change masses, geometry or gait, or want to size servos. |
| `scripts/web_check.py` | A headless client of the phone page (no browser): sends the same messages as the page. | You test the web server by hand. |
| `scripts/measure_web.py` | Press-to-motion time, idle CPU of the web server and peak temperature. | You want the numbers again (e.g. on the Pi). |
| `scripts/measure_ptt.py` | Vosk CPU and body tick time with push-to-talk idle, listening and always-on. | You want the numbers again (e.g. on the Pi). |
| `scripts/stt_check.py` | Measures how well Vosk hears **your** voice; saves results; `--replay` re-judges them. | You add test phrases. |
| `scripts/mic_check.py` | Lists microphones, records 3 seconds, shows the level. | Rarely. |
| `scripts/say.py` | Type text, hear it spoken. | Rarely. |
| `scripts/brain_cli.py` | Type a sentence, the router decides, the body acts (no voice). | Rarely. |
| `scripts/bridge_cli.py` | Type raw commands to the body and see its answers. Reads the Bridge directly: do not run beside other front ends. | Rarely. |
| `scripts/control_window.py` | Small window with keys and buttons to drive the robot. | You change the window. |
| `scripts/control_logic.py` | The window's key and button logic, with no screen code (so it can be tested). | You change key mapping. |
| `scripts/teleop.py` | Drive the simulated robot from the keyboard. **DEV** | Rarely. |
| `scripts/joint_jog.py` | Move the 18 joints with sliders. **DEV** (tuning). | Rarely. |
| `scripts/sim_demo.py` | Hold stand/sit or run a short scripted sequence in the simulation. **DEV** | Rarely. |
| `scripts/walk_demo.py` | Walk forward, turn, strafe in the simulation. **DEV** | Rarely. |
| `scripts/generate_urdf.py` | Rewrites `assets/urdf/hexapod.urdf` from `config.py` (`--check` only verifies). | After changing robot sizes. |
| `scripts/fetch_models.sh` | Downloads Piper, its voice and the Vosk models; then renders the fixed phrases. Safe to re-run. | You change a model. See `HOW_TO.md`. |
| `scripts/prerender_phrases.py` | Renders `config.TTS_PHRASES` to WAV files so they play instantly. | Rarely (it reads config). |
| `scripts/measure_voice.py` | Measures Piper and Vosk speed, body timing with voice running, `clear()` delay. **DEV** | You add a measurement. |
| `scripts/cool_run.py` | Runs a command but waits for the laptop to cool and kills it before it overheats. **DEV** | Rarely. |
| `scripts/__init__.py` | Lets the tests import the scripts. | Never. |

## `assets/` : data (models are NOT stored in git)

| Folder | What is in it | Created by |
|---|---|---|
| `assets/urdf/hexapod.urdf` | The robot model (stored in git; a test fails if it is out of date). | `generate_urdf.py` |
| `assets/piper/` | The Piper program and the voice file. Not in git. | `fetch_models.sh` |
| `assets/vosk/` | The Vosk models. Not in git. | `fetch_models.sh` |
| `assets/phrases/` | Pre-rendered spoken phrases (`okay.wav`, ...). Not in git. | `prerender_phrases.py` |
| `logs/` | `transcripts.jsonl` (what was heard) and `stt_check-*.jsonl`. Not in git. | The voice loop and `stt_check.py` |

## `docs/` : this folder

| File | What it is |
|---|---|
| `docs/README.md` | Start here: the list of docs and a 2-minute overview. |
| `docs/FILES.md` | This page. |
| `docs/HOW_TO.md` | Recipes: change a model, a phrase, a threshold, the robot size, the pins. |
| `docs/HARDWARE.md` | Servos, pins, calibration, power. Honest about what is not built yet. |
| `docs/CONFIG.md` | A map of `config.py`: which setting does what. |
| `docs/TORQUE_REPORT.md` | The generated torque and speed report (tables in N*m and kg*cm), with its limits and the placeholders to replace. |
| `docs/ARCHITECTURE.md` | How the pieces fit together, with pictures. |
| `docs/COMMANDS.md` | Every command you can run, and the safe way to run it. |
| `docs/TROUBLESHOOTING.md` | Problems we hit and what fixed them. |
| `docs/PLAN_SIMPLE.md` | The plan in very simple words. |

## `tests/` : the checks (`pytest`)

Run one file at a time on the dev laptop (see `COMMANDS.md`).

| File | What it checks |
|---|---|
| `tests/test_config.py` | Settings are symmetric and complete. |
| `tests/test_kinematics.py` | Leg maths: forward and back, knee-up, unreachable points. |
| `tests/test_backend.py` | The backend contract and the hard-limit clamp. |
| `tests/test_sim_backend.py` | The PyBullet backend (headless). |
| `tests/test_urdf.py` | The robot model file matches `config.py`. |
| `tests/test_gait.py` | The walking planner (pure). |
| `tests/test_walk_sim.py` | The gait really walks the simulated robot. |
| `tests/test_clock.py` | The swappable clock and the fixed-rate loop. |
| `tests/test_controller.py` | The controller state machine (fake backend). |
| `tests/test_controller_sim.py` | The controller driving the simulated robot. |
| `tests/test_arbitration.py` | Command priority rules. |
| `tests/test_bridge.py` | Messages, validation and queues. |
| `tests/test_body_runner.py` | The body core in one process. |
| `tests/test_body_process.py` | Real body processes: stop speed, flood, shutdown. |
| `tests/test_bridge_cli.py` | The typed-command parser and one end-to-end run. |
| `tests/test_motion_keeper.py` | Heartbeat rules with a fake clock. |
| `tests/test_router.py` | Sentence routing, including the dangerous near-misses. |
| `tests/test_brain_cli.py` | Typed text through to a real headless body. |
| `tests/test_control_logic.py` | The control window's key logic. |
| `tests/test_teleop.py` | Keyboard mapping for `teleop.py`. |
| `tests/test_joint_jog.py` | Labels for `joint_jog.py`. |
| `tests/test_sentences.py` | The sentence splitter: abbreviations, decimals, markdown, any chunking. |
| `tests/test_chat.py` | Chat backends (stub HTTP server, `FakeChat`) and the reply streamer: first sentence early, cancel, history, errors. |
| `tests/test_dialogue.py` | Status to phrase table, throttle, "already sitting". |
| `tests/test_chat_voice.py` | Chat in the voice loop: only while the body is idle; motion and stop cancel it. |
| `tests/test_ptt.py` | Push-to-talk state machine with a fake clock, thread safety, mode switch. |
| `tests/test_barge_in.py` | Recognizers get no audio when ptt is off; barge-in; always mode unchanged; clean shutdown. |
| `tests/test_filler.py` | The instant filler: one for a slow reply, none for a fast one, cancelled by barge-in. |
| `tests/test_app.py` | `HexaApp` with fakes: start, sit, chat, clean shutdown, no orphans. |
| `tests/test_torque_report.py` | The torque report: unit conversion, statistics, parsing, rendering (no sim) and one short sim run. |
| `tests/test_web_protocol.py` | The page's message checks: valid, clamped, unknown action, malformed. |
| `tests/test_web_session.py` | PIN, lockout, one controller, deadman, stop on disconnect, `stop` on the `stop_event` path (fake clock). |
| `tests/test_web_server.py` | The server on a real loopback socket: handshake refusals, deadman, statuses, no controller/gait/backend import. |
| `tests/test_web_voice.py` | Typed text takes the spoken path (stop, chat while walking), the app's hold-to-talk wiring, and one real run: press, WAV through a FileSource, release, the robot sits. |
| `tests/test_web_app.py` | The page against a real headless body: walk moves it, a dropped connection stops it, `main.py --web` exits clean. |
| `tests/test_main_smoke.py` | `main.py` as a real process: sit + fake chat reply from an audio file, exit 0; SIGTERM/SIGINT. |
| `tests/test_startup.py` | The startup checks and `main` exit codes. |
| `tests/test_logsetup.py` | Log rotation. |
| `tests/test_chat_llm.py` | One real-LLM check (only with `pytest -m llm`). |
| `tests/test_tts.py` | Piper wrapper against a fake Piper (crash, hang, orphans). |
| `tests/test_playback.py` | Playback queue, `clear()`, speaking flag, prefetch gaps. |
| `tests/test_tts_real.py` | Real Piper speed (only with `pytest -m audio`). |
| `tests/test_audio.py` | Audio sources, mic queue overflow. |
| `tests/test_event_hub.py` | Events in order, a slow listener loses the oldest, publishing never blocks. |
| `tests/test_status_hub.py` | Many listeners, a stuck one blocks nobody. |
| `tests/test_stt_gate.py` | Self-hearing protection and voice-loop logic (fake recognizer). |
| `tests/test_stt.py` | One model, two recognizers (fake Vosk). |
| `tests/test_stt_decision.py` | Which recognizer to trust, with real forced results. |
| `tests/test_transcript_log.py` | The transcript log never raises. |
| `tests/test_voice_loop.py` | Real Vosk on Piper speech (skipped without models). |
| `tests/test_voice_body.py` | Speech to a real headless body, end to end. |
| `tests/test_cool_run.py` | The temperature guard with a fake thermometer. |
| `tests/test_docs.py` | Every code file is listed on this page. |
| `tests/fakes.py` | Shared fakes: clock, backend, engine, sink, recognizer. |
| `tests/voice_fixtures.py` | Shared fixtures that render speech with Piper for Vosk tests. |
| `tests/conftest.py` | Loads those fixtures for pytest. |
| `tests/walk_harness.py` | Helper that drives the gait in the simulation. |
| `tests/__init__.py` | Marks the folder as a package; pins maths threads to 1. |
