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
