# hexa

A talking hexapod robot: built in PyBullet simulation first, deployed to a
Raspberry Pi 5 later.

Fully offline. STT via Vosk, TTS via Piper, optional chat via a local Ollama
model. No cloud services and no paid tools.

- `AGENTS.md` — contributor conventions.
- `config.py` — every tunable value (geometry, joint limits, clamp ranges).
- `body/` — hexapod control, runs in its own process.
- `voice/` — speech in and out.
- `brain/` — command routing and the LLM fallback.
- `bridge.py` — the message contract between voice/brain and body.

## Running the simulator

```bash
source .venv/bin/activate
python scripts/generate_urdf.py            # only needed after changing geometry in config.py
python scripts/sim_demo.py --headless --pose stand   # no window, prints measured numbers
python scripts/sim_demo.py --pose stand              # PyBullet GUI
python scripts/walk_demo.py --headless               # walk, turn in place, strafe; prints distances
python scripts/walk_demo.py                          # same in the GUI
python scripts/sim_demo.py --headless --script "stand,walk,stop,sit"   # scripted controller run
python scripts/teleop.py                             # keyboard control (GUI); prints the key map
python scripts/joint_jog.py                          # dev only: 18 joint sliders (GUI)
python scripts/joint_jog.py --legs RF                # only one leg's 3 sliders: labels stay readable
```

**Laptop-specific note (Intel HD Graphics "ILK", OpenGL 2.1):** PyBullet's GUI needs
OpenGL 3.3 shaders and aborts here with `GLSL 1.50 is not supported`. This Mesa
override makes it start:

```bash
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/sim_demo.py --pose stand
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/walk_demo.py
```

The scripts do not set this themselves; tests never open the GUI.

**Keep it light on slow machines:** run one process at a time, and prefix long runs with
`nice -n 19 timeout 300`. numpy's BLAS threads are pinned to one thread (they made the
loop 7x slower and crashed the dev laptop).

**Manual control.** `teleop.py`: W/S forward/back, A/D strafe, Q/E turn, Space stop,
1 stand, 2 sit, 3 wave, +/- speed. Releasing a movement key ramps it to zero. It only uses
the controller API. `joint_jog.py` is a tuning tool, not part of the runtime; it only uses
the backend API, so the hard joint limits still apply. Its slider panel is open from the start (in other PyBullet windows it
is hidden until you press `G`), each shown leg's angles are drawn in yellow above the robot,
and `--legs RF` keeps the panel short.

## Control window (Step 5b)

`scripts/control_window.py` is a small Tkinter window that drives the body process through the bridge: keys, Stand/Sit/Wave buttons, a big STOP, a command box and a status log. The PyBullet window is only the viewer (no panels, no text); click the control window to drive.

```bash
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/control_window.py   # dev laptop, with the viewer
python scripts/control_window.py              # other machines, with the viewer
python scripts/control_window.py --headless   # no viewer
```

Keys (only when the command box is not focused): W/S forward/back, A/D strafe, Q/E turn, Space stop, 1 stand, 2 sit, 3 wave, +/- speed. Hold to move, release to ramp to zero; W+A or W+Q combine. Enter or Tab focuses the command box, Esc returns to the keys. Losing focus or closing the window sends stop. The command box takes the same lines as `bridge_cli` (`walk fwd 0.5`, `strafe left`, `turn right 45`, ...). It needs `sudo apt install python3.11-tk`.

## Talking to the body process (Step 5)

The body runs in its own process; `scripts/bridge_cli.py` sends typed commands through the bridge and prints the status replies. Type `stand`, `sit`, `wave`, `stop`, `walk fwd 0.5`, `walk back`, `turn left 90`, `quit`.

```bash
python scripts/bridge_cli.py --headless          # PyBullet DIRECT, no window
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/bridge_cli.py --gui   # dev laptop
python scripts/bridge_cli.py --headless --no-heartbeat   # a walk stops by itself after 1 s (watchdog)
python -m body.process --headless                # idle body; Ctrl-C exits cleanly
```

The CLI sends a heartbeat at `HEARTBEAT_HZ`; without heartbeats a walk or turn is stopped by the watchdog and reported as `done` with `reason=watchdog`. Tests with a wall-clock bound (tick cost, stop and walk latency, distance walked in real time) are marked `timing` and skipped by default: run timing tests on a quiet machine with `pytest -m timing`. Run the tests one file at a time (`nice -n 19 pytest tests/test_body_process.py`): they spawn real processes and take about a minute.

