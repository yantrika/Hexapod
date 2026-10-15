#pragma once
#include <math.h>
#include "Config.h"

// ============================================================
//  Kinematics.h — Inverse kinematics for one hexapod leg
//  Mirrors pihexa/leg.py  BaseLeg.inverse_kinematics()
// ============================================================

struct Vec3 {
  float x, y, z;
  Vec3(float _x=0, float _y=0, float _z=0) : x(_x), y(_y), z(_z) {}
  Vec3 operator+(const Vec3& o) const { return Vec3(x+o.x, y+o.y, z+o.z); }
  Vec3 operator-(const Vec3& o) const { return Vec3(x-o.x, y-o.y, z-o.z); }
  Vec3 operator*(float s)        const { return Vec3(x*s,   y*s,   z*s);   }
  Vec3& operator+=(const Vec3& o){ x+=o.x; y+=o.y; z+=o.z; return *this; }
};

// ── Rotate a point by 'angleDeg' around Z axis ──────────────
inline Vec3 rotateZ(Vec3 p, float angleDeg) {
  float r = angleDeg * M_PI / 180.0f;
  return Vec3(p.x*cosf(r) - p.y*sinf(r),
              p.x*sinf(r) + p.y*cosf(r),
              p.z);
}

// ── Mount angles for each leg (degrees, matches leg.py) ─────
//  leg 0:+45  leg 1:0  leg 2:-45  leg 3:-135  leg 4:180  leg 5:135
static const float MOUNT_ANGLE[6] = {45.0f, 0.0f, -45.0f, -135.0f, 180.0f, 135.0f};

// ── Mount positions in world frame ──────────────────────────
inline Vec3 mountPos(int legIdx) {
  switch(legIdx) {
    case 0: return Vec3( MOUNT_OT_X,  MOUNT_OT_Y, 0);
    case 1: return Vec3( MOUNT_LR_X,  0,          0);
    case 2: return Vec3( MOUNT_OT_X, -MOUNT_OT_Y, 0);
    case 3: return Vec3(-MOUNT_OT_X, -MOUNT_OT_Y, 0);
    case 4: return Vec3(-MOUNT_LR_X,  0,          0);
    case 5: return Vec3(-MOUNT_OT_X,  MOUNT_OT_Y, 0);
    default: return Vec3(0,0,0);
  }
}

// ── Convert world tip position → local leg frame ─────────────
inline Vec3 world2local(int legIdx, Vec3 worldPt) {
  Vec3 mount = mountPos(legIdx);
  Vec3 rel   = worldPt - mount;
  // rotate by -mountAngle to get local frame
  return rotateZ(rel, -MOUNT_ANGLE[legIdx]);
}

// ── Inverse kinematics (local frame) ─────────────────────────
//  Returns angles[3] in degrees: [coxa, femur, tibia]
//  Mirrors BaseLeg.inverse_kinematics() in leg.py
inline void inverseKinematics(Vec3 localPt, float angles[3]) {
  float x = localPt.x - LEG_ROOT2JOINT1;
  float y = localPt.y;
  angles[0] = atan2f(y, x) * 180.0f / M_PI;

  x = sqrtf(x*x + y*y) - LEG_J1_TO_J2;
  float z = localPt.z;

  float ar   = atan2f(z, x);
  float lr2  = x*x + z*z;
  float lr   = sqrtf(lr2);

  float cosA1 = (lr2 + LEG_J2_TO_J3*LEG_J2_TO_J3 - LEG_J3_TO_TIP*LEG_J3_TO_TIP)
                / (2.0f * LEG_J2_TO_J3 * lr);
  float cosA2 = (lr2 - LEG_J2_TO_J3*LEG_J2_TO_J3 + LEG_J3_TO_TIP*LEG_J3_TO_TIP)
                / (2.0f * LEG_J3_TO_TIP * lr);

  // Clamp to avoid NaN from floating point drift
  cosA1 = constrain(cosA1, -1.0f, 1.0f);
  cosA2 = constrain(cosA2, -1.0f, 1.0f);

  float a1 = acosf(cosA1);
  float a2 = acosf(cosA2);

  angles[1] = (ar + a1) * 180.0f / M_PI;
  angles[2] = 90.0f - ((a1 + a2) * 180.0f / M_PI);
}

// ── Clamp angles to joint limits ─────────────────────────────
inline void clampAngles(float angles[3]) {
  angles[0] = constrain(angles[0], COXA_MIN,  COXA_MAX);
  angles[1] = constrain(angles[1], FEMUR_MIN, FEMUR_MAX);
  angles[2] = constrain(angles[2], TIBIA_MIN, TIBIA_MAX);
}
