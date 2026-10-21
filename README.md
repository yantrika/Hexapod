# hexa: Arya, a talking hexapod

Arya is a six-legged robot that walks, listens, talks and chats, fully offline: Vosk hears you,
Piper speaks, an optional local Ollama model chats. Everything is built and tested in a PyBullet
simulation first; the same code drives the real robot on a Raspberry Pi 5 by swapping one backend
class. The repo and the Python package are called `hexa`; the robot's name and persona is Arya.

## Quick start (about 10 minutes, Linux)

Python 3.11. Developed and tested on Linux; other systems are not tested.

```bash
git clone git@github.com:yantrika/Hexapod.git hexa && cd hexa
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-sim.txt
scripts/fetch_models.sh            # Piper voice, Vosk model, spoken phrases (asks nothing, skips what exists)
python main.py --gui --chat fake   # the robot in a 3D window, with typed or spoken commands
```

On an old Intel GPU the 3D window needs an OpenGL override (symptom: a garbled or empty window,
or "GLSL" errors):

```bash
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python main.py --gui --chat fake
```

You now see the hexapod in a PyBullet window. Press Enter in the terminal to start listening,
say "walk forward", press Enter again, say "stop". No microphone? Add `--web`, open the printed
address, enter the PIN and type the commands in the page's box (or use `--no-mic --no-speak`
and the page's buttons). `python scripts/control_window.py` opens its own robot window with a
small key/button panel (Space = stop).

## Ways to run it

| What | Command |
|---|---|
| Robot in the 3D window, scripted chat | `python main.py --gui --chat fake` |
| Same, no sound and no microphone | `python main.py --gui --chat fake --no-speak --no-mic` |
| Phone page (open the printed address, type the printed PIN) | `python main.py --web --chat fake` (this computer) or `--lan` (your phone on the same Wi-Fi) |
| No window (default, lightest) | `python main.py --chat fake` |
| Talk and listen through a phone browser (HTTPS, needs `python scripts/make_cert.py` once) | `python main.py --lan --https --no-mic --phone-voice --chat fake` |
| Always listening instead of push-to-talk | `python main.py --listen always` |
| Real chat model (needs Ollama, see below) | `python main.py --chat ollama` |
| Scripted motion demo in the window | `python scripts/sim_demo.py --script "stand,walk:5,turn:90,strafe:3,stop,sit"` |
| Type commands (no voice) | `python scripts/cli.py` (router) or `python scripts/cli.py --raw` (bridge commands) |
| Drive from the keyboard | `python scripts/teleop.py` (3D window) |
| Pi without servos (moves nothing, logs the joint targets) | `python main.py --backend dryrun` |
| Real servos on the Pi | `python main.py --backend servo` (asks you to type `yes`; read `docs/HARDWARE.md` first) |

Things to say: *walk forward*, *turn left*, *stop*, *sit down*, *stand up*, *wave*, *roast mode*,
*my name is X*, *forget me*. Anything else is chat. *Stop* works while it walks or talks. In
push-to-talk mode a voice "stop" works only while you hold the button; the control window's STOP
button and Space always work.

Useful `main.py` switches: `--gui|--headless`, `--chat fake|ollama|off`, `--no-speak`, `--no-mic`,
`--listen ptt|always`, `--web|--lan [--web-port N]`, `--https`, `--phone-voice`, `--mode calm|funny|roast`,
`--no-memory`, `--model us|in` (Vosk model), `--device N` (microphone, list with
`scripts/mic_check.py`), `--log-level DEBUG`. Stop with Ctrl-C.

**Phone as the microphone (Step 12c).** A phone browser opens its microphone only on a secure page,
so the Pi serves the page over HTTPS with a self-signed certificate:

```bash
python scripts/make_cert.py                        # once, on the Pi (re-run with --force if its address changes)
python main.py --lan --https --no-mic --backend dryrun
```

Open the `https://` address it prints, accept the one-time "not secure" warning, enter the PIN,
then hold the talk button and speak. The audio goes to Vosk on the Pi; nothing leaves your Wi-Fi.
The router, stop word and 10 s listening limit are the same as for the robot's own microphone.
Without a phone, stream a recording: `python scripts/web_check.py --pin <PIN> --wav sit_down.wav`
(see `--help`).

