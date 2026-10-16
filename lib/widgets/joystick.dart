import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';

/// A round stick. `onChanged(x, y)` gives x right+, y UP+ (so forward is +y), each in [-1, 1].
/// Releasing snaps back to 0, 0.
///
/// `turn` is the horizontal-only stick of the dual layout (outlined knob, centre line).
/// `big` is the single-stick layout's knob (halo and an inner ring) with a dashed ring and
/// a "FWD" mark.
class Joystick extends StatefulWidget {
  const Joystick({
    super.key,
    required this.onChanged,
    this.size = 236,
    this.knobSize,
    this.turn = false,
    this.big = false,
    this.dashed = true,
    this.deadZone = 0.08,
    this.haptics = true,
  });

  final void Function(double x, double y) onChanged;
  final double size;
  final double? knobSize;
  final bool turn;
  final bool big;
  final bool dashed;
  final double deadZone;
  final bool haptics;

  @override
  State<Joystick> createState() => _JoystickState();
}

class _JoystickState extends State<Joystick> {
  Offset _knob = Offset.zero; // -1..1, y down on screen
  bool _down = false;

  double get _knobSize => widget.knobSize ?? widget.size * 0.32;
  double get _travel => (widget.size - _knobSize) / 2;

  void _update(Offset local) {
    final centre = Offset(widget.size / 2, widget.size / 2);
    var delta = (local - centre) / _travel;
    if (widget.turn) delta = Offset(delta.dx, 0);
    final length = delta.distance;
    if (length > 1) delta = delta / length;
    setState(() => _knob = delta);
    final live = delta.distance < widget.deadZone ? Offset.zero : delta;
    widget.onChanged(live.dx, -live.dy);
  }

  void _press(Offset local) {
    _down = true;
    if (widget.haptics) HapticFeedback.selectionClick();
    _update(local);
  }

  void _release() {
    if (!_down) return;
    _down = false;
    setState(() => _knob = Offset.zero);
    widget.onChanged(0, 0);
  }

  @override
  Widget build(BuildContext context) {
    final size = widget.size;
    final k = _knobSize;
    final moved = _knob.distance >= widget.deadZone;
    return Listener(
      onPointerDown: (e) => _press(e.localPosition),
      onPointerMove: (e) => _update(e.localPosition),
      onPointerUp: (_) => _release(),
      onPointerCancel: (_) => _release(),
      child: SizedBox(
        width: size,
        height: size,
        child: CustomPaint(
          painter: _RingPainter(
            turn: widget.turn,
            dashed: widget.dashed,
            knob: _knob * _travel,
            showLine: moved && !widget.turn,
          ),
          child: Stack(
            children: [
              if (widget.big && !widget.turn)
                Positioned(
                  top: size * 0.136,
                  left: 0,
                  right: 0,
                  child: Text(
                    'FWD',
                    textAlign: TextAlign.center,
                    style: mono(10, color: Ar.faint),
                  ),
                ),
              Positioned(
                left: size / 2 - k / 2 + _knob.dx * _travel,
                top: size / 2 - k / 2 + _knob.dy * _travel,
                child: Container(
                  width: k,
                  height: k,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: widget.turn ? Ar.raised : Ar.text,
                    border: widget.turn
                        ? Border.all(color: Ar.text, width: 1.5)
                        : null,
                    boxShadow: widget.big
                        ? const [
                            BoxShadow(
                              color: Color(0x14EDEBE6),
                              spreadRadius: 6,
                              blurRadius: 0,
                            ),
                          ]
                        : null,
                  ),
                  child: widget.big
                      ? Container(
                          width: 26,
                          height: 26,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            border: Border.all(color: Ar.textDim, width: 1.5),
                          ),
                        )
                      : null,
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
  _RingPainter({
    required this.turn,
    required this.dashed,
    required this.knob,
    required this.showLine,
  });

  final bool turn;
  final bool dashed;
  final Offset knob;
  final bool showLine;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.width / 2 - 0.75;
    canvas.drawCircle(
      c,
      r,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = Ar.line,
    );
    final tick = Paint()
      ..strokeWidth = 1.5
      ..color = Ar.tick;
    final t = size.width * 0.06;
    canvas.drawLine(Offset(c.dx - r, c.dy), Offset(c.dx - r + t, c.dy), tick);
    canvas.drawLine(Offset(c.dx + r, c.dy), Offset(c.dx + r - t, c.dy), tick);
    if (turn) {
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
    if (dashed) {
      // the design's `stroke-dasharray="2 5"` on a ring at 66% of the radius
      final rr = r * 0.672;
      final dash = 2 / rr;
      final step = 7 / rr;
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = Ar.lineSoft;
      for (var a = 0.0; a < 2 * math.pi; a += step) {
        canvas.drawArc(
          Rect.fromCircle(center: c, radius: rr),
          a,
          dash,
          false,
          paint,
        );
      }
    }
    if (showLine) {
      canvas.drawLine(
        c,
        c + knob,
        Paint()
          ..color = Ar.signal
          ..strokeWidth = 1.5
          ..strokeCap = StrokeCap.round,
      );
    }
  }

  @override
  bool shouldRepaint(_RingPainter old) =>
      old.knob != knob || old.showLine != showLine || old.turn != turn;
}
