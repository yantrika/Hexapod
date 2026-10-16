import 'package:flutter/material.dart';

/// The Arya look: graphite ground, warm off-white ink, one signal orange.
class Ar {
  static const ground = Color(0xFF111214);
  static const raised = Color(0xFF1A1B1F);
  static const line = Color(0xFF2A2C31);
  static const lineSoft = Color(0xFF24262B);
  static const text = Color(0xFFEDEBE6);
  static const textDim = Color(0xFFB9B7B1);
  static const muted = Color(0xFF8E9097);
  static const faint = Color(0xFF6B6E75);
  static const signal = Color(0xFFFF5B1F);
  static const ok = Color(0xFF7FD3A0);
}

const monoFamily = 'monospace';

TextStyle mono(double size, {Color color = Ar.muted, double spacing = 0}) =>
    TextStyle(
      fontFamily: monoFamily,
      fontSize: size,
      color: color,
      letterSpacing: spacing,
    );

ThemeData aryaTheme() => ThemeData(
  brightness: Brightness.dark,
  scaffoldBackgroundColor: Ar.ground,
  colorScheme: const ColorScheme.dark(
    surface: Ar.ground,
    primary: Ar.text,
    error: Ar.signal,
  ),
  splashFactory: NoSplash.splashFactory,
  textTheme: ThemeData.dark().textTheme.apply(
    bodyColor: Ar.text,
    displayColor: Ar.text,
  ),
);
