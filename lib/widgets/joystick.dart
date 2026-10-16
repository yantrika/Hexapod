import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// A round stick. `onChanged(x, y)` gives x right+, y UP+ (so forward is +y), each in [-1, 1].
/// With `horizontalOnly` (the turn stick) y stays 0. Releasing snaps back to 0, 0.
class Joystick extends StatefulWidget {
  const Joystick({
    super.key,
    required this.onChanged,
    this.size = 236,
    this.horizontalOnly = false,
    this.deadZone = 0.08,
    this.filled = true,
  });

  final void Function(double x, double y) onChanged;
  final double size;
  final bool horizontalOnly;
  final double deadZone;
  final bool filled;

  @override
  State<Joystick> createState() => _JoystickState();
}

class _JoystickState extends State<Joystick> {
  Offset _knob = Offset.zero; // -1..1, y down on screen

  double get _knobSize => widget.size * 0.32;
  double get _travel => (widget.size - _knobSize) / 2;

  void _update(Offset local) {
    final centre = Offset(widget.size / 2, widget.size / 2);
    var delta = (local - centre) / _travel;
    if (widget.horizontalOnly) delta = Offset(delta.dx, 0);
    final length = delta.distance;
    if (length > 1) delta = delta / length;
    setState(() => _knob = delta);
    final live = delta.distance < widget.deadZone ? Offset.zero : delta;
    widget.onChanged(live.dx, -live.dy);
  }

  void _release() {
    setState(() => _knob = Offset.zero);
    widget.onChanged(0, 0);
  }

  @override
  Widget build(BuildContext context) {
    final size = widget.size;
    return Listener(
      onPointerDown: (e) => _update(e.localPosition),
      onPointerMove: (e) => _update(e.localPosition),
      onPointerUp: (_) => _release(),
      onPointerCancel: (_) => _release(),
      child: SizedBox(
        width: size,
        height: size,
        child: CustomPaint(
          painter: _RingPainter(horizontalOnly: widget.horizontalOnly),
          child: Stack(
            children: [
              Positioned(
                left: size / 2 - _knobSize / 2 + _knob.dx * _travel,
                top: size / 2 - _knobSize / 2 + _knob.dy * _travel,
                child: Container(
                  width: _knobSize,
                  height: _knobSize,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: widget.filled ? Ar.text : Ar.raised,
                    border: widget.filled
                        ? null
                        : Border.all(color: Ar.text, width: 1.5),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RingPainter extends CustomPainter {
  _RingPainter({required this.horizontalOnly});
  final bool horizontalOnly;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.width / 2 - 1;
    final ring = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5
      ..color = Ar.line;
    canvas.drawCircle(c, r, ring);
    final tick = Paint()
      ..strokeWidth = 1.5
      ..color = const Color(0xFF4A4D54);
    final t = size.width * 0.06;
    canvas.drawLine(Offset(c.dx - r, c.dy), Offset(c.dx - r + t, c.dy), tick);
    canvas.drawLine(Offset(c.dx + r, c.dy), Offset(c.dx + r - t, c.dy), tick);
    if (horizontalOnly) {
      canvas.drawLine(
        Offset(c.dx - r + t, c.dy),
        Offset(c.dx + r - t, c.dy),
        Paint()
          ..color = Ar.lineSoft
          ..strokeWidth = 1,
      );
      return;
    }
    canvas.drawLine(Offset(c.dx, c.dy - r), Offset(c.dx, c.dy - r + t), tick);
    canvas.drawLine(Offset(c.dx, c.dy + r), Offset(c.dx, c.dy + r - t), tick);
    final dots = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = Ar.lineSoft;
    const steps = 48;
    for (var i = 0; i < steps; i += 2) {
      final a0 = i * 2 * math.pi / steps;
      canvas.drawArc(
        Rect.fromCircle(center: c, radius: r * 0.66),
        a0,
        math.pi / steps,
        false,
        dots,
      );
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.horizontalOnly != horizontalOnly;
}
