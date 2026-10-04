# Problems we hit and what fixed them

## The laptop turned itself off

**Cause:** heat. `journalctl -b -1` shows `acpitz: critical temperature reached` and `HARDWARE PROTECTION shutdown`. Limit 87 °C, idle about 59 °C.
**Fix:** run heavy things through `python scripts/cool_run.py -- <command>`, one at a time, test files singly, short runs. Check the temperature with `cat /sys/class/thermal/thermal_zone0/temp` (divide by 1000). Clean the fan and vents if you can.

## `Vosk model not found` or `Piper binary not found`

Run `scripts/fetch_models.sh`. It is safe to re-run and prints `skip:` for what you already have. The downloads are slow (about 100 MB in all). If a download was cut off, delete the half file in `assets/vosk/` and run it again.

## Tests are "skipped"

The tests that use real Vosk or Piper skip themselves when the models are missing. Run `scripts/fetch_models.sh`, then run the test again.

## The robot does not hear me / hears the wrong words

1. `python scripts/mic_check.py`: the peak should be roughly 3000 to 20000 when you speak. Much lower: move closer or raise the input volume. Near 32767: lower it (clipping).
2. `python scripts/stt_check.py --model us`: see exactly what Vosk heard and how the command grammar helped.
3. Single short words ("sit", "wave", "halt") are the hardest. Use two-word forms ("sit down", "wave hello") or the typed front ends.
4. Try the other model: `--model in`. It was slower on this laptop but better on some words.
5. Quiet room, speak normally, and wait for the end of the sentence (about one second of silence) before expecting a result.

## Nothing happens when I talk

In push-to-talk mode (the default) hexa only listens between two presses of Enter (the terminal shows LISTENING). Press Enter, talk, press Enter. For the old always-listening behaviour use `--listen always` or set `LISTEN_MODE = "always"`. A voice "stop" works only while listening: type `stop` + Enter, or use the control window STOP button / Space.

## The robot obeys its own voice

It should not: audio is dropped while it speaks and a little after (`SPEAK_TAIL_S`). If it happens, raise `SPEAK_TAIL_S` in `config.py` a little, lower the speaker volume, or move the microphone away from the speaker. Test with `python scripts/voice_cli.py` and watch that the "okay" does not appear as `heard:`.

## "Stop" is not heard

Voice stop depends on the microphone; it can be missed. Keep another way to stop within reach (the control window's Space bar, or the typed `stop`). On the real robot, the power switch is the emergency stop. The watchdog also stops a walk if the brain stops sending heartbeats (`WATCHDOG_TIMEOUT_S`).

## The robot walks and then stops by itself after 10 seconds

That is `VOICE_WALK_MAX_S` in `config.py`: a spoken walk is only kept alive that long. Say "walk" again or raise the value.

## The body is slow or jerky while the robot speaks

Piper uses about two CPU cores while it speaks and the old laptop cannot spare them (body timing about doubles; with the viewer open it is much worse). Test the voice with a headless body (the default). Pre-rendered phrases cost nothing at speaking time. The Raspberry Pi 5 should do better; re-measure at Step 11 with `scripts/measure_voice.py`.

## The simulation window is garbled or will not open

On this laptop use: `MESA_GL_VERSION_OVERRIDE=3.3 MESA_GLSL_VERSION_OVERRIDE=330 python scripts/<tool>.py --gui`. The window is a viewer only (no text drawn in it). For anything about timing, use headless.

## Two programs fight over the body's statuses

Only the status hub should read the body's status queue. `scripts/bridge_cli.py` reads the Bridge directly (it is a stand-alone debugging tool): do not run it at the same time as `voice_cli.py`, `brain_cli.py` or `control_window.py`.

## A `piper` or body process is left running

Normal exits clean up. If something was killed: `ps -eo pid,args | grep -E "piper|spawn_main"`, then `kill <pid>`. (Piper also exits by itself when its parent dies.)

## The model file `hexapod.urdf` test fails

You changed a size in `config.py`. Run `python scripts/generate_urdf.py` and commit the new file.

## `ruff` or `mypy` complain

Fix them before committing: `ruff check . --fix`, then `mypy .`. Lines must be 100 characters or less.

## Chat says "I can't think right now"

Ollama is not running, the model is not pulled, or it was too slow. Check: `docker ps` (is `ollama` listed? else `docker start ollama`), `docker exec ollama ollama list` (is the model there? else pull it yourself), `curl http://127.0.0.1:11434/api/version`. The first reply after the model was unloaded can take longer than `OLLAMA_TIMEOUT_S` on this laptop; `voice_cli.py` warms the model up at start, and `OLLAMA_KEEP_ALIVE` keeps it loaded for 10 minutes.

## Chat is very slow, or the laptop gets very hot when chatting

Expected on the dev laptop (no AVX): `qwen2.5:0.5b` makes about 1.5 tokens/s and the laptop reaches about 90 C. Use `--chat fake` here, and time the real model on the Pi 5. `docker stop ollama` frees the CPU. Never use a model name ending in `-cloud`: it runs online.

## "Tell me after I stop"

By design: chat does not run while the robot is walking, turning or changing posture, so the microphone stays free for "stop". Say "stop" (or wait for the move to end) and ask again.

## Docker CPU limit

If Ollama seems slow, `docker inspect -f 'NanoCpus={{.HostConfig.NanoCpus}}' ollama` shows 0 for no limit. `docker update --cpus 0` is ignored by Docker; use `docker update --cpus 4 ollama` (the machine's core count) to remove a cap.
