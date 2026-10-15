/*
 * ============================================================
 *  PiHexa18 - Arduino Full Hexapod Controller
 *  Motor: Tower Pro MG995 (6x for front 2 legs / 18x for full)
 *  PWM Driver: PCA9685 (I2C addr: 0x40)
 *  Board: Arduino Mega 2560 or Uno
 *
 *  CURRENT CONFIGURATION (FRONT 2 LEGS ONLY):
 *    Single PCA9685 at 0x40:
 *      Front-Left  (Leg 5): Ch 0 (Coxa), Ch 1 (Femur), Ch 2 (Tibia)
 *      Front-Right (Leg 0): Ch 3 (Coxa), Ch 4 (Femur), Ch 5 (Tibia)
 *    (Switch FRONT_LEGS_ONLY to false in Config.h for full 6 legs)
 *
 *  Modes (Serial Monitor @ 115200 baud):
 *    0  - Standby (Default stance)
 *    1  - Forward (Alternating leg stepping)
 *    2  - Run (Fast Forward)
 *    3  - Backward
 *    4  - Turn Left
 *    5  - Turn Right
 *    13 - Wave Test ('W')
 *    14 - Center All Joints ('C' - 90 deg / 1500us neutral)
 *
 *  Library required: Adafruit PWM Servo Driver Library
 * ============================================================
 */

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include "Config.h"
#include "Kinematics.h"
#include "Gait.h"
#include "ServoControl.h"

// ── PCA9685 objects ────────────────────────────────────────
Adafruit_PWMServoDriver pwm_left  = Adafruit_PWMServoDriver(0x40);
Adafruit_PWMServoDriver pwm_right = Adafruit_PWMServoDriver(0x41);

// ── Timing ─────────────────────────────────────────────────
unsigned long lastTime   = 0;
unsigned long elapsed    = 0;

// ── Movement state ─────────────────────────────────────────
int  currentMode    = MODE_STANDBY;
bool modeChanged    = false;

// ── Gait state ─────────────────────────────────────────────
GaitState gait;

// ── Servo calibration offsets [leg][joint] ─────────────────
// Adjust these after physical assembly (in degrees, ±)
float offset[6][3] = {
  {0, 0, 0},  // leg 0
  {0, 0, 0},  // leg 1
  {0, 0, 0},  // leg 2
  {0, 0, 0},  // leg 3
  {0, 0, 0},  // leg 4
  {0, 0, 0}   // leg 5
};

bool hasLeftPCA  = false;
bool hasRightPCA = false;

// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Wire.begin();
  #if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(25000, true);
  #endif

#if FRONT_LEGS_ONLY
  // ── Single PCA9685 (0x40) for Front 2 Legs ────────────────
  Wire.beginTransmission(0x40);
  if (Wire.endTransmission() == 0) {
    hasLeftPCA = true;
    pwm_left.begin();
    pwm_left.setOscillatorFrequency(27000000);
    pwm_left.setPWMFreq(SERVO_FREQ);
    Serial.println(F("[OK] PCA9685 (0x40) detected. Front 2 legs active!"));
  } else {
    Serial.println(F("[!] ERROR: PCA9685 (0x40) NOT detected! Check SDA/SCL, 5V, GND."));
  }
#else
  // ── Dual PCA9685 (0x40 & 0x41) for All 6 Legs ──────────────
  Wire.beginTransmission(0x40);
  if (Wire.endTransmission() == 0) {
    hasLeftPCA = true;
    pwm_left.begin();
    pwm_left.setOscillatorFrequency(27000000);
    pwm_left.setPWMFreq(SERVO_FREQ);
    Serial.println(F("[OK] Left PCA9685 (0x40) detected. Legs 3, 4, 5 active."));
  } else {
    Serial.println(F("[!] Left PCA9685 (0x40) NOT detected."));
  }

  Wire.beginTransmission(0x41);
  if (Wire.endTransmission() == 0) {
    hasRightPCA = true;
    pwm_right.begin();
    pwm_right.setOscillatorFrequency(27000000);
    pwm_right.setPWMFreq(SERVO_FREQ);
    Serial.println(F("[OK] Right PCA9685 (0x41) detected. Legs 0, 1, 2 active."));
  } else {
    Serial.println(F("[i] Right PCA9685 (0x41) not detected (Bridge A0 jumper if using 2 boards)."));
  }
