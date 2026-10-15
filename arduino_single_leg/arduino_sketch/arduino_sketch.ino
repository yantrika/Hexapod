/*
 * PiHexa18 - Arduino Mega 2560 Single Leg Controller (PCA9685 I2C Version)
 * 
 * Controls 3 servos for a single hexapod leg via PCA9685 16-Channel PWM Driver:
 *   - I2C Address: 0x40
 *   - Arduino Mega Pin 20 (SDA) -> PCA9685 SDA
 *   - Arduino Mega Pin 21 (SCL) -> PCA9685 SCL
 *   - Arduino Mega 5V           -> PCA9685 VCC (Logic Power)
 *   - Arduino Mega GND          -> PCA9685 GND (Common Ground)
 *   - External 5V Power (2A+)   -> PCA9685 V+ (Screw Terminal)
 *
 * PCA9685 Servo Channels:
 *   - Channel 0: Coxa  (Joint 1 - Shoulder Yaw)
 *   - Channel 1: Femur (Joint 2 - Thigh Pitch)
 *   - Channel 2: Tibia (Joint 3 - Knee Pitch)
 *
 * Receives Serial commands from Python Web Bridge:
 *   - "A <coxa_deg> <femur_deg> <tibia_deg>\n" -> Move joints to target angles (-90 to +90 deg)
 *   - "P <coxa_us> <femur_us> <tibia_us>\n"   -> Move joints by direct microsecond pulse
 *   - "PING\n"                                -> Responds with "PONG"
 */

#include <Wire.h>

// --- PCA9685 CONFIGURATION ---
const uint8_t PCA9685_ADDR = 0x40; // Default I2C address

// Servo Channels on PCA9685
const uint8_t CH_COXA  = 0;
const uint8_t CH_FEMUR = 1;
const uint8_t CH_TIBIA = 2;

// --- SERVO PULSE LIMITS (in microseconds) ---
// Configured for Tower Pro MG995 (1000us - 2000us, Center = 1500us)
const int PULSE_MIN = 1000;
const int PULSE_MAX = 2000;
const int PULSE_MID = 1500; // 1500 us = true 90 deg center for MG995

// Joint angle offsets (trim adjustment)
float offsetCoxa  = 0.0;
float offsetFemur = 0.0;
float offsetTibia = 0.0;

// --- NATIVE I2C DRIVER FOR PCA9685 (No external library needed) ---
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
  pcaWrite(0x00, 0x00); // MODE1: normal mode
  
  // Calculate prescale for 50Hz (20ms period)
  float prescaleval = 25000000.0 / 4096.0 / (float)freq - 1.0;
  uint8_t prescale = (uint8_t)floor(prescaleval + 0.5);

  uint8_t oldmode = pcaRead(0x00);
  uint8_t newmode = (oldmode & 0x7F) | 0x10; // Sleep mode to update prescaler
  pcaWrite(0x00, newmode);
  pcaWrite(0xFE, prescale);                  // Set PRESCALE reg
  pcaWrite(0x00, oldmode);
  delay(5);
  pcaWrite(0x00, oldmode | 0xA0);            // Auto-increment enable
}

void pcaSetPWM(uint8_t channel, uint16_t on, uint16_t off) {
  Wire.beginTransmission(PCA9685_ADDR);
  Wire.write(0x06 + 4 * channel); // LEDn_ON_L
  Wire.write(on & 0xFF);
  Wire.write(on >> 8);
  Wire.write(off & 0xFF);
  Wire.write(off >> 8);
  Wire.endTransmission();
}

void pcaSetPulse(uint8_t channel, int pulseUs) {
  pulseUs = constrain(pulseUs, PULSE_MIN, PULSE_MAX);
  // At 50Hz, period = 20,000 us (4096 ticks)
  uint16_t off = (uint16_t)(((uint32_t)pulseUs * 4096UL) / 20000UL);
  pcaSetPWM(channel, 0, off);
}

