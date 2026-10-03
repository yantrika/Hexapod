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

## Talking to the body process (Step 5)

The body runs in its own process; `scripts/bridge_cli.py` sends typed commands through the bridge and prints the status replies. Type `stand`, `sit`, `wave`, `stop`, `walk fwd 0.5`, `walk back`, `turn left 90`, `quit`.

```bash
python scripts/bridge_cli.py --headless          # PyBullet DIRECT, no window
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/bridge_cli.py --gui   # dev laptop
python scripts/bridge_cli.py --headless --no-heartbeat   # a walk stops by itself after 1 s (watchdog)
python -m body.process --headless                # idle body; Ctrl-C exits cleanly
```

The CLI sends a heartbeat at `HEARTBEAT_HZ`; without heartbeats a walk or turn is stopped by the watchdog and reported as `done` with `reason=watchdog`. Run the tests one file at a time (`nice -n 19 pytest tests/test_body_process.py`): they spawn real processes and take about a minute.
