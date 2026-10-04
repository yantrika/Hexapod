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
