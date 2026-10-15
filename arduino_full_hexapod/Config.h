#pragma once

// ============================================================
//  Config.h — Hardware & timing constants for MG995 + PCA9685
// ============================================================

// ── FRONT LEGS TEST CONFIGURATION ───────────────────────────
// Set to 'true' to test only front 2 legs with 1 PCA9685 (0x40)
// Set to 'false' when all 6 legs and both PCA9685 boards are ready
#define FRONT_LEGS_ONLY     true

// Channels on single PCA9685 (0x40) when FRONT_LEGS_ONLY is true:
// Front-Left (Leg 5):
#define CH_FLEFT_COXA       0
#define CH_FLEFT_FEMUR      1
#define CH_FLEFT_TIBIA      2
// Front-Right (Leg 0):
#define CH_FRIGHT_COXA      3
#define CH_FRIGHT_FEMUR     4
#define CH_FRIGHT_TIBIA     5

// ── Servo PWM (MG995 spec: 1000–2000 µs at 50 Hz) ──────────
#define SERVO_FREQ          50       // Hz
#define PULSE_MIN_US        1000     // µs  (0°  position)
#define PULSE_MAX_US        2000     // µs  (180° position)
#define PULSE_MID_US        1500     // µs  (90° / neutral)

// PCA9685 at 50Hz: 1 count = 20000µs / 4096 = 4.8828µs
// pulse_count = pulse_us / 4.8828
#define US_PER_COUNT        4.8828f
#define PULSE_MIN_CNT       ((int)(PULSE_MIN_US / US_PER_COUNT))   // ≈ 205
#define PULSE_MAX_CNT       ((int)(PULSE_MAX_US / US_PER_COUNT))   // ≈ 410

// ── Leg geometry (mm) — from pihexa/config.py ───────────────
#define LEG_ROOT2JOINT1     23.4f
#define LEG_J1_TO_J2        32.0f
#define LEG_J2_TO_J3        54.51f
#define LEG_J3_TO_TIP       92.91f

// ── Body mount positions (mm) ────────────────────────────────
#define MOUNT_LR_X          34.2f
#define MOUNT_OT_X          25.14f
#define MOUNT_OT_Y          58.14f

// ── Angle limits (degrees) ───────────────────────────────────
#define COXA_MIN           -45.0f
#define COXA_MAX            45.0f
#define FEMUR_MIN          -45.0f
#define FEMUR_MAX           75.0f
#define TIBIA_MIN          -60.0f
#define TIBIA_MAX           60.0f

// ── Timing ───────────────────────────────────────────────────
#define MOVEMENT_INTERVAL_MS    5     // ms — main loop delay
#define MOVEMENT_SWITCH_MS      150   // ms — gait transition time
#define STEP_DURATION_MS        20    // ms — gait step duration

// ── Movement modes ───────────────────────────────────────────
#define MODE_STANDBY        0
#define MODE_FORWARD        1
#define MODE_FORWARD_FAST   2
#define MODE_BACKWARD       3
#define MODE_TURN_LEFT      4
#define MODE_TURN_RIGHT     5
#define MODE_SHIFT_LEFT     6
#define MODE_SHIFT_RIGHT    7
#define MODE_CLIMB          8
#define MODE_ROTATE_X       9
#define MODE_ROTATE_Y       10
#define MODE_ROTATE_Z       11
#define MODE_TWIST          12
#define MODE_WAVE           13
#define MODE_CENTER         14
