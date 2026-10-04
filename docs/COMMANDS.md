# Commands

Run from the project folder with the virtual environment on:

```bash
cd ~/Desktop/Projects/projects/hexa
source .venv/bin/activate
```

## Safe way to run things on the dev laptop

The laptop overheats and powers off at 87 °C (it idles near 59 °C). So:

```bash
python scripts/cool_run.py -- nice -n 19 <your command>      # waits until cool, kills it at 82 C
cat /sys/class/thermal/thermal_zone0/temp                    # temperature in thousandths of a degree
```

- One heavy thing at a time. Run test files one by one, never the whole suite in one go.
- The simulation tests (`test_walk_sim.py`, `test_controller_sim.py`, `test_body_process.py`, `test_voice_body.py`, `test_brain_cli.py`) are the hottest.
- Test the voice with a **headless** body (the default). The viewer plus Piper plus walking makes the body very slow.

## Ollama (runs in Docker; you start it and pull models)

```bash
docker start ollama
docker exec -it ollama ollama pull qwen2.5:0.5b
docker exec ollama ollama list
docker stop ollama                  # frees the CPU when you are not chatting
```

## Models and phrases

| Command | What it does |
|---|---|
| `scripts/fetch_models.sh` | Downloads Piper, the voice (`PIPER_VOICE`), the default Vosk model, then renders the fixed phrases. Names come from `config.py`. Safe to re-run. |
| `scripts/fetch_models.sh --all-models` | Also downloads the other Vosk models in `config.VOSK_MODELS` (the Indian English one). |
| `scripts/fetch_models.sh --help` | Shows what it fetches. |
| `python scripts/prerender_phrases.py --force` | Re-renders the spoken phrases. |

## Talk to the robot

| Command | What it does |
|---|---|
| `python main.py [--gui] [--listen always] [--chat fake]` | THE entrypoint: the whole robot, startup checks, rotating log, clean shutdown on Ctrl-C / SIGTERM. |
| `python scripts/voice_cli.py` | The whole loop, push-to-talk: press Enter, say "walk forward", press Enter (headless body). |
| `python scripts/voice_cli.py --listen always` | Listen all the time instead of push-to-talk (the default: Enter starts and stops listening, `stop` + Enter stops the robot). |
| `python scripts/voice_cli.py --chat fake` | Scripted chat: use this on the slow dev laptop. |
| `python scripts/voice_cli.py --no-speak --chat fake` | No audio: replies are printed as `[hexa] ...`. |
| `python scripts/voice_cli.py --model in` | Same with the Indian English model. |
| `python scripts/voice_cli.py --no-speak` | No Piper, so the robot says nothing. |
| `python scripts/voice_cli.py --gui` | With the viewer. Needs the Mesa override below. |
| `python scripts/brain_cli.py` | Type sentences instead of speaking. |
| `python scripts/bridge_cli.py --headless` | Type raw commands (`walk fwd 0.5`, `stop`). |
| `python scripts/control_window.py` | A window with keys and buttons. |

## Check the voice

| Command | What it does |
|---|---|
| `python scripts/mic_check.py` | Lists microphones, records 3 s, shows the level. Speak! |
| `python scripts/stt_check.py --model us --mic 8` | Says each phrase 3 times, shows what was heard, prints a summary. |
| `python scripts/stt_check.py --replay logs/stt_check-us.jsonl` | Re-judges a saved run with the current thresholds. |
| `python scripts/say.py` | Type text, hear it. `/clear` cancels, `/quit` exits. |
| `python scripts/say.py --phrase okay` | Plays a pre-rendered phrase. |
| `python scripts/say.py --list-phrases` | Lists the phrases. |

## Measure

| Command | What it does |
|---|---|
| `python scripts/measure_voice.py --clear` | How fast `clear()` stops speech (plays quiet sound). |
| `python scripts/measure_voice.py --stt` | One vs two Vosk recognizers: CPU, memory, body timing. |
| `python scripts/measure_chat.py` | Times the real Ollama model (heats the laptop!). Add `--with-voice` for the standing Vosk+Piper load. |
| `python scripts/measure_voice.py --body` | Body timing with and without Piper (add `--gui`). |

## Simulation tools (DEV)

| Command | What it does |
|---|---|
| `python scripts/sim_demo.py` | Stand/sit in the simulation. |
| `python scripts/walk_demo.py` | Walk, turn, strafe. |
| `python scripts/teleop.py` | Drive from the keyboard. |
| `python scripts/joint_jog.py` | Move each joint with sliders. |
| `python scripts/generate_urdf.py` (`--check`) | Rebuild or verify the robot model file. |

The viewer on this laptop needs: `MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/...  --gui`

## Tests and checks

```bash
ruff check .          # style
mypy .                # types
python scripts/cool_run.py -- nice -n 19 pytest tests/test_router.py -q     # one file
```

| Marker | Meaning | Run with |
|---|---|---|
| (none) | normal tests | `pytest tests/test_<name>.py` |
| `timing` | speed-sensitive, needs a quiet machine | `pytest -m timing` |
| `audio` | real Piper, no speaker | `pytest -m audio -s tests/test_tts_real.py` |
| `llm` | a running Ollama with the model pulled | `pytest -m llm -s tests/test_chat_llm.py` |

Tests that use the real Vosk model or Piper skip themselves if the models are missing: run `scripts/fetch_models.sh`.

Before commit: `ruff check .`, `mypy .`, and the test files for what you changed.

## Servo sizing

```bash
python scripts/torque_report.py --markdown docs/TORQUE_REPORT.md --json logs/torque_report.json   # full report, about 1-2 min
python scripts/torque_report.py --motions walk --seconds 3 --no-sensitivity                        # quick look
python scripts/torque_report.py --render logs/torque_report.json                                   # print a saved report, no sim
```
