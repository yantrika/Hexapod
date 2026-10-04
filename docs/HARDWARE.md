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
