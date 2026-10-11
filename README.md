# hexa

A six-legged robot you can talk to. It hears you, walks, and answers back, and it works **fully offline**: no cloud services, no paid tools.

Say "walk forward", "sit down" or "stop", or just chat. Its persona is **Arya**: calm, funny or roast mode.

```
 you ── mic ──▶ Vosk (STT) ──▶ router ──┬─ command ──▶ body: gait + leg maths ──▶ simulation / servos
                                        └─ chat ─────▶ local LLM (Ollama) ──▶ Piper (TTS) ──▶ speaker
```

- **Safe by design.** Words are matched to commands by a plain router; the LLM only chats and never moves a leg. A stop always wins, and a watchdog halts the robot if the brain goes quiet.
- **Simulation first.** Everything is built and tested in PyBullet, then moves to a Raspberry Pi 5 by swapping one backend class.
- **Control it your way.** Voice, keyboard, a desktop window, or a phone browser page on your own Wi-Fi.

**Status:** works in simulation and in a hardware-free dry run on the Pi 5. Real servo control is not built yet.

## Quick start

Needs Python 3.11 and a microphone and speaker (or use `--no-mic --no-speak`).

```bash
git clone https://github.com/yantrika/Hexapod.git hexa && cd hexa
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-sim.txt
scripts/fetch_models.sh          # speech models and the fixed phrases (downloads once)
python main.py --chat fake       # press Enter, say "walk forward", press Enter
```

Add `--gui` for the PyBullet window, or `--lan` for the phone page (it prints a URL and a PIN).
`python main.py --help` lists every option, and startup prints one line per missing piece.

To stop, use the STOP button, Space, or type `stop`. A spoken "stop" works only while the mic is listening.

## Project layout

| Path | What it does |
|---|---|
| `main.py` | the one entry point |
| `brain/` | command router, chat, Arya persona and memory |
| `voice/` | microphone in (Vosk), speaker out (Piper) |
| `body/` | gait, kinematics, controller and the backends (sim, dry run) |
| `bridge.py` | the only link between brain and body |
| `config.py` | every number you might want to change |
| `web/` | phone control page |
| `scripts/` | demos, checks and tools |

## Docs

Start with [`docs/RUNNING.md`](docs/RUNNING.md). Then:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): how the parts fit, measured numbers, known limits
- [`docs/HOW_TO.md`](docs/HOW_TO.md): change a model, phrase, threshold or the robot size
- [`docs/HARDWARE.md`](docs/HARDWARE.md): wiring, servos, power
- [`docs/COMMANDS.md`](docs/COMMANDS.md) and [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
- [`docs/FILES.md`](docs/FILES.md): what every file does
- [`plan.md`](plan.md) and [`AGENTS.md`](AGENTS.md): the full plan and contributor rules

## Development

```bash
pytest               # default run skips the timing, audio and llm tests
ruff check . && mypy .
```

Read `AGENTS.md` before changing code. Heavy runs on a slow machine should go through `python scripts/cool_run.py -- <command>`.
