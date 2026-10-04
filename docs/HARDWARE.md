# Hardware: servos, pins, power

## Be clear about the status

**Nothing for the real robot exists in the code yet.**

- `body/servo_backend.py` is a placeholder. If you start it, it stops with `NotImplementedError`.
- So there are **no pin numbers, no servo channels and no calibration values anywhere in the project today**. Do not look for them; they were never written.
- `requirements-pi.txt` only has a comment that a servo driver (for example Adafruit ServoKit) will go there. That is an example, not a decision.
- Real hardware is **Step 11** in `plan.md`.

This page tells you what Step 11 will need and **where each number will live**, so you can fill it in by hand.

## The rule: one place for servo numbers

All servo-specific numbers live **only** in a calibration table inside `body/servo_backend.py`. Nothing else in the project may know which servo is which.

| Where | What it holds |
|---|---|
| `config.py` | The clean robot: sizes, ±90° hard limits, walking limits. Same for sim and real robot. |
| `body/servo_backend.py` table (Step 11) | For each of the 18 joints: board channel, centre, direction (sign), offset, pulse range. |

"Clean frame" means: angle 0 = the neutral pose (stand). The simulation and the maths always use this. The table converts a clean angle into what a real servo needs. **Left-side direction flips come only from the table**, never from other code.

## The 18 joints (fill this in at Step 11)

Joint order is fixed in `config.JOINT_NAMES` (leg by leg). Legs are named around the body, **clockwise as seen from above, starting front-right**:

```
            front (+X)
        LF  \   /  RF         RF mount 30 deg   LF mount 330 deg
   LM  ----  body  ----  RM   RM mount 90 deg   LM mount 270 deg
        LR  /   \  RR         RR mount 150 deg  LR mount 210 deg
            back
```

Copy this table into your notes (or into the Step 11 code) and fill the empty cells as you wire and calibrate:

| Joint | Board channel | Centre (pulse or deg) | Direction (+1 / -1) | Offset (deg) | Pulse range |
|---|---|---|---|---|---|
| RF_coxa | | | | | |
| RF_femur | | | | | |
| RF_tibia | | | | | |
| RM_coxa | | | | | |
| RM_femur | | | | | |
| RM_tibia | | | | | |
| RR_coxa | | | | | |
| RR_femur | | | | | |
| RR_tibia | | | | | |
| LR_coxa | | | | | |
| LR_femur | | | | | |
| LR_tibia | | | | | |
| LM_coxa | | | | | |
| LM_femur | | | | | |
| LM_tibia | | | | | |
| LF_coxa | | | | | |
| LF_femur | | | | | |
| LF_tibia | | | | | |

Joint meaning: **coxa** turns the leg left/right (about the vertical axis); **femur** lifts the thigh; **tibia** bends the shin.

## Limits to respect

- Hard limits: **-90° to +90°** for every joint (`JOINT_HARD_LIMITS_DEG`). Every angle is clamped in one place (`HexapodBackend` in `body/backend.py`). The servo's real range must contain this range, or the table must map it inside the servo's range.
- Walking limits are tighter (`GAIT_SOFT_LIMITS_DEG`, coxa about ±30°). They stop legs hitting each other.

## Servo and power sizing (a method, not figures)

**No servo has been chosen and no current figure is invented here.** The simulation gives torque and speed needs; you fill the datasheet numbers in. Re-run after replacing the placeholder masses (`config.py`).

1. **Torque and speed from the sim.** `python scripts/torque_report.py` writes [TORQUE_REPORT.md](TORQUE_REPORT.md): per joint type (coxa, femur, tibia) the peak and RMS torque in N*m and kg*cm, with and without `TORQUE_SAFETY_FACTOR`, and the speed as seconds per 60 degrees. Read its "Limits" section: the sim has no gear friction or backlash and a lowered velocity gain (it understates the peak).
2. **Compare with the datasheet** (all at YOUR supply voltage, usually 6.0 V or 7.4 V, not the headline voltage):

   | Datasheet number | Must be |
   |---|---|
   | Rated stall torque (kg*cm) | at least `peak x factor` of that joint type |
   | Speed (s / 60 deg, no load) | at most the report's "needs s per 60 deg" (a loaded servo is slower: keep margin) |
   | Travel | contains the clean-frame range of the joint (see Limits above) |
   | Stall current (A) | `I_stall`, used below |
   | Running (loaded, moving) current (A) | `I_run`, used below |
   | Idle current (A) | `I_idle` (holding still, unloaded) |
   | Operating voltage range | contains the supply you will use |
   | Gear type, rotation limit | metal gears for the femur and tibia; check the horn spline |