## Typed text to the robot (Step 6)

`scripts/brain_cli.py` routes typed text with the offline router and drives the body process through the bridge. A walk keeps going (a motion keeper sends heartbeats) until you say stop, or for `VOICE_WALK_MAX_S` (10 s). Anything that is not a command prints `[chat] <text>` (the chat model comes in Step 9).

```bash
python scripts/brain_cli.py --headless      # no window
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/brain_cli.py --gui   # dev laptop
```

Try `walk forward`, `please sit down`, `can you wave`, `turn left`, `hexa stop`, `halt`, `I sat down for lunch` (chat). The line printed for each input shows the matched phrase and score.

## Speech (Step 7)

Piper (offline TTS) runs as ONE long-lived subprocess; `voice/playback.py` speaks sentence by sentence through a queue that `clear()` can cancel at any moment (it is the only module that touches the speaker).

```bash
scripts/fetch_models.sh                  # Piper binary + voice, then pre-renders the fixed phrases (skips what exists)
python scripts/say.py                    # type a line, hear it; /clear cancels, /quit exits; prints time to first sound
python scripts/say.py "Hello, I am hexa" # one line and exit
python scripts/say.py --phrase okay      # a pre-rendered phrase: no synthesis wait (--list-phrases)
nice -n 19 python scripts/measure_voice.py --clear   # clear() latency on the speaker (quiet)
nice -n 19 python scripts/measure_voice.py --body    # body tick time with and without Piper (add --gui)
```

Tests: `pytest tests/test_playback.py tests/test_tts.py` (no sound device, no Piper: fake engine, sink and clock). `pytest -m audio -s tests/test_tts_real.py` synthesizes with the real Piper and prints time to first audio and the real-time factor.

Dev laptop numbers (Core i3 M380, no AVX; they vary with background load): model load about 1.0-2.6 s once at startup, then real-time factor 0.4-1.0 and 0.6-2 s to first audio for a short sentence. While Piper synthesizes it takes about two cores and roughly doubles the body's tick time (see the Risks table in `plan.md`); pre-rendered phrases cost nothing at speaking time. `PIPER_NICE` and `PIPER_CPU_LIST` in `config.py` exist but did not help in measurement. Re-measure on the Pi 5 in Step 11.

**Dev rule (this laptop):** test voice with a headless body. GUI viewer plus Piper plus walking gives about 90 ms body ticks (11 ticks/s).

## Voice in (Step 8)

Microphone, Vosk (offline), the router and the body, with hexa ignoring its own voice. Models come from `scripts/fetch_models.sh` (small US English `us` and small Indian English `in`).

```bash
python scripts/mic_check.py                  # lists inputs, records 3 s, prints peak and RMS: speak!
python scripts/stt_check.py --model us       # say each phrase, see what Vosk heard and what the router did
python scripts/stt_check.py --model in       # the same with the Indian English model, to compare
python scripts/voice_cli.py                  # the whole loop, headless body: say "walk forward", "stop"
python scripts/voice_cli.py --model in       # other model;  --no-speak: no Piper;  --gui: viewer
nice -n 19 python scripts/measure_voice.py --stt     # Vosk cost, body ticks with Vosk, voice latency
```

Say "walk forward", "sit down", "stand up", "turn left", "wave", "stop". After a command hexa says "okay" (a placeholder; speech from body statuses is Step 9) and the microphone is ignored while it speaks and for `SPEAK_TAIL_S` after. Tests (no microphone or speaker): `pytest tests/test_status_hub.py tests/test_audio.py tests/test_stt_gate.py` (fake recognizer), `pytest tests/test_voice_loop.py` (real Vosk on Piper-rendered speech, skipped without models), `nice -n 19 pytest tests/test_voice_body.py` (end to end with a headless body; run alone).

Dev laptop numbers (they move with background load): Vosk uses about 15-25 % of a core, real-time factor 0.07-0.11 decoding speech; it adds about 1-2 ms to the body's mean tick. Spoken "walk forward" is sent about 0.85 s after you stop talking (Vosk waits for silence) and the first foot target moves about 0.25 s later. Piper busy at the same time is still the expensive part (see Speech above).
