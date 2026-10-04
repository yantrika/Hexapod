# How to change things

Each recipe says **which file to edit**, **what to run after**, and how to check it worked.
On the dev laptop wrap heavy commands: `python scripts/cool_run.py -- <command>` (see `COMMANDS.md`).

## Change the speech recognition model (Vosk)

**One place: `config.py`.** The robot uses the small US model `vosk-model-small-en-us-0.15`.

1. Find the real file name at https://alphacephei.com/vosk/models (do not guess it).
2. `config.py`: change the directory name in `VOSK_MODELS` (for example change the `"us"` line), or add a new line and set `VOSK_MODEL_DEFAULT` to its short name.
3. Run `scripts/fetch_models.sh`. It reads the names from `config.py` and downloads the default model (it skips what you already have). `--all-models` also downloads every other entry (the Indian model).
4. Try it: `python scripts/stt_check.py --model us` and `python scripts/voice_cli.py`. (`--model` accepts a short name, a folder name or a path.)

Notes:
- The command grammar needs a model that ships `graph/HCLr.fst` and `graph/Gr.fst`. Both small models do. If a model lacks them, set `STT_USE_GRAMMAR = False`.
- Bigger models are slower. The small Indian English model was noticeably slower than the US one on the dev laptop, so it is off by default.
- Nothing else in the code names a model.

## Change the speaking voice (Piper)

**One place: `config.py`.**

1. Pick a voice at https://huggingface.co/rhasspy/piper-voices (check the real file list; each voice has a `.onnx` and a `.onnx.json`).
2. `config.py`: change `PIPER_VOICE` (for example `"en/en_US/lessac/low/en_US-lessac-low"`). `PIPER_MODEL_PATH` follows from it.
3. Delete the old pre-rendered phrases and rebuild them with the new voice: `rm assets/phrases/*.wav`, then `scripts/fetch_models.sh`.
4. Hear it: `python scripts/say.py "Hello, I am hexa"`.

Faster voices are "low" quality. On the dev laptop Piper uses about two CPU cores while it speaks and slows the body (see `plan.md`, Risks).

## Change what the robot says automatically

`TTS_PHRASES` in `config.py` is a list of `name: text`. Edit or add one, then run `python scripts/prerender_phrases.py --force` (or `scripts/fetch_models.sh` for new ones only). Play one with `python scripts/say.py --phrase NAME`.
The phrase spoken after each voice command is `VOICE_ACK_PHRASE`.

## Change the voice commands (words the robot understands)

All in `config.py`, section "Router":

| Setting | What it is |
|---|---|
| `ROUTER_PHRASES` | The command phrases and what each does. Add a line such as `"go ahead": _WALK_FWD`. |
| `ROUTER_ALIASES` | Exact-only near-forms like "waves". Never fuzzy-matched. |
| `STOP_WORDS` | Words that stop the robot anywhere in a sentence. |
| `ROUTER_FILLERS` | Words ignored in front of a command ("please", "hey"). |
| `ROUTER_THRESHOLD`, `ROUTER_MAX_WORDS` | How fuzzy and how long a command may be. |

The command grammar for Vosk is built from these automatically (`voice/stt.py`, `command_grammar()`); you do not edit it separately.

After a change:
1. `pytest tests/test_router.py tests/test_stt_decision.py` (add a test for your phrase and a negative test for a sentence that must NOT trigger it).
2. `python scripts/stt_check.py --model us` to see how your voice does on it.

## Tune the "which recognizer do I trust" numbers

In `config.py`: `STT_STOP_CONF`, `STT_GRAMMAR_CONF`, `STT_GRAMMAR_EXTRA_WORDS`, `STT_STOP_EXTRA_WORDS`.

1. Run `python scripts/stt_check.py --model us` once (it saves `logs/stt_check-us.jsonl`).
2. Change a number in `config.py`.
3. `python scripts/stt_check.py --replay logs/stt_check-us.jsonl` re-judges the same recording with the new number. No need to speak again.
4. Aim for 0 "DANGEROUS false positives" and 0 "MISSED STOPS" first, then raise accuracy.

## Change the microphone or speaker

`MIC_DEVICE` and `SPEAKER_DEVICE` in `config.py` (`None` = default). List inputs with `python scripts/mic_check.py`. One-off: `--mic N` on `stt_check.py`, `--device N` on `voice_cli.py`.

## Change the robot size or shape

1. `config.py`, section "Body geometry": `BODY_RADIUS`, `COXA_LENGTH`, `FEMUR_LENGTH`, `TIBIA_LENGTH`, `LEG_MOUNT_ANGLES_DEG`.
2. Rebuild the model file: `python scripts/generate_urdf.py` (a test fails if it is out of date: `python scripts/generate_urdf.py --check`).
3. Check: `pytest tests/test_kinematics.py tests/test_urdf.py tests/test_config.py`.
4. Then re-tune walking (below). Stand height follows `TIBIA_LENGTH`.

## Change how it walks, sits, waves

All in `config.py`: speeds and strides (`STEP_LENGTH_MAX_M`, `STEP_HEIGHT_M`, `TURN_RATE_MAX_DEG_S`, `GAIT_PERIOD_S`), poses (`BODY_HEIGHT_SIT`), wave (`WAVE_*`), limits (`GAIT_SOFT_LIMITS_DEG`). Look at it with `python scripts/walk_demo.py` or drive it with `python scripts/control_window.py`. Walking tests: `pytest tests/test_gait.py` then `tests/test_walk_sim.py` (this one is hot: run it alone).

## Change safety timing

`config.py`, section "Timing and bridge": `WATCHDOG_TIMEOUT_S` (robot stops if no heartbeat), `HEARTBEAT_HZ`, `MAX_MESSAGE_AGE_S`, `FALL_TILT_DEG`. Keep `WATCHDOG_TIMEOUT_S` larger than the time between heartbeats.

## Change the servo pins or calibration

See `HARDWARE.md`. Short version: only the table in `body/servo_backend.py`; nothing exists yet because the hardware step is not built.

## Change the chat model (Ollama)

`OLLAMA_MODEL`, `OLLAMA_URL`, `OLLAMA_TIMEOUT_S`, `CHAT_MAX_TOKENS` in `config.py`. The chat code (`brain/chat.py`) is not built yet (Step 9), so changing these has no effect today.

## Add a new file or module

1. Create it in the right folder (`body/`, `brain/`, `voice/`, `scripts/`, `tests/`).
2. Add one line for it to `docs/FILES.md` (a test checks this).
3. Add a `tests/test_<name>.py`.
4. Run `ruff check .` and `mypy .`.

## Move to the Raspberry Pi

Step 11 in `plan.md`: install `requirements-pi.txt`, run `scripts/fetch_models.sh` (it picks the ARM Piper), write the servo backend (`HARDWARE.md`), then re-measure Piper and Vosk speed with `scripts/measure_voice.py`.
