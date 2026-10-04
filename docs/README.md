# hexa documentation

hexa is a six-legged robot you can talk to. It listens (Vosk), understands (a simple word router), moves (a walking gait), and answers (Piper). It works offline. It is built in a simulation first, then moved to a Raspberry Pi 5 with real servos.

## Which page do I need?

| I want to... | Read |
|---|---|
| Know what every file does and which to edit | [FILES.md](FILES.md) |
| Change a model, a phrase, a threshold, the robot size | [HOW_TO.md](HOW_TO.md) |
| Change servo pins, channels or calibration | [HARDWARE.md](HARDWARE.md) |
| Find which setting in `config.py` does what | [CONFIG.md](CONFIG.md) |
| Understand how the parts connect | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Run something, or run tests safely | [COMMANDS.md](COMMANDS.md) |
| Fix a problem | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Read the plan in very simple words | [PLAN_SIMPLE.md](PLAN_SIMPLE.md) |
| Read the full plan and rules | [../plan.md](../plan.md), [../AGENTS.md](../AGENTS.md) |

## Where the project is

| Part | State |
|---|---|
| Leg maths, walking, controller, simulation, stop and watchdog | Done and tested |
| Router (words to commands), typed commands | Done |
| Speaking (Piper), cancel-able playback | Done |
| Listening (Vosk), no self-hearing, command grammar | Done; thresholds waiting for your voice results |
| Chat with a local AI (Ollama), speaking from body status, `main.py` | **Not built** (Step 9) |
| Interrupt while talking, push-to-talk | **Not built** (Step 10) |
| Real servos on the Pi (pins, calibration) | **Not built** (Step 11) |
| Phone web page | **Not built** (Step 12) |

## Three things to remember

1. All numbers are in `config.py`.
2. Servo-specific numbers will live only in `body/servo_backend.py`.
3. The dev laptop overheats: use `scripts/cool_run.py` for anything heavy (see [COMMANDS.md](COMMANDS.md)).

## Keeping these docs true

- Add, rename or remove a file: update [FILES.md](FILES.md). A test (`tests/test_docs.py`) fails if a code file is missing from it.
- Change a setting's meaning: update [CONFIG.md](CONFIG.md).
- Change a model or the wiring: update [HOW_TO.md](HOW_TO.md) / [HARDWARE.md](HARDWARE.md).
