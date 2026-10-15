/*
 * Simple Motor Test Sketch (Arduino Mega 2560)
 *
 * WHAT IT DOES:
 * 1. Automatically sweeps the motor smoothly back and forth (45° <-> 135°).
 * 2. OR: Type any angle (0 to 180) in the Serial Monitor (115200 baud) to move to that exact angle!
 *
 * WIRING:
 * - Signal (Yellow/Orange) -> Pin 9 (or change TEST_PIN to 10 or 11)
 * - Power (Red)            -> External 5V Power Supply
 * - Ground (Brown/Black)   -> External Power GND AND Arduino GND
 */

#include <Servo.h>

const int TEST_PIN = 9; // Change to 10 or 11 to test other motors

// Pulse limits matching PiHexa servos
const int PULSE_MIN = 544;
const int PULSE_MAX = 2400;

Servo testServo;

bool autoSweep = true;
int currentAngle = 90;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  while (!Serial) { ; }

  testServo.attach(TEST_PIN, PULSE_MIN, PULSE_MAX);

  // Start at safe center (90 deg)
  testServo.write(90);

  Serial.println(F("================================================"));
  Serial.println(F("           SERVO MOTOR TEST SKETCH              "));
  Serial.println(F("================================================"));
  Serial.print(F("Testing on Pin: "));
  Serial.println(TEST_PIN);
  Serial.println(F("Mode: Auto-sweep (45 deg to 135 deg)."));
  Serial.println(F("TIP: Type any angle (0 - 180) in Serial Monitor"));
  Serial.println(F("     to test manual positions!"));
  Serial.println(F("================================================"));

  delay(1000);
}

void loop() {
  // Check if user typed a manual angle in Serial Monitor
  if (Serial.available() > 0) {
    int target = Serial.parseInt();
    // Flush extra characters like \n or \r
    while (Serial.available() > 0) { Serial.read(); }

    if (target >= 0 && target <= 180) {
      autoSweep = false; // Pause auto-sweep when manual input is received
      currentAngle = target;
      testServo.write(currentAngle);

      Serial.print(F(">> Manual Move -> "));
      Serial.print(currentAngle);
      Serial.println(F(" deg"));
      Serial.println(F("(Type -1 to resume auto-sweep)"));
      digitalWrite(LED_BUILTIN, HIGH);
    } else if (target == -1) {
      autoSweep = true;
      Serial.println(F(">> Resumed Auto-Sweep mode!"));
    }
  }

  // Smooth back-and-forth sweep
  if (autoSweep) {
    // Sweep from 45 to 135 degrees smoothly
    for (int pos = 45; pos <= 135; pos += 1) {
      if (Serial.available() > 0) break; // Check for manual input
      testServo.write(pos);
      digitalWrite(LED_BUILTIN, (pos % 20 < 10) ? HIGH : LOW);
      delay(15); // Smooth speed
    }

    delay(300); // Pause at end

    // Sweep back from 135 to 45 degrees
    for (int pos = 135; pos >= 45; pos -= 1) {
      if (Serial.available() > 0) break;
      testServo.write(pos);
      digitalWrite(LED_BUILTIN, (pos % 20 < 10) ? HIGH : LOW);
      delay(15);
    }

    delay(300); // Pause at end
  }
}
