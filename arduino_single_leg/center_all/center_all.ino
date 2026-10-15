/*
 * ==============================================================================
 *  PiHexa18 - ALL-IN-ONE JOINT CENTERING & ALIGNMENT TOOL
 *  Motors: Tower Pro MG995 (3x: Coxa, Femur, Tibia)
 *
 *  WHAT THIS SKETCH DOES:
 *  Simultaneously sets and holds all 3 leg motors (Coxa, Femur, Tibia) at their
 *  EXACT neutral center position (1500 us / 90 deg) so you can attach the mechanical
 *  horns and leg brackets at the perfect angle in one go.
 *
 *  WIRING CONNECTIONS (Supports BOTH methods simultaneously):
 *  ---------------------------------------------------------
 *  METHOD A: Using PCA9685 I2C Driver (Recommended)
 *    - Arduino Mega Pin 20 (SDA) -> PCA9685 SDA
 *    - Arduino Mega Pin 21 (SCL) -> PCA9685 SCL
 *    - Arduino 5V                -> PCA9685 VCC (Logic)
 *    - Arduino GND               -> PCA9685 GND (Common Ground)
 *    - External 5V-6V (2A+)      -> PCA9685 V+ Screw Terminal
 *    - Motor 1 (Coxa / Shoulder) -> PCA9685 Channel 0
 *    - Motor 2 (Femur / Thigh)   -> PCA9685 Channel 1
 *    - Motor 3 (Tibia / Knee)    -> PCA9685 Channel 2
 *
 *  METHOD B: Direct to Arduino Mega Pins (If not using PCA9685)
 *    - Motor 1 (Coxa / Shoulder) Signal -> Pin 9
 *    - Motor 2 (Femur / Thigh)   Signal -> Pin 10
 *    - Motor 3 (Tibia / Knee)    Signal -> Pin 11
 *    - External 5V-6V Power (+)         -> Servo Red Wires
 *    - External Power GND (-)           -> Servo Brown/Black Wires & Arduino GND
 *
 *  ALIGNMENT TARGETS (Attach while powered & locked):
 *  --------------------------------------------------
 *  1. COXA  (Shoulder): Attach horn pointing STRAIGHT OUTWARD along the leg axis.
 *  2. FEMUR (Thigh)   : Attach thigh piece COMPLETELY FLAT & HORIZONTAL.
 *  3. TIBIA (Knee/Shin): Attach shin piece pointing STRAIGHT DOWN (90 deg to thigh).
 * ==============================================================================
 */

#include <Wire.h>
#include <Servo.h>

// --- DIRECT ARDUINO PIN ASSIGNMENTS ---
const int PIN_COXA  = 9;
const int PIN_FEMUR = 10;
const int PIN_TIBIA = 11;

// --- PCA9685 I2C ASSIGNMENTS ---
const uint8_t PCA9685_ADDR = 0x40;
const uint8_t CH_COXA  = 0;
const uint8_t CH_FEMUR = 1;
const uint8_t CH_TIBIA = 2;

// --- TOWER PRO MG995 PULSE SPECIFICATIONS ---
const int PULSE_MIN = 1000;
const int PULSE_MAX = 2000;
const int PULSE_MID = 1500; // Exact 90 deg neutral center (1500 us)

// Servo instances for direct pin driving
Servo servoCoxa;
Servo servoFemur;
Servo servoTibia;

bool pcaDetected = false;

// --- Built-in PCA9685 Driver (No extra libraries needed) ---
void pcaWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