**Arya's voice on the phone (Step 12c-2).** Add `--phone-voice` and tap "Hear Arya on this phone"
once: her answers play on the phone instead of a speaker on the robot.

`python main.py --help` lists every option, and startup prints one line per missing piece.

Vision (standalone for now, no robot needed): `pip install -r requirements-vision.txt`,
`python scripts/fetch_vision_models.py`, then `python scripts/vision_check.py --debug-preview`
(webcam, boxes, track ids, events and a timing summary every 5 s; `--no-preview` for no window).
Faces: `python scripts/face_check.py enroll NAME` (asks yes/no first), then `watch` (own database).
On the Pi: `python scripts/vision_bench.py --load 4` (headless numbers with the cores busy). Over SSH
add `--no-preview` to `face_check.py` (there is no display). Measured on the Pi 5: 4 fps, 13 ms detect.

Optional chat model: Ollama runs in Docker on a laptop (`docker start ollama`,
`docker exec -it ollama ollama pull qwen2.5:0.5b`, stop it when done) and from a folder on the Pi
(`docs/HARDWARE.md`). The laptop is too slow for it (about 1.5 tokens/s), so use `--chat fake` there.

## Where things are

| Folder | What |
|---|---|
| `main.py`, `config.py`, `bridge.py` | The entry point; every constant; the two-process message schema |
| `body/` | The body process: gait, kinematics, controller, backends (sim, dry-run, servo) |
| `brain/` | Router, voice loop, chat, dialogue, persona, memory, status hub |
| `voice/` | Audio in, Vosk, Piper, playback, push-to-talk |
| `web/` | The phone control page |
| `vision/` | The vision process: webcam, face detection, tracker, events only (not wired into `main.py` yet) |
| `scripts/` | Tools you run: models, sim demos, servo calibration, Pi sync, checks |
| `tests/` | The test suite (`docs/DEVELOPMENT.md`) |
| `data/` | Persona files, the servo calibration file, the private memory database |
| `deploy/` | The systemd unit (written, not installed) |
| `.github/` | The CI workflow: ruff, mypy and the tests that need no PyBullet, models or hardware |
| `assets/`, `logs/` | Downloaded models and phrases, the generated robot file, logs: created on demand, not committed |
| `docs/` | `ARCHITECTURE.md`, `HARDWARE.md`, `DEVELOPMENT.md`, `ROADMAP.md` |

## Troubleshooting

| Problem | Try |
|---|---|
| "Vosk model not found" / "Piper binary not found" | `scripts/fetch_models.sh` |
| The window is garbled, empty or will not open | The OpenGL override in the quick start; needs a display |
| No sound, no microphone, or the wrong one | `python scripts/mic_check.py`, then `--device N` |
| The robot hears itself or obeys its own voice | Use a headset or move the microphone away from the speaker |
| The phone cannot open the page | `--lan`, same Wi-Fi, check the PIN and the port; plain HTTP, trusted networks only |
| The body is slow or jerky while it speaks | `--headless`, `--no-speak`, `--chat fake` |
| Chat says "I can't think right now" | Ollama is not running or the model is not pulled |
| A leftover `piper` or body process | `pkill -f "main.py"`; `pgrep -af piper` |
| `ruff`/`mypy` complain | Fix them; CI runs both |
| A machine overheats during the full test suite | Run the tests in smaller batches (a folder at a time) |

More: `docs/HARDWARE.md` has the Pi-specific problems. `logs/hexa.log` has the last run.

## Docs

- `docs/ARCHITECTURE.md` how the processes fit together, the safety rules, measured numbers
- `docs/HARDWARE.md` wiring, Pi setup, servo calibration, the kill switch
- `docs/DEVELOPMENT.md` tests, how to test a feature in the window, lint, adding features, syncing to the Pi
- `docs/ROADMAP.md` what is next
- `AGENTS.md` short rules for AI coding agents