3. **How many joints are loaded together.** The report's section 5 counts, at the same physics step, how many of the 18 joints are at 80 % of their peak torque. Use the **larger** (p99-referenced) maximum for the walk as `N_peak`; the report currently shows the walk's counts, and the other motions, with the placeholder masses.
4. **Supply current formula** (fill the placeholders from the datasheet):

   ```
   I_peak_total   = N_peak * I_stall + (18 - N_peak) * I_run     # worst short moment
   I_walk_average = 18 * I_run_walk                              # sustained walking
   I_supply       >= 1.25 * I_peak_total                         # 25 % margin, at the servo voltage
   ```

   `I_run_walk` is the datasheet running current scaled to the load: the report's RMS torque divided by the servo's rated torque gives the load fraction to read off the datasheet's current-versus-load curve if there is one. Without a curve use `I_run` (conservative). Add the PCA9685 boards' logic current and the Pi's own supply separately (the Pi never shares the servo supply).
5. **Wiring and boards.** Two PCA9685 boards (16 channels each, 18 servos needed): board 0 at I2C address `0x40` (default, all address jumpers open), board 1 at `0x41` (jumper A0 closed). Check with `i2cdetect -y 1`: both must appear. Servo power goes to each board's V+ terminal from the servo supply; the boards' logic (VCC) comes from the Pi's 3.3 V. A bulk capacitor near each board; a kill switch in the servo supply line.

### BOM checklist

| Item | Spec needed | Status |
|---|---|---|
| 18 servos | torque and speed from step 2, travel, metal gears, voltage | not chosen |
| Servo supply | at least `I_supply` from step 4, at the servo voltage, with a kill switch | not chosen |
| PCA9685 boards | 2 pieces, addresses `0x40` and `0x41` | not bought |
| Bulk capacitors | near each board | not bought |
| Raspberry Pi 5 | 8 GB preferred, active cooler, its own 5 V / 5 A supply | not bought |
| Wiring | gauge rated for `I_peak_total` on the servo power rails, common ground | not bought |
| Battery or mains supply | capacity for the run time you want (`I_walk_average`) | not chosen |
| Scale | to weigh the real parts (replace the placeholder masses) | have / need |
| Stand or blocks | robot held off the ground for the first power-up | not made |

## Power (required, from `plan.md`)

- The 18 servos need their **own high-current 5–6 V supply**.
- Connect its ground to the Pi's ground (common ground).
- **The Pi must never power the servos.**
- Add a physical power switch. It is the emergency stop.
- Servo brown-outs or a Pi reset are listed as a risk in `plan.md`.

## Pin and wiring locations (Raspberry Pi 5 to PCA9685 to servos)

Nothing here is wired by the software yet; this is where each wire goes so you can build it and report real numbers. Servo code is not written until you say the hardware is on the bench (`plan.md`, stage 11e).

### The Pi's 40-pin header

Pin 1 is at the end of the header nearest the SD card slot, in the inner row (the row closer to the middle of the board); pin 2 is next to it in the outer row. Odd pins are the inner row, even pins the outer row. Confirm with the pin-number markings on the board (or the official Raspberry Pi pinout) BEFORE you connect anything, and wire with the Pi powered off.

| Pi pin | Name | Use |
|---|---|---|
| **1** | 3V3 | PCA9685 **VCC** (logic only, a few mA) |
| **3** | GPIO2 / **SDA1** | PCA9685 **SDA** (the I2C data wire, device `/dev/i2c-1`) |
| **5** | GPIO3 / **SCL1** | PCA9685 **SCL** (the I2C clock wire) |
| **6** | GND | PCA9685 **GND** (common ground; pins 9, 14, 20, 25, 30, 34, 39 are also GND) |
| 2 and 4 | 5 V | **Not used.** Never power servos from the Pi's 5 V pins |

Both PCA9685 boards share the same four wires (VCC, GND, SDA, SCL) in parallel. The Pi's own pins carry no servo current.

### One PCA9685 board

| Board pin / terminal | Connect to |
|---|---|
| **VCC** (6-pin header) | Pi pin 1 (3V3) |
| **GND** (6-pin header) | Pi pin 6 (GND) and the servo supply ground (common ground) |
| **SDA**, **SCL** | Pi pin 3, Pi pin 5 |
| **OE** (output enable, active low) | Leave it unconnected or tied to GND = outputs enabled. A later option is a Pi GPIO to OE as a software disable; that is NOT the kill switch (the physical switch in the servo power line is) |
| **V+ / GND screw terminal** | The servo supply, **through the fuse and the kill switch**. Never the Pi |
| **Channel 0-15** (3 pins each) | One servo per channel: signal (PWM) pin to the servo's signal wire, middle pin V+ (red), outer pin GND (brown or black). On an MG995 the wires are brown (GND), red (V+), orange (signal) |
| **A0-A5 solder pads** | Close A0 on the second board to make its address `0x41`; the first board keeps `0x40` |

### Proposed channel assignment (you confirm or change it; it only becomes code at stage 11e)

Joint order is `config.JOINT_NAMES` (leg by leg: coxa, femur, tibia). One board per side keeps each supply rail to nine servos:

