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
```

**Laptop-specific note (Intel HD Graphics "ILK", OpenGL 2.1):** PyBullet's GUI needs
OpenGL 3.3 shaders and aborts here with `GLSL 1.50 is not supported`. This Mesa
override makes it start:

```bash
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/sim_demo.py --pose stand
MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/walk_demo.py
```

The scripts do not set this themselves; tests never open the GUI.