// Convert kinematic angle (-90 to +90 deg) to servo pulse (us)
int angleToPulse(float kmAngle, float offset, bool invert) {
  float corrected = kmAngle + offset;
  if (invert) {
    corrected = -corrected;
  }
  // Clamp between -90 and 90 degrees
  if (corrected < -90.0) corrected = -90.0;
  if (corrected > 90.0)  corrected = 90.0;
  
  float us = PULSE_MID + corrected * ((PULSE_MAX - PULSE_MIN) / 180.0);
  return constrain((int)us, PULSE_MIN, PULSE_MAX);
}

void setLegAngles(float coxa, float femur, float tibia) {
  // In PiHexa kinematics: Femur is inverted relative to standard orientation
  int pulseC = angleToPulse(coxa,  offsetCoxa,  false);
  int pulseF = angleToPulse(femur, offsetFemur, true);
  int pulseT = angleToPulse(tibia, offsetTibia, false);

  pcaSetPulse(CH_COXA,  pulseC);
  pcaSetPulse(CH_FEMUR, pulseF);
  pcaSetPulse(CH_TIBIA, pulseT);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  Serial.begin(115200);
  while (!Serial) { ; } // Wait for serial port

  // Initialize I2C bus and PCA9685
  Wire.begin();
  Wire.setClock(400000); // 400kHz Fast I2C
  pcaInit(50);           // 50Hz for servos

  // Move all 3 joints to neutral mechanical center (0 deg)
  setLegAngles(0, 0, 0);

  // Blink LED 3 times to confirm successful boot
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(100);
    digitalWrite(LED_BUILTIN, LOW);
    delay(100);
  }

  Serial.println(F("READY: PiHexa Single Leg Controller (PCA9685) Initialized on Arduino Mega 2560"));
}

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  // Toggle onboard LED on every command to show activity
  digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));

  if (cmd.equalsIgnoreCase("PING")) {
    Serial.println(F("PONG"));
    return;
  }

  // Angle Command: "A <coxa> <femur> <tibia>"
  if (cmd.startsWith("A ") || cmd.startsWith("A\t")) {
    int s1 = cmd.indexOf(' ');
    int s2 = (s1 != -1) ? cmd.indexOf(' ', s1 + 1) : -1;
    int s3 = (s2 != -1) ? cmd.indexOf(' ', s2 + 1) : -1;
    if (s1 != -1 && s2 != -1 && s3 != -1) {
      float coxa  = cmd.substring(s1 + 1, s2).toFloat();
      float femur = cmd.substring(s2 + 1, s3).toFloat();
      float tibia = cmd.substring(s3 + 1).toFloat();

      setLegAngles(coxa, femur, tibia);

      Serial.print(F("OK: "));
      Serial.print(coxa, 2); Serial.print(" ");
      Serial.print(femur, 2); Serial.print(" ");
      Serial.println(tibia, 2);
      return;
    } else {
      Serial.println(F("ERR: Format: A <coxa> <femur> <tibia>"));
      return;
    }
  }

  // Offset Trim Command: "OFFSET <coxa> <femur> <tibia>"
  if (cmd.startsWith("OFFSET ")) {
    int s1 = cmd.indexOf(' ');
    int s2 = (s1 != -1) ? cmd.indexOf(' ', s1 + 1) : -1;
    int s3 = (s2 != -1) ? cmd.indexOf(' ', s2 + 1) : -1;
    if (s1 != -1 && s2 != -1 && s3 != -1) {
      offsetCoxa  = cmd.substring(s1 + 1, s2).toFloat();
      offsetFemur = cmd.substring(s2 + 1, s3).toFloat();
      offsetTibia = cmd.substring(s3 + 1).toFloat();
      Serial.println(F("OK: Offsets updated"));
      return;
    }
  }

  // Direct Microsecond Command: "P <pulseC> <pulseF> <pulseT>"
  if (cmd.startsWith("P ")) {
    int pC, pF, pT;
    if (sscanf(cmd.c_str(), "P %d %d %d", &pC, &pF, &pT) == 3) {
      pcaSetPulse(CH_COXA,  pC);
      pcaSetPulse(CH_FEMUR, pF);
      pcaSetPulse(CH_TIBIA, pT);
      Serial.println(F("OK: Pulse set"));
      return;
    }
  }

  Serial.println(F("ERR: Unknown command"));
}

void loop() {
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    processCommand(line);
  }
}