| Board | Address | Channels 0-8 | Channels 9-15 |
|---|---|---|---|
| Right side | `0x40` | 0 RF_coxa, 1 RF_femur, 2 RF_tibia, 3 RM_coxa, 4 RM_femur, 5 RM_tibia, 6 RR_coxa, 7 RR_femur, 8 RR_tibia | spare |
| Left side | `0x41` | 0 LR_coxa, 1 LR_femur, 2 LR_tibia, 3 LM_coxa, 4 LM_femur, 5 LM_tibia, 6 LF_coxa, 7 LF_femur, 8 LF_tibia | spare |

Check on the Pi (owner-run, see HOW_TO.md): `i2cdetect -y 1` must show `40` (and `41` for two boards).

## Hardware numbers I need from you (minimum, centre, maximum)

Measure these on the bench (one servo at a time, horn removed, supply behind the kill switch, see `plan.md` day-1 checklist) and send me the filled tables. I will put them ONLY into the calibration table in `body/servo_backend.py`.

### Per servo type (one row for the MG995 you have)

| Item | Your value |
|---|---|
| Servo model and how many you have | |
| Supply voltage you will use (V) | |
| PWM frequency (Hz) the datasheet allows (the PCA9685 is set to 50 by default) | |
| **Minimum** pulse that is safe, no stall (us) | |
| **Centre** pulse (us, measured, not assumed 1500) | |
| **Maximum** pulse that is safe, no stall (us) | |
| Travel between min and max (degrees, measured with a protractor) | |
| Stall torque at your voltage (kg*cm) and stall current (A) | |
| No-load current and loaded running current (A) | |

### Per joint (18 rows; the first two columns are the proposed wiring above)

| Joint | Board / channel | Min pulse (us) | Centre pulse (us) | Max pulse (us) | Direction (+1 / -1) | Mechanical min / max angle (deg) |
|---|---|---|---|---|---|---|
| RF_coxa | 0x40 / 0 | | | | | |
| RF_femur | 0x40 / 1 | | | | | |
| RF_tibia | 0x40 / 2 | | | | | |
| RM_coxa | 0x40 / 3 | | | | | |
| RM_femur | 0x40 / 4 | | | | | |
| RM_tibia | 0x40 / 5 | | | | | |
| RR_coxa | 0x40 / 6 | | | | | |
| RR_femur | 0x40 / 7 | | | | | |
| RR_tibia | 0x40 / 8 | | | | | |
| LR_coxa | 0x41 / 0 | | | | | |
| LR_femur | 0x41 / 1 | | | | | |
| LR_tibia | 0x41 / 2 | | | | | |
| LM_coxa | 0x41 / 3 | | | | | |
| LM_femur | 0x41 / 4 | | | | | |
| LM_tibia | 0x41 / 5 | | | | | |
| LF_coxa | 0x41 / 6 | | | | | |
| LF_femur | 0x41 / 7 | | | | | |
| LF_tibia | 0x41 / 8 | | | | | |

"Mechanical min / max angle" is the angle the leg can really reach before it hits the frame or another leg, measured in the clean frame (0 = the neutral stand). It must stay inside the hard limits of `-90` to `+90` degrees, or I tighten the table.

### Power and frame (also asked in `plan.md`)

| Item | Your value |
|---|---|
| Servo supply type (battery chemistry and cells, or mains) and voltage | |
| Supply continuous and peak current (A) | |
| Fuse rating and type; kill switch current and voltage rating | |
| Wire gauge on the servo rails | |
| Coxa, femur and tibia lengths (mm); body radius (mm) | |
| Leg mount angles if they differ from `config.py` (RF 30, RM 90, RR 150, LR 210, LM 270, LF 330 degrees) | |
| Total robot weight with battery (g) | |

## Safe bring-up order (from `plan.md`, Step 11)

1. Write the table and the backend; run its tests (a fake driver, no robot).
2. Calibrate with the **robot held off the ground**.
3. Ground tests, starting with `stand`.
4. Check stand, sit, walk, stop behave like the simulation.
5. Kill the brain program while walking: the robot must stop by itself within `WATCHDOG_TIMEOUT_S` (1 second).

## Audio devices (microphone and speaker)

These are not pins, but you may need to change them on the Pi:

- `MIC_DEVICE` and `SPEAKER_DEVICE` in `config.py` (`None` = system default).
- Find the numbers with `python scripts/mic_check.py` (lists inputs). Use the number with `--mic N` on the check scripts.
- Audio is 16 kHz mono (`AUDIO_SAMPLE_RATE`).

## If you change the wiring later

1. Change the channel/centre/sign cells in the table in `body/servo_backend.py` only.
2. Re-run the calibration with the robot off the ground.
3. Update the table in your notes and this page.
4. Do not edit `config.py` for a wiring change.
