# Repository Guidelines

`hexa` is a talking hexapod robot: built in PyBullet simulation first, then deployed to a Raspberry Pi 5. It is fully offline (Vosk STT, Piper TTS, optional local Ollama chat). `plan.md` is the authoritative architecture and build plan; if this file and `plan.md` disagree, fix this file. Work one build step at a time and do not code ahead of the approved step.

## Project Structure & Module Organization
Flat top-level packages, no `src/`. See `plan.md` section 6 for the full tree.
- `config.py` — single source of truth for geometry, joint limits, clamps, timing, router, audio, paths, models.
- `bridge.py` — `Command` / `Status` dataclasses, queue creation, validation.
- `body/` — body process: `kinematics`, `poses`, `urdf`, `gait`, `controller`, `arbitration`, `clock`, `process`, and the backend seam (`backend.py`, `sim_backend.py`, `servo_backend.py`).
- `brain/` — `router.py`, `chat.py`, `dialogue.py`.
- `voice/` — `audio.py`, `stt.py`, `tts.py`, `playback.py`.
- `tests/` — `test_<module>.py` per module, shared fakes in `tests/fakes.py`.
- `scripts/` — model download, URDF generation, sim demo. `assets/` — URDF and models (downloaded models are gitignored).

Avoid committing build output, caches, models, or secrets; add them to `.gitignore` first.

## Architecture Rules
- Two processes joined only by the `Bridge` handle from `bridge.py`: `command_queue` (brain to body, small, drop-oldest), `status_queue` (body to brain) and a shared `stop_event`. Message fields: `action`, `params`, `seq`, `timestamp` for commands; `status`, `ref_seq`, `detail`, `seq`, `timestamp` for status.
- The voice/brain process uses threads (STT, router/chat worker, TTS playback, status listener, heartbeat) that talk only through `queue.Queue` and one shared `speaking` `threading.Event`. The TTS queue must stay clearable (needed for barge-in).
- Voice and brain never touch joint angles. Nothing outside `sim_backend.py` / `servo_backend.py` may depend on which backend is loaded.
- Each body control tick drains the command queue: stale messages (older than `MAX_MESSAGE_AGE_S`) are dropped, `stop` always preempts everything (even if stale) and holds the current pose, other motion commands are latest-wins. `bridge.send` sets `stop_event` for every `stop`, and the body checks it each tick before draining. `sit`/`stand`/`wave` answer `busy` while a transition runs. A watchdog stops the body if no command or heartbeat arrives within `WATCHDOG_TIMEOUT_S` while walking or turning.
- The brain speaks based on status messages from the body, never on assumption.
- Physics (240 Hz) and control (50 Hz) are driven by wall-clock time, not loop iteration counts. The body can run headless (PyBullet DIRECT) via a flag.
- While TTS is playing (plus `SPEAK_TAIL_S`), STT results are discarded.

## Conventions
- Never hard-code values that belong in `config.py`; bound values with `config.clamp`. Two joint-limit tiers: hard (`JOINT_HARD_LIMITS_DEG`, enforced once in the `HexapodBackend` clamp layer) and soft gait limits (`GAIT_SOFT_LIMITS_DEG`, used by the gait planner).
- Sim and kinematics use a clean joint frame (0 = neutral). Servo centre/sign/offset live only in the calibration table in `servo_backend.py`.
- Units: metres and radians, unless a name ends in `_DEG`. Body frame: +X forward, +Y left, +Z up.
- Leg mount angles are measured clockwise from above, 0° = +X: RF 30, RM 90, RR 150, LR 210, LM 270, LF 330. Convert to math yaw only through `config.mount_yaw_rad` (the single conversion point). Left mirrors right, and RF's foot target must land on the right side (y < 0).
- The zero pose is the stand pose (`BODY_HEIGHT_STAND = TIBIA_LENGTH`). The URDF is generated from config by `scripts/generate_urdf.py` (re-run it after changing geometry; a test fails if the committed file is stale). Joint axes: coxa +Z, femur and tibia -Y.
- Leg order is `RF, RM, RR, LR, LM, LF`; tripod A = RF, RR, LM; tripod B = RM, LR, LF.
- IK always returns the knee-up solution and returns `None` for unreachable points.
- Router: `stop` words match anywhere; other commands need a short utterance (after filler removal) and `fuzz.ratio >= ROUTER_THRESHOLD`; everything else goes to chat.

## Build, Test, and Development Commands
Python 3.11 venv in `.venv/` (gitignored).
- Install: `pip install -r requirements-dev.txt` (plus `requirements-sim.txt` for PyBullet, `requirements-pi.txt` on the Pi).
- Test: `pytest`; single test: `pytest tests/test_<module>.py::test_name`.
- Lint and types: `ruff check .` and `mypy .`; run both before committing.
- Models: `scripts/fetch_models.sh` (arrives in Steps 7–8).

## Coding Style & Naming Conventions
- 4 spaces for Python, 2 spaces for markup and config files.
- `snake_case` for all files, directories, and Python symbols; `PascalCase` for classes; `UPPER_SNAKE_CASE` for constants.
- Prefer descriptive, full-word identifiers over abbreviations.
- Use `ruff` for linting and `mypy` for type checks; configuration lives in `pyproject.toml` (added in Step 0).

## Testing Guidelines
- `pytest`, files named `test_<module>.py`. New behaviour and bug fixes need tests.
- Inject clocks (`body/clock.py`) instead of sleeping; test arbitration, watchdog, and the self-hearing gate with a fake clock.
- Kinematics: test `FK(IK(point)) ≈ point` on 100 seeded random reachable points, the knee-up branch, and `None` for unreachable points. Do not test `IK(FK(angles)) == angles`.
- Router tests must include negative cases ("I sat down for lunch", "I'll walk you through it", "turn up the music").
- Keep the suite green before opening a pull request.

## Commit & Pull Request Guidelines
Use [Conventional Commits](https://www.conventionalcommits.org): `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`. Use the imperative mood and keep the subject under 72 characters.

Pull requests should include a short summary, motivation, testing performed, and any linked issues. Add screenshots or a short clip for simulation changes. Hardware work (Step 11) must note the power setup: servos run from a separate 5–6 V high-current supply, never from the Pi.
