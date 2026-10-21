# Rules for AI agents

`hexa` is Arya, a fully offline talking hexapod (Vosk, Piper, optional local Ollama), built and
checked in PyBullet and run on a Raspberry Pi 5 by swapping one backend class. This file is the
short list of rules. Details: `README.md` (run it), `docs/ARCHITECTURE.md` (how it fits),
`docs/HARDWARE.md` (Pi, servos, wiring), `docs/DEVELOPMENT.md` (tests, layout, how to change
things), `docs/ROADMAP.md` (what is next). Work one approved ROADMAP item at a time and do not
code ahead of it.

## Commands

`pytest` (default run skips the `timing`, `audio`, `llm` markers), `pytest tests/sim` for motion
changes, `ruff check . && mypy .` before every commit. Python 3.11 venv in `.venv/`.

## Servos and the real robot (safety-critical)

- Servo code never runs unless `--backend servo` is given and the operator types `yes`.
  `HEXA_BACKEND=servo` is refused; servo is never a default; no systemd unit with a hardware
  backend is enabled at boot.
- One path to a servo: Controller -> `HexapodBackend.set_joint_targets` (hard limits) ->
  `ServoBackend` (calibrated pulse window, slew limit) -> `body/servo_drivers.py`. Nothing else
  imports the drivers (`tests/body/test_servo_paths.py`).
- Every per-joint servo number lives in `data/calibration/servo_cal.json`; never in `config.py`
  and never in other code. A joint that is not calibrated is not driven.
- The robot powers up sitting, attaches one leg at a time, releases after idling, and on SIGINT,
  SIGTERM or SIGHUP sits down and releases. Do not weaken any of this.
- Servos run from a separate 5-6 V supply with a kill switch; the Pi never powers them.
- On the Pi: never use `sudo` (give the owner the exact command), touch only `~/hexa`, enable
  nothing at boot. Downloads and installs (models, Ollama, packages) are run by the owner: give the
  command. Ollama runs in Docker on the laptop and stays stopped unless a test needs it.

## Architecture rules

- Two processes joined only by the `Bridge`. Voice and brain never touch joint angles. Nothing
  outside the backends may know which backend is loaded; import pybullet lazily.
- **`stop` always wins** (the `stop_event` path, never rate-limited, never through the motion
  keeper). Stale messages are dropped, the watchdog stops a robot that gets no heartbeat, the body
  validates every command, a fallen robot accepts only `stop`.
- **The LLM is never in the command path.** The router decides; only a `chat` result reaches
  `ChatBackend`, and only while the body is idle; a command, a stop or any motion cancels the reply.
- The robot speaks from the body's STATUS, never from assumption. Fixed phrases are pre-rendered.
- Audio in only through `voice/audio.py`, audio out only through `voice/playback.py`. Piper is one
  long-lived process. Speech is discarded while the robot speaks (self-hearing gate).
- Confidence alone never trusts a grammar match; see `brain/stt_decision.py`.
- Front ends (control window, teleop, CLIs, the phone page) command motion only through the Bridge
  or the `Controller` API, never the gait or a backend. The phone page keeps its safety on the
  server (PIN, one controller, deadman, `stop` on disconnect, no joint data).
- `config.py` is the single source of constants: never hard-code a value that belongs there; bound
  values with `config.clamp`. Leg order `RF, RM, RR, LR, LM, LF`; body frame +X forward, +Y left,
  +Z up; mount angles become yaw only through `config.mount_yaw_rad`.

## Persona and privacy

- Perception and the LLM may trigger only `stand`, `wave` and small in-place turns (about 10-20
  degrees), never walking, only when idle, STOP winning. Only the rule-based behaviour layer turns
  perception into commands.
- Persona modes change only how Arya talks. The router runs first and does not know the persona;
  persona requests never reach the body or the LLM.
- The hard limits (no remarks about looks, body, skin colour, race, religion, caste, gender,
  sexuality, disability, family or health; no slurs or threats; real-help questions get a straight
  answer) are enforced in every mode, in the prompt and in `brain/output_check.py`, and have tests.
- Privacy: face enrolment only after an explicit yes (recorded as `face_consent`; the database
  refuses embeddings without it); embeddings live only on the robot, in `data/memory/`, and never
  in logs, events' text or synced files; no image or face crop is ever saved (crops exist in
  memory until embedded); "forget me" deletes everything in one transaction, face included;
  `data/memory/` is private, ignored by git and never synced; tests never touch the real database
  (`scripts/face_check.py` uses its own file). The brain owns the database: the vision process
  never opens it and only gets embeddings by command. Vision still never moves the robot.

## Conventions

- Match the surrounding code (naming, comment density). Full-word identifiers; 4 spaces.
- Tests accompany behaviour changes and bug fixes. Inject clocks instead of sleeping; wait for
  events in real-process tests. Keep router negative cases.
- Do not commit generated or private files (logs, `.tmp/`, models, the URDF, `data/memory/`,
  wheelhouse). Add them to `.gitignore` first.
- Keep docs true: update `docs/` when a setting, a model, the wiring or a command changes. Add a
  new top-level folder to `README.md` or `docs/DEVELOPMENT.md`.
- Conventional Commits, imperative, under 72 characters. A PR states what, why and how it was
  tested; hardware work states the power setup.
