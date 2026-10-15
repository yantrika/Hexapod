# PiHexa - Single Leg Controller (Arduino Mega 2560)

Control and test a single 3-DOF hexapod leg using an **Arduino Mega 2560** connected to your PC over USB, featuring a full **Web Remote Control & Kinematics Dashboard**.

---

## 🔌 1. Hardware Wiring (PCA9685 + Arduino Mega 2560)

### I2C Connections (Arduino Mega to PCA9685):
| Arduino Mega 2560 | PCA9685 Pin | Description |
| :--- | :--- | :--- |
| **Pin 20 (SDA)** | **SDA** | I2C Data line |
| **Pin 21 (SCL)** | **SCL** | I2C Clock line |
| **5V** | **VCC** | Logic power (powers the PCA9685 chip) |
| **GND** | **GND** | Common Ground |

### Servo Connections on PCA9685:
| PCA9685 Channel | Leg Joint | Function |
| :--- | :--- | :--- |
| **Channel 0** | Joint 1 (Coxa) | Shoulder Yaw servo (horizontal) |
| **Channel 1** | Joint 2 (Femur) | Thigh Pitch servo (vertical) |
| **Channel 2** | Joint 3 (Tibia) | Knee Pitch servo (vertical) |

> ⚠️ **Important**: Connect your external 5V (2A+) power supply directly to the **PCA9685 green screw terminal (V+ and GND)** to power the servos.

---

## ⚡ 2. Upload Sketch to Arduino Mega 2560

1. Connect your Arduino Mega 2560 to your PC via USB cable.
2. Open [`arduino_sketch/arduino_sketch.ino`](arduino_sketch/arduino_sketch.ino) in the Arduino IDE.
3. Select **Board: Arduino Mega or Mega 2560** and your **COM Port**.
4. Click **Upload**.

---

## 💻 3. Start the Web Interface

In your terminal or command prompt:

```bash
cd arduino_single_leg
python server.py
```

Then open your browser to:
👉 **[http://localhost:5000](http://localhost:5000)**

---

## 🎮 4. How to Use

1. **Connect USB**: In the top-right bar, select your Arduino's **COM port** and click **Connect**.
2. **Select Leg Position**: Click any of the 6 legs on the visual hexapod diagram (Leg 0 to Leg 5). The system automatically recalculates the mounting angle transforms ($+45^\circ$, $0^\circ$, $-45^\circ$, $-135^\circ$, $180^\circ$, $+135^\circ$) and tripod cycle phase.
3. **Start Walking**: Choose a mode (`Forward`, `Backward`, `Turn Left`, `Shift`, `Climb`, etc.) and click **Start Walking**.
4. **Manual Sliders**: Move the individual joint sliders (Coxa, Femur, Tibia) to verify mechanical range of motion or test individual servos.
