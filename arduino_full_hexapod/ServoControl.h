#pragma once
#include <Adafruit_PWMServoDriver.h>
#include "Config.h"
#include "Kinematics.h"
#include "Gait.h"

// ============================================================
//  ServoControl.h — Angle → PWM pulse → PCA9685 channel
//  Mirrors pihexa/servo.py  Servo.set_angle() & angle2pulse()
//
//  PCA9685 channel map (same as servo.py):
//
//   RIGHT board (0x41) — legs 0, 1, 2
//    leg 0 (front-right) : ch  5, 6, 7   (coxa, femur, tibia)
//    leg 1 (mid-right)   : ch  2, 3, 4
//    leg 2 (rear-right)  : ch  8, 9, 10
//
//   LEFT board (0x40)  — legs 3, 4, 5
//    leg 3 (rear-left)   : ch  8, 9, 10
//    leg 4 (mid-left)    : ch  2, 3, 4
//    leg 5 (front-left)  : ch  5, 6, 7
// ============================================================

// ── Convert degree angle to PCA9685 count ───────────────────
//  angle2pulse mirrors servo.py:
//    pulse = (pulse_min + pulse_max)/2 + reverse * angle * ((pulse_max - pulse_min)/180)
inline int angleToPulse(float angle, int reverse) {
  float pulse_us = PULSE_MID_US + reverse * angle * ((PULSE_MAX_US - PULSE_MIN_US) / 180.0f);
  pulse_us = constrain(pulse_us, (float)PULSE_MIN_US, (float)PULSE_MAX_US);
  return (int)(pulse_us / US_PER_COUNT);
}

// ── Set one servo via leg/joint index ────────────────────────
inline void setServoAngle(int legIdx, int partIdx, float angle,
                          float offset[6][3],
                          Adafruit_PWMServoDriver* pwm_left,
                          Adafruit_PWMServoDriver* pwm_right)
{
  int ch;
  Adafruit_PWMServoDriver* pwm;

#if FRONT_LEGS_ONLY
  // Single PCA9685 board (0x40):
  // Front-Left (Leg 5) on Ch 0, 1, 2
  // Front-Right (Leg 0) on Ch 3, 4, 5
  if (legIdx == 5) {
    ch = (partIdx == 0) ? CH_FLEFT_COXA : ((partIdx == 1) ? CH_FLEFT_FEMUR : CH_FLEFT_TIBIA);
    pwm = pwm_left;
  } else if (legIdx == 0) {
    ch = (partIdx == 0) ? CH_FRIGHT_COXA : ((partIdx == 1) ? CH_FRIGHT_FEMUR : CH_FRIGHT_TIBIA);
    pwm = pwm_left;
  } else {
    return; // Skip other 4 legs
  }
#else
  // 6-Leg 2-Board Mapping — exact copy of servo.py set_angle()
  if      (legIdx == 0) { ch = 5 + partIdx; pwm = pwm_right; }
  else if (legIdx == 1) { ch = 2 + partIdx; pwm = pwm_right; }
  else if (legIdx == 2) { ch = 8 + partIdx; pwm = pwm_right; }
  else if (legIdx == 3) { ch = 8 + partIdx; pwm = pwm_left;  }
  else if (legIdx == 4) { ch = 2 + partIdx; pwm = pwm_left;  }
  else if (legIdx == 5) { ch = 5 + partIdx; pwm = pwm_left;  }
  else return;
#endif

  // Apply calibration offset
  float corrected = angle + offset[legIdx][partIdx];

  // Femur (partIdx==1) is reversed — same as servo.py
  int reverse = (partIdx == 1) ? -1 : 1;

  int pulse = angleToPulse(corrected, reverse);
  if (pwm != NULL) {
    pwm->setPWM(ch, 0, pulse);
  }
}

// ── Drive legs from current GaitState ───────────────────────
inline void updateLegs(GaitState* g, float offset[6][3],
                       Adafruit_PWMServoDriver* pwm_left,
                       Adafruit_PWMServoDriver* pwm_right)
{
  for (int leg = 0; leg < 6; leg++) {
#if FRONT_LEGS_ONLY
    // Only process Front-Right (0) and Front-Left (5)
    if (leg != 0 && leg != 5) continue;
#endif

    float angles[3];

    if (g->mode == MODE_CENTER) {
      // Hold mechanical neutral center (90° / 1500 us)
      angles[0] = 0.0f;
      angles[1] = 0.0f;
      angles[2] = 0.0f;
    } else {
      // Convert world tip → local frame → IK angles
      Vec3 localPt = world2local(leg, g->tipPos[leg]);
      inverseKinematics(localPt, angles);
      clampAngles(angles);
    }

    // Write each joint (coxa, femur, tibia)
    for (int joint = 0; joint < 3; joint++) {
      setServoAngle(leg, joint, angles[joint], offset, pwm_left, pwm_right);
    }
  }
}
