import 'package:flutter/material.dart';

/// The Arya look: graphite ground, warm off-white ink, one signal orange.
/// Values are copied from the design (`design/Arya Controller.html`).
class Ar {
  static const ground = Color(0xFF111214);
  static const raised = Color(0xFF1A1B1F);
  static const line = Color(0xFF2A2C31);
  static const lineSoft = Color(0xFF24262B);
  static const lineHard = Color(0xFF3A3D44);
  static const tick = Color(0xFF4A4D54);
  static const text = Color(0xFFEDEBE6);
  static const textDim = Color(0xFFB9B7B1);
  static const muted = Color(0xFF8E9097);
  static const faint = Color(0xFF6B6E75);
  static const signal = Color(0xFFFF5B1F);
  static const ok = Color(0xFF7FD3A0);
}

const sansFamily = 'InstrumentSans';
const monoFamily = 'JetBrainsMono';

/// Instrument Sans. `weight` is the variable-font axis value (400, 500, 600).
TextStyle sans(
  double size, {
  Color color = Ar.text,
  int weight = 400,
  double? height,
  double spacing = 0,
}) => TextStyle(
  fontFamily: sansFamily,
  fontSize: size,
  color: color,
  fontWeight: FontWeight.values[(weight ~/ 100) - 1],
  fontVariations: [FontVariation('wght', weight.toDouble())],
  height: height,
  letterSpacing: spacing,
);

/// JetBrains Mono, for every label, number and status line.
TextStyle mono(
  double size, {
  Color color = Ar.muted,
  double spacing = 0,
  int weight = 400,
}) => TextStyle(
  fontFamily: monoFamily,
  fontSize: size,
  color: color,
  fontWeight: FontWeight.values[(weight ~/ 100) - 1],
  fontVariations: [FontVariation('wght', weight.toDouble())],
  letterSpacing: spacing,
);

ThemeData aryaTheme() => ThemeData(
  brightness: Brightness.dark,
  scaffoldBackgroundColor: Ar.ground,
  fontFamily: sansFamily,
  colorScheme: const ColorScheme.dark(
    surface: Ar.ground,
    primary: Ar.text,
    error: Ar.signal,
  ),
  splashFactory: NoSplash.splashFactory,
  highlightColor: Colors.transparent,
  textTheme: ThemeData.dark().textTheme.apply(
    bodyColor: Ar.text,
    displayColor: Ar.text,
    fontFamily: sansFamily,
  ),
);
