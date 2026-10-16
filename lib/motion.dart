import 'package:flutter/foundation.dart';

import 'robot_link.dart';

/// Turns the on-screen controls into the robot's one move message.
///
/// Every control writes its own value; the move sent is their sum, clamped by the link. The
/// same object serves the Drive and Landscape screens, so switching between them keeps
/// whatever is held (and `reset` zeroes everything, e.g. when the layout changes).
class MotionInput extends ChangeNotifier {
  MotionInput(this.link);
  final RobotLink link;

  double stickX = 0, stickY = 0; // move stick: x right+, y forward+
  double turnStick = 0; // dual layout turn stick: right+
  double padForward = 0,
      padStrafe = 0,
      padYaw = 0; // held buttons: forward/left/turn-left +

  double get forward => (stickY + padForward).clamp(-1.0, 1.0);
  double get strafe => (-stickX + padStrafe).clamp(-1.0, 1.0);
  double get yaw => (-turnStick + padYaw).clamp(-1.0, 1.0);

  void push() {
    link.setMove(forward: forward, strafe: strafe, yaw: yaw);
    notifyListeners();
  }

  void reset() {
    stickX = stickY = turnStick = padForward = padStrafe = padYaw = 0;
    push();
  }

  void stop() {
    stickX = stickY = turnStick = padForward = padStrafe = padYaw = 0;
    link.stop();
    notifyListeners();
  }
}