uint8_t pcaRead(uint8_t reg) {
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(PCA9685_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0;
}

void pcaInit(uint8_t freq = 50) {
  pcaWrite(0x00, 0x00);
  float prescaleval = 25000000.0 / 4096.0 / (float)freq - 1.0;
  uint8_t prescale = (uint8_t)floor(prescaleval + 0.5);
  uint8_t oldmode = pcaRead(0x00);
  uint8_t newmode = (oldmode & 0x7F) | 0x10;
  pcaWrite(0x00, newmode);
  pcaWrite(0xFE, prescale);
  pcaWrite(0x00, oldmode);
  delay(5);
  pcaWrite(0x00, oldmode | 0xA0);
}

void pcaSetPulse(uint8_t channel, int pulseUs) {
  pulseUs = constrain(pulseUs, PULSE_MIN, PULSE_MAX);
  uint16_t off = (uint16_t)(((uint32_t)pulseUs * 4096UL) / 20000UL);
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(0x06 + 4 * channel);
  Wire.write(0);
  Wire.write(0);
  Wire.write(off & 0xFF);
  Wire.write(off >> 8);
  Wire.endTransmission();
}

// Write pulse to both Direct Pin and PCA9685 channel simultaneously
void setJointPulse(int joint, int pulseUs) {
  pulseUs = constrain(pulseUs, PULSE_MIN, PULSE_MAX);
  switch (joint) {
    case 0: // Coxa
      servoCoxa.writeMicroseconds(pulseUs);
      if (pcaDetected) pcaSetPulse(CH_COXA, pulseUs);
      break;
    case 1: // Femur
      servoFemur.writeMicroseconds(pulseUs);
      if (pcaDetected) pcaSetPulse(CH_FEMUR, pulseUs);
      break;
    case 2: // Tibia
      servoTibia.writeMicroseconds(pulseUs);
      if (pcaDetected) pcaSetPulse(CH_TIBIA, pulseUs);
      break;
  }
}

// Lock all 3 joints to neutral center
void lockAllToCenter() {
  setJointPulse(0, PULSE_MID);
  setJointPulse(1, PULSE_MID);
  setJointPulse(2, PULSE_MID);
}

// Gentle test wiggle to identify a specific joint
void wiggleJoint(int joint) {
  Serial.print(F("Identifying Motor "));
  Serial.println(joint == 0 ? F("1 (COXA)") : (joint == 1 ? F("2 (FEMUR)") : F("3 (TIBIA)")));
  for (int w = 0; w < 2; w++) {
    setJointPulse(joint, PULSE_MID + 150);
    delay(250);
    setJointPulse(joint, PULSE_MID - 150);
    delay(250);
  }
  setJointPulse(joint, PULSE_MID);
  Serial.println(F("Returned to LOCKED Center."));
}

void printInstructions() {
  Serial.println(F("\n=========================================================="));
  Serial.println(F("       PiHexa18: ALL-IN-ONE JOINT CENTERING TOOL          "));
  Serial.println(F("       Motor Type: Tower Pro MG995 (1500 us Center)       "));
  Serial.println(F("=========================================================="));
  Serial.println(F("STATUS: ALL 3 MOTORS ACTIVELY LOCKED AT 1500 us (CENTER)\n"));
  Serial.println(F("ATTACHMENT INSTRUCTIONS:"));
  Serial.println(F("  [1] MOTOR 1 (COXA / Pin 9 / Ch 0):"));
  Serial.println(F("      -> Attach horn pointing STRAIGHT OUTWARD (radial to body)"));
  Serial.println(F("  [2] MOTOR 2 (FEMUR / Pin 10 / Ch 1):"));
  Serial.println(F("      -> Attach thigh piece COMPLETELY FLAT & HORIZONTAL"));
  Serial.println(F("  [3] MOTOR 3 (TIBIA / Pin 11 / Ch 2):"));
  Serial.println(F("      -> Attach knee/shin pointing STRAIGHT DOWN (90 deg angle)\n"));
  Serial.println(F("SERIAL COMMANDS (Optional):"));
  Serial.println(F("  '0' or 'C' -> Re-lock all 3 joints to Center"));
  Serial.println(F("  '1'        -> Wiggle Coxa (identify motor 1)"));
  Serial.println(F("  '2'        -> Wiggle Femur (identify motor 2)"));
  Serial.println(F("  '3'        -> Wiggle Tibia (identify motor 3)"));
  Serial.println(F("  'T'        -> Gentle test sweep of all 3 joints"));
  Serial.println(F("==========================================================\n"));
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);

  // 1. Attach direct Arduino pins
  servoCoxa.attach(PIN_COXA, PULSE_MIN, PULSE_MAX);
  servoFemur.attach(PIN_FEMUR, PULSE_MIN, PULSE_MAX);
  servoTibia.attach(PIN_TIBIA, PULSE_MIN, PULSE_MAX);

  // 2. Initialize PCA9685 I2C if present
  Wire.begin();
  Wire.beginTransmission(PCA9685_ADDR);
  if (Wire.endTransmission() == 0) {
    pcaDetected = true;
    pcaInit(50);
  }

  // 3. Lock all 3 motors to Center immediately
  lockAllToCenter();

  printInstructions();
  if (pcaDetected) {
    Serial.println(F("[i] PCA9685 I2C detected on 0x40. Channels 0, 1, 2 ACTIVE."));
  } else {
    Serial.println(F("[i] PCA9685 not detected. Arduino direct Pins 9, 10, 11 ACTIVE."));
  }
}

void loop() {
  // Continuously enforce holding the center position firmly
  lockAllToCenter();

  // Check for serial commands
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    while (Serial.available()) Serial.read(); // flush remaining chars

    if (cmd == '0' || cmd == 'c' || cmd == 'C') {
      lockAllToCenter();
      Serial.println(F(">> All joints locked to CENTER (1500 us)."));
    } else if (cmd == '1') {
      wiggleJoint(0);
    } else if (cmd == '2') {
      wiggleJoint(1);
    } else if (cmd == '3') {
      wiggleJoint(2);
    } else if (cmd == 't' || cmd == 'T') {
      Serial.println(F(">> Gentle test motion..."));
      for (int a = -20; a <= 20; a += 5) {
        int us = PULSE_MID + (int)(a * 5.55f);
        setJointPulse(0, us);
        setJointPulse(1, us);
        setJointPulse(2, us);
        delay(30);
      }
      for (int a = 20; a >= -20; a -= 5) {
        int us = PULSE_MID + (int)(a * 5.55f);
        setJointPulse(0, us);
        setJointPulse(1, us);
        setJointPulse(2, us);
        delay(30);
      }
      lockAllToCenter();
      Serial.println(F(">> Done. Returned to LOCKED CENTER."));
    }
  }

  // Heartbeat LED blink
  digitalWrite(LED_BUILTIN, HIGH);
  delay(300);
  digitalWrite(LED_BUILTIN, LOW);
  delay(300);
}
