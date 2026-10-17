# Hardware: Raspberry Pi, servos, wiring, bring-up

Status: the servo backend (`body/servo_backend.py`) is written, reviewed and tested against fake
drivers. **No servo has been powered under software control yet and nothing is calibrated.** The
calibration file holds placeholders (`"calibrated": false`) and the backend refuses to drive a
joint that is not calibrated. Bring-up stages 11e (bench, first power-up, calibration) and 11f
(ground tests) are open in `ROADMAP.md`.

## Rules (owner)

- **Never `sudo` from an AI agent or a script on the Pi.** Anything that needs it is listed under
  "Owner-run setup" and the owner runs it in their own session.
- Touch only `~/hexa` on the Pi. Nothing is enabled at boot (no systemd unit, no `@reboot`).
  `deploy/hexa.service` is the dry-run unit, written and not installed; a unit with a hardware
  backend must never be enabled at boot until the kill switch and calibration are done.
- **The 18 servos run from their own 5-6 V high-current supply. The Pi never powers them.** Common
  ground between the supply, the PCA9685 and the Pi.
- A physical **kill switch in the servo power line** (within reach, plus a fuse) is the emergency
  stop. Software cannot stop servos after `kill -9` or power loss.
- Real servos start only with `python main.py --backend servo` (or the servo scripts) and a typed
  `yes`. `HEXA_BACKEND=servo` is refused; servo is never the default.
- First power-up and calibration: robot **held off the ground**, kill switch in hand.

## What is wired

One PCA9685 (I2C address `0x40`, all jumpers open, 50 Hz) drives 16 joints; the two rear coxas use
the Pi's two hardware-PWM pins. Board-level numbers are in `config.py` (hardware section); every
per-joint number (output, centre, direction sign, offset, pulse window, calibrated) is in
`data/calibration/servo_cal.json`, validated by `body/servo_calibration.py`. Nothing else may hold
a servo number.

| Joint | Output | Joint | Output | Joint | Output |
|---|---|---|---|---|---|
| RF_coxa | PCA 0 | RR_coxa | **GPIO12** (PWM0, pin 32) | LM_coxa | PCA 10 |
| RF_femur | PCA 1 | RR_femur | PCA 6 | LM_femur | PCA 11 |
| RF_tibia | PCA 2 | RR_tibia | PCA 7 | LM_tibia | PCA 12 |
| RM_coxa | PCA 3 | LR_coxa | **GPIO13** (PWM1, pin 33) | LF_coxa | PCA 13 |
| RM_femur | PCA 4 | LR_femur | PCA 8 | LF_femur | PCA 14 |
| RM_tibia | PCA 5 | LR_tibia | PCA 9 | LF_tibia | PCA 15 |

Legs are named clockwise from above starting front-right: RF, RM, RR, LR, LM, LF (mount angles
30, 90, 150, 210, 270, 330 degrees from +X). Coxa turns the leg about the vertical axis, femur
lifts the thigh, tibia bends the shin. Signs in the file are a first guess (the left side is
mirrored); the calibration confirms them.

### Pi header (Pi powered off while wiring; confirm pin numbers on the board)

| Pi pin | Name | Goes to |
|---|---|---|
| 1 | 3V3 | PCA9685 VCC (logic only) |
| 3 | GPIO2 / SDA1 | PCA9685 SDA (`/dev/i2c-1`) |
| 5 | GPIO3 / SCL1 | PCA9685 SCL |
| 6 | GND | PCA9685 GND and the servo supply ground (common ground) |
| 32 / 33 | GPIO12 / GPIO13 | signal of the RR_coxa / LR_coxa servo |
| 2, 4 | 5 V | **not used**; never power servos from the Pi |

PCA9685 V+/GND screw terminal: the servo supply, **through the fuse and the kill switch**. Each
channel header: signal, V+ (red), GND (brown/black); an MG995 is brown GND, red V+, orange signal.
Put a bulk capacitor (about 1000 uF per 4-6 servos, rated above the supply voltage) across V+ and
GND at the board. The two GPIO-driven servos need their own V+ and GND from the same supply and the
Pi GND as reference.

