import 'package:flutter/foundation.dart';

import 'drive_mode.dart';

/// The app's own settings. Kept in memory for now (saving them across launches needs
/// `shared_preferences`, which needs Windows Developer Mode on this machine).
class Prefs extends ChangeNotifier {
  DriveMode defaultMode = DriveMode.stick;
  double topSpeed = 0.7; // 0..1, the cap on the robot's normalised walk speed
  double deadZone = 0.08; // stick dead zone, 0..0.3
  bool haptics = true;
  bool leftHanded = false;

  void update(void Function(Prefs p) change) {
    change(this);
    notifyListeners();
  }
}
