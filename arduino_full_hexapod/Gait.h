#pragma once
#include "Config.h"
#include "Kinematics.h"

// ============================================================
//  Gait.h — Pre-computed leg tip waypoints + gait stepping
//  Mirrors pihexa/movement.py and the movement_table paths
//
//  Each gait has N waypoints per leg (Vec3 world-frame positions).
//  The state machine linearly interpolates between them.
// ============================================================

// ── Standby position per leg (world frame) ──────────────────
// Derived from config.py: standby_z, left_right_x, other_x, other_y
#define SZ    67.06f   // standby_z  (leg_J3_TO_TIP*cos15 - leg_J2_TO_J3*sin30)
#define LRX  157.11f   // left_right_x
#define OX   119.30f   // other_x
#define OY   161.80f   // other_y

// Standby tip positions [6 legs]
static const Vec3 STANDBY[6] = {
  Vec3( OX,  OY, -SZ),   // leg 0
  Vec3(LRX,   0, -SZ),   // leg 1
  Vec3( OX, -OY, -SZ),   // leg 2
  Vec3(-OX, -OY, -SZ),   // leg 3
  Vec3(-LRX,  0, -SZ),   // leg 4
  Vec3(-OX,  OY, -SZ)    // leg 5
};

// ── Step height (mm) above standby Z when lifting a leg ─────
#define STEP_H   30.0f

// ── Stride length (mm) — how far each step moves forward ─────
#define STRIDE   35.0f

// ── Tripod gait groups ──────────────────────────────────────
//  Group A: legs 0, 2, 4  (lift first in forward)
//  Group B: legs 1, 3, 5
#define GROUPA  0   // legs 0,2,4
#define GROUPB  1   // legs 1,3,5

// ── Gait state ───────────────────────────────────────────────
struct GaitState {
  Vec3  tipPos[6];    // current interpolated tip position per leg
  int   phase;        // 0 or 1 (which tripod is swinging)
  float phaseTime;    // how long we've been in this phase (ms)
  float phaseDur;     // total duration of one phase (ms)
  int   mode;         // current movement mode
};

// ── Which group does each leg belong to? ─────────────────────
inline int legGroup(int leg) {
  // legs 0,2,4 → group A; legs 1,3,5 → group B
  return (leg % 2 == 0) ? GROUPA : GROUPB;
}

// ── Compute target tip for each leg given mode & phase ───────
//  swingGroup = which group is currently swinging (lifting)
//  ratio      = 0.0 (start of phase) → 1.0 (end of phase)
inline Vec3 targetTip(int leg, int mode, int swingGroup, float ratio) {
  Vec3 base   = STANDBY[leg];
  bool swing  = (legGroup(leg) == swingGroup);

  // Special Test Mode: WAVE (lifts leg 0 and waves back and forth)
  if (mode == MODE_WAVE) {
    if (leg == 0) {
      float wave = sinf(ratio * 2.0f * M_PI);
      return Vec3(base.x + wave * 25.0f, base.y, base.z + 45.0f);
    } else {
      return base;
    }
  }

  // Direction vector per mode (applied in world frame, scaled by STRIDE)
  // PiHexa coordinate frame: +Y = Forward, -Y = Backward, +X = Right, -X = Left
  float dx = 0, dy = 0;
  switch(mode) {
    case MODE_FORWARD:      dy =  STRIDE; break;
    case MODE_FORWARD_FAST: dy =  STRIDE * 1.5f; break;
    case MODE_BACKWARD:     dy = -STRIDE; break;
    case MODE_TURN_LEFT:    dx = (leg < 3) ? -STRIDE :  STRIDE; break;
    case MODE_TURN_RIGHT:   dx = (leg < 3) ?  STRIDE : -STRIDE; break;
    case MODE_SHIFT_LEFT:   dx = -STRIDE; break;
    case MODE_SHIFT_RIGHT:  dx =  STRIDE; break;
    default:                dx = 0; dy = 0; break;
  }

  // Smooth continuous progression (-1.0 to +1.0)
  float progress = (ratio - 0.5f) * 2.0f;

  if (swing) {
    // Swing leg: lifts up in arc and steps from backward to forward
    float lift = sinf(ratio * M_PI) * STEP_H; // +lift raises foot from -SZ toward body
    return Vec3(base.x + dx * (progress * 0.5f),
                base.y + dy * (progress * 0.5f),
                base.z + lift);
  } else {
    // Stance leg: pushes from forward to backward on the ground
    return Vec3(base.x - dx * (progress * 0.5f),
                base.y - dy * (progress * 0.5f),
                base.z);
  }
}

// ── Init gait state ──────────────────────────────────────────
inline void gaitInit(GaitState* g, int mode) {
  for (int i = 0; i < 6; i++) {
    g->tipPos[i] = STANDBY[i];
  }
  g->phase     = 0;
  g->phaseTime = 0;
  g->phaseDur  = STEP_DURATION_MS * 10;   // ms for one full phase
  g->mode      = mode;
}

// ── Advance gait by elapsed ms ───────────────────────────────
inline void gaitStep(GaitState* g, int mode, float elapsed, bool modeChanged) {
  // On mode change, reset to standby smoothly
  if (modeChanged) {
    g->mode      = mode;
    g->phase     = 0;
    g->phaseTime = 0;
    if (mode == MODE_STANDBY) {
      for (int i = 0; i < 6; i++) g->tipPos[i] = STANDBY[i];
      return;
    }
  }

  if (mode == MODE_STANDBY) {
    for (int i = 0; i < 6; i++) g->tipPos[i] = STANDBY[i];
    return;
  }

  // Simple body rotation modes — oscillate body attitude
  if (mode == MODE_ROTATE_X || mode == MODE_ROTATE_Y ||
      mode == MODE_ROTATE_Z || mode == MODE_TWIST   ||
      mode == MODE_CLIMB) {
    // For these modes: just hold standby (safe default)
    // Advanced body-pose modes need IMU/sensor feedback.
    // They can be expanded later.
    for (int i = 0; i < 6; i++) g->tipPos[i] = STANDBY[i];
    return;
  }

  g->phaseTime += elapsed;

  if (g->phaseTime >= g->phaseDur) {
    // Advance to next phase (toggle tripod)
    g->phase     = 1 - g->phase;
    g->phaseTime = 0;
  }

  float ratio = g->phaseTime / g->phaseDur;
  ratio = constrain(ratio, 0.0f, 1.0f);

  for (int i = 0; i < 6; i++) {
    g->tipPos[i] = targetTip(i, mode, g->phase, ratio);
  }
}
