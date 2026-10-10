# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read these before changing anything; they hold all rules, commands and conventions:

- `AGENTS.md` — architecture rules, conventions, commands, testing and commit guidelines.
- `plan.md` — architecture, message schema, `config.py` constants and the step-by-step build plan. Work one approved step at a time. If `AGENTS.md` and `plan.md` disagree, `plan.md` wins and `AGENTS.md` is the one to fix.
- `docs/ARCHITECTURE.md` (data flow, measured numbers, known limits) and `docs/FILES.md` (every code file; `tests/test_docs.py` fails if a file is missing from it, so update it when you add, rename or remove a file).

## What this is

`hexa` is a fully offline talking hexapod (Vosk STT, Piper TTS, optional local Ollama chat), built and verified in PyBullet first and then moved to a Raspberry Pi 5 by swapping one backend class. It is currently at the dry-run stage on the Pi (Step 11a-11c); no servo code until stage 11e is approved.

## Commands

Python 3.11 venv in `.venv/`.

```bash
pip install -r requirements-dev.txt -r requirements-sim.txt   # requirements-pi.txt on the Pi (wheels only)
scripts/fetch_models.sh                  # Piper, Vosk (US + Indian English), pre-rendered phrases (owner runs downloads)

pytest                                   # default run excludes the timing, audio and llm markers
pytest tests/test_router.py::test_name   # single test
pytest -m timing | pytest -m audio -s | pytest -m llm -s
ruff check . && mypy .                   # run both before committing

python main.py --chat fake               # the app: headless body, push-to-talk. Also --gui, --web/--lan, --backend dryrun
python scripts/sim_demo.py --headless --script "stand,walk,stop,sit"
python scripts/generate_urdf.py          # after ANY geometry change in config.py (a test fails on a stale URDF)
python scripts/cool_run.py --unguarded -- <command>   # how to run anything heavy here (see below)
scripts/sync_to_pi.sh [--assets] [--dry-run]          # push to the Pi (never --delete)
scripts/pi_dryrun.sh start|log|status|stop            # run on the Pi in tmux
```

The PyBullet GUI on the dev laptop needs `MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330` (see `README.md`).

## Standing owner rules (easy to miss)

- **No heat guard**, on the laptop or the Pi. Run at full CPU through `cool_run.py --unguarded -- <cmd>` so the peak temperature is still logged, and report it. One heavy process at a time: no `pytest -n`, run sim test files in small batches, use the GUI only on request. The laptop is a 2010 dual-core i3 and BLAS threads are pinned to one thread on purpose.
- **Downloads and installs are run by the owner**: give them the exact command. Ollama runs in Docker and stays stopped unless a test needs it.
- **Pi**: never `sudo`; touch only `~/hexa`; nothing enabled at boot. `ssh hexa-pi`.
- Keep the existing sim as is: no servo-realism work (MG995, motor sizing) unless asked.

## Architecture in one page

Two OS processes joined only by the `Bridge` (`bridge.py`: `command_queue` brain→body, `status_queue` body→brain, plus `stop_event`). Anything that needs more than one file to see:

- **Brain process** (`main.py` → `brain/app.py::HexaApp`): threads for STT, router/chat worker, TTS playback, status hub and heartbeat, talking only through `queue.Queue` and one `speaking` Event. Audio in goes ONLY through `voice/audio.py` (`AudioSource`), audio out ONLY through `voice/playback.py`.
- **Router decides, the LLM only chats**: `brain/router.py` (fuzzy phrases, aliases, stop words) picks commands; only a `chat` result reaches `brain/chat.py` (`ChatBackend`: `OllamaChat`, `FakeChat`), and only while the body is idle. STT final results are chosen by `brain/stt_decision.py` between a free-text and a grammar recognizer; confidence alone is never the guard.
- **Hexa speaks from the body's STATUS, never from assumption** (`brain/dialogue.py`, pre-rendered phrases in `config.TTS_PHRASES`). `brain/status_hub.py` fans each status out to every subscriber (dialogue, motion keeper, web page, control window).
- **Body process** (`body/process.py`): a 50 Hz wall-clock tick: drain queue → drop stale → `stop` always wins, otherwise latest wins → watchdog → `controller.py` (state machine, all clamps) → `gait.py` (pure planner) → `kinematics.py` (IK, knee-up) → `HexapodBackend.set_joint_targets()`. Backends: `sim_backend.py` (PyBullet, 240 Hz), `dryrun_backend.py` (Pi, no hardware), `servo_backend.py` (later). Nothing outside the backends may know which one is loaded; pybullet is imported lazily.
- **Front ends** (`scripts/control_window.py`, `teleop.py`, `bridge_cli.py`, `brain_cli.py` and the phone page in `web/`) command motion only through the Bridge or the `Controller` API, never the gait or a backend. `web/session.py` holds the phone safety logic server-side (PIN, one controller, deadman, `stop` on disconnect).
- **`config.py` is the single source of truth** (geometry, two joint-limit tiers, timing, thresholds, audio, paths). Never hard-code values that belong there; leg mount angles convert to yaw only via `config.mount_yaw_rad`. Leg order is `RF, RM, RR, LR, LM, LF`; body frame is +X forward, +Y left, +Z up.