#endif

  delay(100);

  // Init gait state
  gaitInit(&gait, currentMode);

  // Move active legs to initial position
  updateLegs(&gait, offset, hasLeftPCA ? &pwm_left : NULL, hasRightPCA ? &pwm_right : NULL);

  lastTime = millis();
  Serial.println(F("\n=================================================="));
#if FRONT_LEGS_ONLY
  Serial.println(F("    PiHexa18: FRONT 2 LEGS CONTROLLER (TEST MODE) "));
  Serial.println(F("=================================================="));
  Serial.println(F("Board: Single PCA9685 (0x40)"));
  Serial.println(F("  Front-Left  (Leg 5): Coxa=Ch0, Femur=Ch1, Tibia=Ch2"));
  Serial.println(F("  Front-Right (Leg 0): Coxa=Ch3, Femur=Ch4, Tibia=Ch5"));
  Serial.println(F("=================================================="));
  Serial.println(F("Commands (Serial Monitor @ 115200 baud):"));
  Serial.println(F("  0  : Standby (Neutral Stance)"));
  Serial.println(F("  1  : Walk Forward (Alternating Stepping)"));
  Serial.println(F("  2  : Fast Walk"));
  Serial.println(F("  3  : Walk Backward"));
  Serial.println(F("  4  : Turn Left  | 5 : Turn Right"));
  Serial.println(F("  13 : Wave Test (Lifts front leg and waves)"));
  Serial.println(F("  14 : Center ('C' - Hold all 6 joints at 90 deg / 1500us)"));
#else
  Serial.println(F("     PiHexa Full Hexapod Controller Ready!        "));
  Serial.println(F("=================================================="));
  Serial.println(F("Type mode number (0 to 14) in Serial Monitor:"));
  Serial.println(F("  0: Standby | 1: Forward | 2: Run | 3: Backward"));
  Serial.println(F("  4: Turn L  | 5: Turn R  | 6: Shift L | 7: Shift R"));
#endif
  Serial.println(F("==================================================\n"));
}

// ─────────────────────────────────────────────────────────────
void loop() {
  // ── Read serial command ──────────────────────────────────
  if (Serial.available()) {
    int newMode = -1;
    char c = Serial.peek();

    if (c == 'c' || c == 'C') {
      Serial.read();
      newMode = MODE_CENTER;
    } else if (c == 'w' || c == 'W') {
      Serial.read();
      newMode = MODE_WAVE;
    } else if (c >= '0' && c <= '9') {
      newMode = Serial.parseInt();
    } else {
      Serial.read(); // discard whitespace
    }

    // Flush trailing newlines
    while (Serial.available() && (Serial.peek() == '\r' || Serial.peek() == '\n' || Serial.peek() == ' ')) {
      Serial.read();
    }

    if (newMode >= 0 && newMode <= 14 && newMode != currentMode) {
      currentMode = newMode;
      modeChanged = true;
      Serial.print(F(">> Mode changed to: "));
      Serial.print(currentMode);
      switch(currentMode) {
        case MODE_STANDBY:      Serial.println(F(" (STANDBY)")); break;
        case MODE_FORWARD:      Serial.println(F(" (FORWARD)")); break;
        case MODE_FORWARD_FAST: Serial.println(F(" (FAST FORWARD)")); break;
        case MODE_BACKWARD:     Serial.println(F(" (BACKWARD)")); break;
        case MODE_TURN_LEFT:    Serial.println(F(" (TURN LEFT)")); break;
        case MODE_TURN_RIGHT:   Serial.println(F(" (TURN RIGHT)")); break;
        case MODE_WAVE:         Serial.println(F(" (WAVE TEST)")); break;
        case MODE_CENTER:       Serial.println(F(" (CENTER ALL JOINTS)")); break;
        default:                Serial.println(); break;
      }
    }
  }

  // ── Compute elapsed time ─────────────────────────────────
  unsigned long now = millis();
  elapsed = now - lastTime;
  lastTime = now;

  // ── Step gait ────────────────────────────────────────────
  gaitStep(&gait, currentMode, (float)elapsed, modeChanged);
  modeChanged = false;

  // ── Drive servos ─────────────────────────────────────────
  updateLegs(&gait, offset, hasLeftPCA ? &pwm_left : NULL, hasRightPCA ? &pwm_right : NULL);

  delay(MOVEMENT_INTERVAL_MS);
}
