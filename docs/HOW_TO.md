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
After a voice command the robot speaks from the body's status through `brain/dialogue.py` (which phrase for which status is a small table there). To add a phrase: add it to `TTS_PHRASES`, run `python scripts/prerender_phrases.py`, then use its name in `dialogue.py`.

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

See `HARDWARE.md` (pin locations and the numbers to fill in). Short version: only the table in `body/servo_backend.py`; nothing exists yet because the hardware step is not built.

## Change the chat model (Ollama)

Ollama runs in Docker on the laptop and by hand on the Pi (see "Ollama on the Pi" below). **You** pull models (I never download):

1. `docker start ollama` (if it is not running), then `docker exec -it ollama ollama pull <name>`, for example `qwen2.5:1.5b`. Only use local models (not names ending in `-cloud`): the robot must stay offline.
2. `config.py`: change `OLLAMA_MODEL` to that name (or try it once with `python scripts/voice_cli.py --ollama-model <name>`).
3. Time it before trusting it: `python scripts/measure_chat.py --model <name>` (it prints a verdict: you need at least 3 tokens/s and a first token within 5 s). On the dev laptop `qwen2.5:0.5b` gave about 1.5 tokens/s, so use `--chat fake` here and time the real model on the Pi.

Other chat settings in `config.py`: `CHAT_SYSTEM_PROMPT` (the personality: short, friendly, no lists/emoji, only the abilities it has), `CHAT_MAX_TOKENS` (reply cap), `CHAT_TEMPERATURE`, `CHAT_HISTORY_TURNS` (memory), `OLLAMA_KEEP_ALIVE`, `OLLAMA_TIMEOUT_S`, `CHAT_MAX_SENTENCE_CHARS`. Nothing else in the code names a chat model: the code only knows the `ChatBackend` interface in `brain/chat.py`, so a different engine means one new class there.

## Add a new file or module

1. Create it in the right folder (`body/`, `brain/`, `voice/`, `scripts/`, `tests/`).
2. Add one line for it to `docs/FILES.md` (a test checks this).
3. Add a `tests/test_<name>.py`.
4. Run `ruff check .` and `mypy .`.

## Work on the Raspberry Pi (start here for the Pi)

The Pi 5 runs Ubuntu 24.04 Server. All commands below are typed **on the Pi** (`ssh hexa-pi`, then `cd ~/hexa`) unless it says "laptop". Commands starting with `sudo` are yours to run; I never use `sudo`. The body is the **dry-run body**: it moves nothing and logs what the gait would send.

### Put the latest code on the Pi (laptop)

```bash
cd ~/Desktop/Projects/projects/hexa
scripts/sync_to_pi.sh --dry-run     # preview
scripts/sync_to_pi.sh               # code only (no --delete, models and .venv are kept)
scripts/sync_to_pi.sh --assets      # also the models, over the LAN (only needed once)
```

### Start, watch and stop (inside tmux, survives your ssh closing)

```bash
cd ~/hexa
scripts/pi_dryrun.sh start 424242   # page + PIN on the LAN; prints the PIN and the address
scripts/pi_dryrun.sh log            # what the gait would send to the servos (Ctrl-C leaves hexa running)
scripts/pi_dryrun.sh status         # running? page address
scripts/pi_dryrun.sh attach         # the live console (detach: Ctrl-b then d)
scripts/pi_dryrun.sh stop           # clean stop
```

Open the printed address (for example `http://hexa.local:8765/`) on your phone or laptop and type the PIN. With the real chat model: `HEXA_CHAT=ollama scripts/pi_dryrun.sh start 424242` (add `HEXA_OLLAMA_MODEL=qwen2.5:1.5b` for the bigger one). If Ollama is not running it stops at once and says so.

### Ollama on the Pi (do this with a fast connection; about 1.6 GB + 0.4 GB + 1 GB)

Run it by hand, not as a boot service (nothing starts at boot). No `sudo` is needed (`zstd`, which unpacks the download, is already on the Pi).

1. **Download and unpack** (1.56 GB, resumable if the Wi-Fi drops: run the same `curl` again):
   ```bash
   mkdir -p ~/ollama && cd ~/ollama
   curl -fL -C - -o ollama-linux-arm64.tar.zst https://ollama.com/download/ollama-linux-arm64.tar.zst
   tar --zstd -xf ollama-linux-arm64.tar.zst      # creates bin/ollama and lib/ollama
   rm ollama-linux-arm64.tar.zst
   ~/ollama/bin/ollama --version
   ```
   This is the plain `arm64` build (not the `jetpack` ones, which are for NVIDIA Jetson boards). Do NOT use the one-line `install.sh`: it installs a service that starts at boot.
2. **Start the server in its own tmux session** (the models are stored in `~/.ollama`):
   ```bash
   tmux new-session -d -s ollama '~/ollama/bin/ollama serve'
   sleep 3; curl -s http://127.0.0.1:11434/api/version
   ```
3. **Pull the two models** (about 0.4 GB and 1 GB; local models only, never names ending in `-cloud`):
   ```bash
   ~/ollama/bin/ollama pull qwen2.5:0.5b
   ~/ollama/bin/ollama pull qwen2.5:1.5b
   ~/ollama/bin/ollama list
   ```
