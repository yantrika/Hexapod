/*
 * ============================================================
 * Center Tool: Motor 2 - Femur (Thigh Pitch)
 * Motor: Tower Pro MG995
 *
 * CONNECTIONS (Supports BOTH methods simultaneously):
 *
 * Method A (Direct Arduino Pin):
 *   - Servo Signal -> Pin 10 on Arduino Mega
 *   - Servo V+     -> External 5V-6V Power (+)
 *   - Servo GND    -> Common Ground (-) connected to Arduino GND
 *
 * Method B (Via PCA9685 I2C Driver at 0x40):
 *   - Arduino Pin 20 (SDA) -> PCA9685 SDA
 *   - Arduino Pin 21 (SCL) -> PCA9685 SCL
 *   - Arduino 5V           -> PCA9685 VCC (Logic Power)
 *   - Arduino GND          -> PCA9685 GND (Common Ground)
 *   - Servo Signal         -> PCA9685 Channel 1 (or Channel 0)
 *   - External 5V-6V (2A+) -> PCA9685 V+ Screw Terminal
 *
 * HOW TO USE:
 * 1. Upload this sketch to your Arduino Mega.
 * 2. Power on the servo (using external 5V/6V power).
 * 3. The motor will lock firmly at true neutral (1500 us / 90 deg).
 * 4. While powered, attach the thigh piece COMPLETELY FLAT & HORIZONTAL.
 * 5. Tighten the center screw.
 * ============================================================
 */

#include <Wire.h>
#include <Servo.h>

const int SERVO_PIN = 10;
const uint8_t PCA9685_ADDR = 0x40;
const uint8_t CH_FEMUR = 1;

// Tower Pro MG995 specifications
const int PULSE_MIN = 1000;
const int PULSE_MAX = 2000;
const int PULSE_MID = 1500; // True 90-degree center for MG995 (1500 us)

Servo servo;
bool pcaDetected = false;

// --- Built-in PCA9685 helper functions (no extra library needed) ---
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

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);

  // 1. Direct Pin output
  servo.attach(SERVO_PIN, PULSE_MIN, PULSE_MAX);
  servo.writeMicroseconds(PULSE_MID);

  // 2. PCA9685 I2C output (if connected)
  Wire.begin();
  Wire.beginTransmission(PCA9685_ADDR);
  if (Wire.endTransmission() == 0) {
    pcaDetected = true;
    pcaInit(50);
    pcaSetPulse(0, PULSE_MID);
    pcaSetPulse(CH_FEMUR, PULSE_MID);
    pcaSetPulse(2, PULSE_MID);
  }

  Serial.println(F("============================================="));
  Serial.println(F("  MOTOR 2: FEMUR (THIGH) CENTER TOOL         "));
  Serial.println(F("  Motor: Tower Pro MG995                     "));
  Serial.println(F("  Direct Pin: 10 | PCA9685: Channel 1        "));
  Serial.println(F("  Position: LOCKED AT CENTER (1500 us / 90 deg)"));
  if (pcaDetected) {
    Serial.println(F("  PCA9685: DETECTED & ACTIVE (0x40)          "));
  } else {
    Serial.println(F("  PCA9685: Not detected (Direct Pin 10 active)"));
  }
  Serial.println(F("============================================="));
  Serial.println(F("ACTION: Attach the thigh piece COMPLETELY FLAT & HORIZONTAL"));
}

void loop() {
  // Hold center position firmly
  servo.writeMicroseconds(PULSE_MID);
  if (pcaDetected) {
    pcaSetPulse(CH_FEMUR, PULSE_MID);
    pcaSetPulse(0, PULSE_MID);
  }

  // Blink LED to indicate active hold
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
  digitalWrite(LED_BUILTIN, LOW);
  delay(500);
}