### Optional OE wire (protects the PCA9685 channels after `kill -9`)

Wire a Pi GPIO to the PCA9685 **OE** pin (active low) with an external 10 k pull-up from OE to
3.3 V, set `config.PCA9685_OE_GPIO` to that GPIO number, and install `requirements-pi-oe.txt`
(libgpiod bindings). The backend then drives OE low only while attached; if the process dies for
any reason the kernel releases the line, the pull-up takes OE high and the PCA9685 outputs go off.
The two GPIO-PWM servos are **not** covered: after `kill -9` the kernel keeps their last pulse, so
only the kill switch stops those.

## Owner-run setup on the Pi (each needs `sudo`; Ubuntu 24.04 Server, Python 3.12, aarch64)

Tick these off in order. Reboot where noted.

1. [ ] Packages: `sudo apt install -y python3.12-venv python3-dev build-essential alsa-utils libportaudio2 portaudio19-dev libsndfile1 ffmpeg i2c-tools`
2. [ ] Groups: add the `hexa` user to `audio` and `i2c` (`sudo usermod -aG audio,i2c hexa`), log in again.
3. [ ] I2C: add `dtparam=i2c_arm=on` to `/boot/firmware/config.txt`, load `i2c-dev` (`echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf`), reboot. Check `ls /dev/i2c-1` and, with the PCA9685 powered from the Pi's 3.3 V logic side only and **no servo power connected**, `i2cdetect -y 1` shows `40`.
4. [ ] Hardware PWM for the two rear coxas: add `dtoverlay=pwm-2chan` to `/boot/firmware/config.txt`, reboot, check `ls /sys/class/pwm/pwmchip2` (the chip number is `config.GPIO_PWM_CHIP`).
5. [ ] A udev rule so the `hexa` user may write `/sys/class/pwm` (the files are root-owned by default). **Untested starting point; verify on the Pi before relying on it** (`ls -l /sys/class/pwm/pwmchip2/ /sys/class/pwm/pwmchip2/pwm0`): `/etc/udev/rules.d/99-hexa-pwm.rules` containing
   `SUBSYSTEM=="pwm", ACTION=="add|change", RUN+="/bin/sh -c 'chgrp -R hexa /sys%p && chmod -R g=u /sys%p'"`
   then `sudo udevadm control --reload && sudo udevadm trigger`. The backend reports "no permission to write ..." until this works.
6. [ ] Only if the OE wire is fitted: the user may need access to the GPIO chip (`/dev/gpiochip4` on the Pi 5, check `gpiodetect`; see `config.GPIO_CHIP_PATH`).
7. [ ] Ollama (optional chat): never the official install script (it enables a boot service). Unpack the tarball under `~/ollama` as below; no `sudo`.