4. **Time them** (the bar is 3 tokens/s and a first token within 5 s; it prints a verdict and the RAM of the `ollama` processes; it logs the temperature):
   ```bash
   cd ~/hexa
   export TMPDIR=$HOME/hexa/.tmp HEXA_BACKEND=dryrun
   .venv/bin/python scripts/cool_run.py --unguarded -- .venv/bin/python scripts/measure_chat.py --model qwen2.5:0.5b --with-voice
   .venv/bin/python scripts/cool_run.py --unguarded -- .venv/bin/python scripts/measure_chat.py --model qwen2.5:1.5b --with-voice
   ```
   Tell me the numbers (tokens/s, first token, RAM, peak temperature) and I put them in `plan.md`.
5. **Stop Ollama** when you are done (frees about 1 GB of RAM): `tmux kill-session -t ollama`.

### Run the full test suite on the Pi

```bash
cd ~/hexa
export TMPDIR=$HOME/hexa/.tmp HEXA_BACKEND=dryrun
.venv/bin/python scripts/cool_run.py --unguarded -- .venv/bin/python -m pytest -q --junitxml=$HOME/hexa/.tmp/pi_tests.xml
.venv/bin/python -m pytest -m llm -s tests/test_chat_llm.py        # needs Ollama running (step 2 above)
.venv/bin/python -m pytest -m timing -q                            # speed-sensitive tests, quiet Pi
```

`cool_run.py --unguarded` only logs the temperature and prints the peak (and `vcgencmd get_throttled`); it never stops the run. Expected without Ollama: **962 passed, 10 skipped, 0 failed** (the 10 are tests that need PyBullet, which the Pi does not have). The `llm` tests skip with "no Ollama" until the server is up. `-m audio` tests need a real speaker and microphone setup and are not part of this.

### Change the Wi-Fi the Pi uses

The Pi uses **netplan** with `systemd-networkd` and `wpa_supplicant` (no NetworkManager, so there is no `nmcli`). The Wi-Fi settings are in `/etc/netplan/50-cloud-init.yaml`, a root-only file, so I could not read it: step 1 shows it to you. Everything below is `sudo` and yours to run. **Keep the old network in the file as a fallback**: the Pi joins whichever listed network it can see, so a wrong new password cannot strand it.

1. **Look at what is there** (and take a backup):
   ```bash
   sudo cat /etc/netplan/50-cloud-init.yaml
   sudo cp /etc/netplan/50-cloud-init.yaml ~/netplan-backup.yaml
   networkctl status wlan0 | head -12
   ```
2. **Edit** `sudo nano /etc/netplan/50-cloud-init.yaml`. Under `wifis:` / `wlan0:` / `access-points:` add the new network next to the old one. Use spaces only (no tabs) and keep the indentation exactly like this:
   ```yaml
   network:
     version: 2
     wifis:
       wlan0:
         dhcp4: true
         optional: true
         regulatory-domain: "IN"          # your country code; sets the allowed channels
         access-points:
           "OldNetworkName":
             password: "old-password"
           "NewNetworkName":
             password: "new-password"
   ```
   A phone hotspot is the same. An open network is `"Name": {}`. A hidden network: add `hidden: true` under the name. Keep names and passwords in quotes. If your file already has other keys (an `ethernets` block, `renderer`), leave them as they are and only add the access point.
3. **Check the syntax, then apply it safely**:
   ```bash
   sudo chmod 600 /etc/netplan/50-cloud-init.yaml     # the file holds the password
   sudo netplan generate
   sudo netplan try
   ```
   `netplan try` applies the change and **undoes it by itself after 120 s unless you press Enter**, so a typo cannot lock you out. If you are connected by ssh over the Wi-Fi being changed, the session may drop: reconnect (step 4), or just wait for the automatic undo and fix the file. When it works, make it permanent with `sudo netplan apply`.
4. **Find the Pi on the new network**: from the laptop (on the same network) `ssh hexa-pi` works through `hexa.local`. If it does not, on the Pi (screen and keyboard, or Ethernet) run `ip -br a show wlan0` to get the address, then `ssh hexa@<that address>`, and change `HostName` under `Host hexa-pi` in `~/.ssh/config` on the laptop. The phone page address changes with the network: run `scripts/pi_dryrun.sh status` for the new one.
5. **Optional, so cloud-init never overwrites your edit**: `echo 'network: {config: disabled}' | sudo tee /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg`.
6. **To forget the old network later**: delete its two lines from the same file and run `sudo netplan try` again.

If nothing connects at all, plug in an Ethernet cable (the default Ubuntu setup asks for an address by DHCP on `eth0`; I have not verified your file) or use a screen and keyboard, then restore the backup: `sudo cp ~/netplan-backup.yaml /etc/netplan/50-cloud-init.yaml && sudo netplan apply`.

### Install the missing hardware tool later (owner-run)

`sudo apt install -y i2c-tools` (day 1 of the hardware, for `i2cdetect`). Turning on I2C itself is in `plan.md`, stage 11e.

### Servo pins and wiring

Where each wire goes and the numbers I need back (minimum, centre and maximum per servo) are in [HARDWARE.md](HARDWARE.md), section "Pin and wiring locations" and "Hardware numbers I need from you".
