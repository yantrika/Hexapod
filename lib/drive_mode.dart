enum DriveMode {
  stick('Stick'),
  pad('Pad'),
  dual('Dual');

  const DriveMode(this.label);
  final String label;
}