Not needed and not done: enabling `hexa.service`, anything at boot, firewall rules (a `ufw` rule
for the phone page, if the owner turns `ufw` on, is the owner's).

## Bench bring-up (stage 11e) and ground tests (stage 11f)

Stop at the first step that does not match.

1. **Power rail first, no servo plugged in**: supply -> fuse -> kill switch -> PCA9685 V+; common
   ground; capacitor; the Pi on its own supply. With a multimeter: switch off = 0 V on V+, on = the
   supply voltage (not above the servo rating), polarity correct, no short between V+ and GND.
2. **Software gates on the Pi**: `scripts/sync_to_pi.sh`, then the dry-run (below) and the test
   suite on the Pi pass. `python main.py --backend servo` must refuse while the file is
   uncalibrated.
3. **Find the centre, horn REMOVED**: one servo on its channel, powered through the kill switch.
   `python scripts/servo_jog.py --joint RF_coxa`: step the pulse (`a/d` fine, `z/c` coarse) to mid
   travel, note it with `n`, write with `w`. Listen for buzzing; watch the supply current. Never
   past what the servo allows (the absolute window is `SERVO_PULSE_ABS_MIN_US..MAX_US`).
4. **Fit horns at neutral**: `python scripts/servo_center.py --joints RF_coxa RF_femur RF_tibia`
   holds those joints at their centre so horns and legs are attached in the neutral pose. Enter
   releases the servos. It refuses a joint whose centre is still the 1500 us placeholder.
5. **Sign and range, leg attached, robot held off the ground**: run `servo_jog.py` again. Note the
   direction sign (`+`/`-`: positive coxa swings the foot counter-clockwise seen from above;
   positive femur raises the knee; positive tibia opens the knee), the safe minimum (`m`) and
   maximum (`M`, no stall, no touching the frame), then `w`. A joint is calibrated when centre,
   sign, min and max are all noted. The tool rewrites `data/calibration/servo_cal.json`; commit it
   with the date. `sync_to_pi.sh` seeds the file once and never overwrites the Pi's measured copy;
   copy it back with `scp hexa-pi:hexa/data/calibration/servo_cal.json data/calibration/`.
6. Repeat for all 18 joints, one at a time.
7. **`kill -9` test**: `servo_center.py` prints its process id. `kill -9 <pid>` and watch: the
   PCA9685 servos hold their last pulse (go limp with the OE wire); the GPIO-PWM servos hold too.
   Switch the kill switch to remove power. Knowing this, never leave a powered robot unattended.
8. **Keyboard drive, held off the ground**: `python scripts/servo_teleop.py` (starts sitting; `1`
   stand, WASD/QE move, Space stop, `X` stop, sit and quit). Ctrl-C and a closed terminal sit the
   robot down and release the servos.
9. **Ground tests**: on a stand first, then the ground, starting with `stand`. Done when stand,
   sit, walk and stop behave like the simulation, the kill switch removes servo power while the Pi
   keeps running, and killing the brain process stops the robot via the watchdog (1 s).

Safety behaviour you will see: the backend powers up **sitting**, attaches one leg at a time
(`SERVO_STAGGER_S`), slews every output (`SERVO_SLEW_DEG_S`), releases every channel after
`SERVO_IDLE_RELEASE_S` of sitting idle, and on SIGINT/SIGTERM/SIGHUP sits down (if standing, at
most `SERVO_SHUTDOWN_SIT_TIMEOUT_S`) and releases.

## Servo and power sizing

No servo is chosen; the MG995 (analog, about 500-2500 us over 180 degrees) is what is on hand.
`python scripts/torque_report.py` (writes to `.tmp/`) gives the sim's needs; the sim servo is
ideal (no friction, backlash or sag), the masses in `config.py` are placeholders (weigh the real
parts), so treat the numbers as a floor. Last run (worst case over stand, sit, wave, walk, strafe,
turn):

| Joint | Peak torque | Peak x2 (safety factor) | RMS x2 | Speed needed |
|---|---|---|---|---|
| coxa | 2.09 N*m (21 kg*cm) | 4.18 N*m (43 kg*cm) | 0.58 N*m | 0.17 s / 60 deg |
| femur | 1.29 N*m (13 kg*cm) | 2.59 N*m (26 kg*cm) | 0.57 N*m | 0.21 s / 60 deg |
| tibia | 0.90 N*m (9 kg*cm) | 1.80 N*m (18 kg*cm) | 0.33 N*m | 0.23 s / 60 deg |

Compare with the datasheet at **your** supply voltage: rated stall torque at least "peak x2";
speed no slower than the table (a loaded servo is slower: keep margin); travel containing the
joint's range; metal gears for femur and tibia. Supply current:
`I_peak_total = N_peak * I_stall + (18 - N_peak) * I_run`, `I_supply >= 1.25 * I_peak_total`
(`N_peak` = joints loaded together, from the report's section 5), plus wire gauge for that current.
Check leg length and total mass against the stall torque before cutting a frame. The parked branch
`step-10d-mg995` has earlier MG995 planning values (see `ROADMAP.md`).

## Working on the Pi

`ssh hexa-pi` (user `hexa`, host `hexa.local`, key auth). The Pi has no PyBullet: it runs the
dry-run body, which moves nothing and logs what the gait would send to `logs/dryrun.log`.

```bash
# laptop: put the code on the Pi (never --delete; models, .venv, logs stay)
scripts/sync_to_pi.sh --dry-run        # preview
scripts/sync_to_pi.sh [--assets]       # --assets also sends the models over the LAN (once)

# Pi, first time
cd ~/hexa && python3 -m venv .venv
export TMPDIR=~/hexa/.tmp; .venv/bin/pip install -r requirements-pi.txt   # wheels only; srt comes from wheelhouse/
scripts/fetch_models.sh                # only what is missing, ARM Piper

# Pi, run (tmux, survives ssh closing)
scripts/pi_dryrun.sh start 424242      # phone page + PIN on the LAN; prints the address
scripts/pi_dryrun.sh log | status | attach | stop

# Pi, tests
export TMPDIR=$HOME/hexa/.tmp HEXA_BACKEND=dryrun
.venv/bin/python -m pytest -q          # expect 0 failed; the tests that need PyBullet skip
```

`wheelhouse/` (built on the laptop by `scripts/build_wheelhouse.sh`, sent by `sync_to_pi.sh`)
holds the one wheel PyPI lacks (`srt`). If a dependency has no aarch64 wheel for Python 3.12,
report its name and version and stop; nothing is built on the Pi.

**Ollama on the Pi** (by hand, no boot service, about 1.6 GB download, resumable):

```bash
mkdir -p ~/ollama && cd ~/ollama
curl -fL -C - -o ollama-linux-arm64.tar.zst https://ollama.com/download/ollama-linux-arm64.tar.zst
tar --zstd -xf ollama-linux-arm64.tar.zst && rm ollama-linux-arm64.tar.zst
tmux new-session -d -s ollama '~/ollama/bin/ollama serve'
~/ollama/bin/ollama pull qwen2.5:0.5b && ~/ollama/bin/ollama pull qwen2.5:1.5b   # local models only
cd ~/hexa && export TMPDIR=$HOME/hexa/.tmp HEXA_BACKEND=dryrun
.venv/bin/python scripts/measure_chat.py --model qwen2.5:0.5b --with-voice      # bar: 3 tokens/s, first token < 5 s
tmux kill-session -t ollama            # frees about 1 GB when done
```

**Changing the Pi's Wi-Fi** (netplan + wpa_supplicant, no NetworkManager): `sudo cp
/etc/netplan/50-cloud-init.yaml ~/netplan-backup.yaml`, add the new network under
`wifis: wlan0: access-points:` **next to** the old one (spaces only, names and passwords in
quotes), `sudo chmod 600` the file, `sudo netplan generate`, `sudo netplan try` (it undoes itself
after 120 s unless you press Enter), then `sudo netplan apply`. Find the Pi again with
`ssh hexa-pi`; if that fails read the address with `ip -br a show wlan0` on the Pi. With no network
at all use Ethernet or a screen and restore the backup.

### Pi problems

| Problem | Try |
|---|---|
| Chat says "I can't think right now" | Is the `ollama` tmux session up (`tmux ls`)? `curl -s http://127.0.0.1:11434/api/version`; `~/ollama/bin/ollama list` |
| Cannot reach `hexa.local` or the page after a Wi-Fi change | Same network as the laptop; wait a minute for mDNS; read the address on the Pi |
| `pi_dryrun.sh start` says "already running" or the page will not open | `scripts/pi_dryrun.sh status`, then `stop` and `start`; use the `--lan` address it prints (plain `http://`) |
| "no permission to write /sys/class/pwm/..." | Setup step 5 (udev rule) |
| "/sys/class/pwm/pwmchip2 not found" | Setup step 4 (`dtoverlay=pwm-2chan`, reboot) |
| `i2cdetect` shows nothing | Setup step 3, wiring, PCA9685 powered, `/dev/i2c-1` exists |

## Audio devices

`MIC_DEVICE` and `SPEAKER_DEVICE` in `config.py` (`None` = system default). List inputs with
`python scripts/mic_check.py`; one-off `--device N` on `main.py`. Audio is 16 kHz mono. The Pi 5
has no analog jack: use a USB microphone and speaker or a USB headset (user in the `audio` group).
No audio device has been tried on the Pi yet.

## If the wiring changes

Edit the output/centre/sign cells in `data/calibration/servo_cal.json` (or re-run `servo_jog.py`),
re-calibrate with the robot off the ground, and update the table above. Never put a servo number
in `config.py`.
