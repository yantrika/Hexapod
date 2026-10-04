# Running hexa (start here)

One page: set up, get the models, see the robot, talk to it. Every command is run from the project folder.

```bash
cd ~/Desktop/Projects/projects/hexa
source .venv/bin/activate
```

> **Heat:** the dev laptop can reach 90 °C (it powers off at 87 °C and once did). The owner runs everything at full CPU with no heat guard, one heavy thing at a time. To log the peak temperature without any guard: `python scripts/cool_run.py --unguarded -- python main.py --chat fake`.
> It waits until the laptop is cool and kills the job at 82 °C. One heavy thing at a time.

## 1. First-time setup

| Step | Command |
|---|---|
| Install the packages | `pip install -r requirements.txt` |
| Download the models (Piper voice, Vosk, fixed phrases) | `scripts/fetch_models.sh` |
| Also the Indian English Vosk model (optional) | `scripts/fetch_models.sh --all-models` |

The script is safe to re-run: it skips what you already have. Models go in `assets/` and are not committed.

## 2. The models

| Model | Job | Where it lives | How to get or change it |
|---|---|---|---|
| **Vosk** (small US) | Speech to text (hearing you) | `assets/vosk/` | `scripts/fetch_models.sh`. Choose with `--model us` or `--model in`. |
| **Piper** (lessac low) | Text to speech (the robot's voice) | `assets/piper/` | `scripts/fetch_models.sh`. Change `PIPER_VOICE` in `config.py`. |
| **Ollama** (`qwen2.5:0.5b`) | Chat only (never moves the robot) | Docker container `ollama` | See section 6. |

To swap a model, see [HOW_TO.md](HOW_TO.md).

## 3. See the robot

There are three ways. Pick one.

### A. The simulator window (PyBullet GUI)

```bash
python main.py --gui --chat fake
```

A 3D window opens with the hexapod. On this laptop the viewer needs:

```bash
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python main.py --gui --chat fake
```

The window plus voice plus walking is heavy. Use `--no-speak` to lighten it.

### B. The phone page (a browser, no window needed)

```bash
python main.py --web --chat fake          # this computer only:  http://127.0.0.1:8765
python main.py --lan --chat fake          # also your phone: prints the URL(s) to open
```

1. The terminal prints a 6-digit **PIN** (also the address for `--lan`). Open the address and type the PIN.
2. Hold the arrows to walk. Let go and it stops. **STOP** always works.
3. Stand, sit and wave have buttons. Hold **Talk** to speak into the *robot's* microphone, or type in the box.
4. Your phone and the computer must be on the same Wi-Fi. The page is plain HTTP: trusted networks only.

Set your own PIN with `HEXA_WEB_PIN=123456 python main.py --web`.

### C. No window at all (headless)

```bash
python main.py                            # the default: simulation without a window
python scripts/control_window.py          # a small window with keys and buttons
python scripts/teleop.py                  # keyboard driving
python scripts/walk_demo.py               # watch it walk, turn, strafe
python scripts/sim_demo.py                # stand and sit
```

## 4. Talk to it

```bash
python main.py --chat fake                # push-to-talk: press Enter, speak, press Enter
python main.py --listen always            # listens all the time
python scripts/brain_cli.py               # type sentences instead of speaking
python scripts/voice_cli.py               # the interactive voice demo
```

Things to say: *walk forward*, *turn left*, *stop*, *sit down*, *stand up*, *wave*. Anything else is a chat question.
`stop` works even while it is walking or talking.

Useful switches for `main.py`:

| Switch | Meaning |
|---|---|
| `--gui` / `--headless` | with or without the simulator window |
| `--chat fake\|ollama\|off` | scripted chat, the real model, or no chat |
| `--no-speak` | no sound: replies are printed instead |
| `--no-mic` | no microphone (typing and the phone page still work) |
| `--listen ptt\|always` | push-to-talk or always listening |
| `--web`, `--lan`, `--web-port N` | phone page, on the network, other port |
| `--model us\|in` | Vosk model |
| `--device N` | microphone number (find it with `scripts/mic_check.py`) |
| `--log-level DEBUG` | more logging (log file is rotated automatically) |

Stop everything with **Ctrl-C**.

## 5. Check the voice parts

| Check | Command |
|---|---|
| Is the microphone working? | `python scripts/mic_check.py` |
| How well does Vosk hear me? | `python scripts/stt_check.py --model us` |
| Does the speaker work? | `python scripts/say.py "Hello, I am hexa"` |
| Try the phone page without a phone | `python scripts/web_check.py` |

## 6. The chat model (Ollama, optional)

The laptop is too slow for it (about 1.5 tokens/s), so use `--chat fake` here. The Raspberry Pi is where it is meant to run.

```bash
docker start ollama
docker exec -it ollama ollama pull qwen2.5:0.5b      # once
python main.py --chat ollama
docker stop ollama                                    # frees the CPU afterwards
```

## 7. Run the tests

```bash
python -m pytest tests/test_router.py -q     # one file at a time
```

Run the suite in batches of test files. More in [COMMANDS.md](COMMANDS.md).

## 8. On the Raspberry Pi

The Pi 5 runs Ubuntu 24.04 (Python 3.12) with no PyBullet, so it uses the **dry-run body**: it logs joint targets and moves nothing. Reach it with `ssh hexa-pi`.

| Step | Command |
|---|---|
| Copy the project (and the models over the LAN) | `scripts/sync_to_pi.sh --assets` (add `--dry-run` to preview) |
| First time only: virtualenv | `ssh hexa-pi`, then `cd ~/hexa && python3 -m venv .venv` |
| Install the packages (wheels only) | `export TMPDIR=~/hexa/.tmp; .venv/bin/pip install -r requirements-pi.txt` |
| Get the ARM Piper (only what is missing) | `scripts/fetch_models.sh` |
| Run it | `.venv/bin/python main.py --backend dryrun --no-mic --no-speak --chat fake` |
| Run it with the phone page | `HEXA_WEB_PIN=424242 .venv/bin/python main.py --backend dryrun --lan --no-mic --no-speak --chat fake`, then open the printed address (or `http://hexa.local:8765/`) |
| **Start in tmux** (page + PIN on the LAN, survives ssh closing) | `ssh hexa-pi`, then `cd ~/hexa && scripts/pi_dryrun.sh start 424242` (PIN optional; omit for a random one). It prints the PIN and the page address. |
| Watch what the gait would send to the servos | `cd ~/hexa && scripts/pi_dryrun.sh log` (same as `tail -f logs/dryrun.log`; Ctrl-C leaves it running) |
| Status, live console, stop cleanly | `scripts/pi_dryrun.sh status`, `scripts/pi_dryrun.sh attach` (detach: Ctrl-b d), `scripts/pi_dryrun.sh stop` |
| Run the tests | `.venv/bin/python scripts/cool_run.py --unguarded -- .venv/bin/python -m pytest -q` (logs the temperature only) |
| Measure | `scripts/measure_voice.py --body`, `--stt --latency`, `scripts/measure_e2e.py`, `scripts/measure_web.py`, and from another machine `scripts/measure_lan.py --url ws://hexa.local:8765 --pin 424242` |

Rules: nothing uses `sudo` from Claude, only `~/hexa` is touched, nothing starts at boot, and there is no servo code until stage 11e is approved. Set `HEXA_BACKEND=dryrun` so every script uses the dry-run body. The systemd unit `deploy/hexa.service` is written but not installed. Still to do on the Pi: the real microphone and speaker checks (`mic_check.py`, `say.py`) and the Ollama timing. See `plan.md`, stage 11a.

## If something goes wrong

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Quick answers:

| Problem | Try |
|---|---|
| "model not found" at start | `scripts/fetch_models.sh` |
| No sound or no microphone | `python scripts/mic_check.py`, then `--device N` |
| Phone cannot open the page | use `--lan`, same Wi-Fi, check the port and the PIN |
| Everything is slow or the laptop is hot | `--headless`, `--chat fake`, `--no-speak`, and watch the temperature with `cool_run.py --unguarded` |
