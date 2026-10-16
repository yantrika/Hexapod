import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../robot_link.dart';
import '../theme.dart';
import 'icons.dart';

/// Small caps label: mono 10, wide tracking (the design's `letter-spacing: 0.08em`).
class CapLabel extends StatelessWidget {
  const CapLabel(this.text, {super.key, this.color = Ar.muted});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) =>
      Text(text, style: mono(10, color: color, spacing: 0.8));
}

/// The 44px square back button used at the top of every sub screen.
class BackSquare extends StatelessWidget {
  const BackSquare({super.key, this.onTap});
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
    onTap: onTap ?? () => Navigator.of(context).maybePop(),
    behavior: HitTestBehavior.opaque,
    child: Semantics(
      button: true,
      label: 'Back',
      excludeSemantics: true,
      child: Container(
        width: 44,
        height: 44,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Ar.line),
        ),
        child: const AIcon(Shapes.back, size: 18, color: Ar.text, stroke: 1.7),
      ),
    ),
  );
}

/// The orange emergency STOP. Always the same spot, always reachable.
class StopButton extends StatelessWidget {
  const StopButton({
    super.key,
    required this.onStop,
    this.width = 96,
    this.radius = 18,
    this.compact = false,
  });

  final VoidCallback onStop;
  final double width;
  final double radius;

  /// Icon-only (the Voice screen's 52px square).
  final bool compact;

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    label: 'Emergency stop',
    excludeSemantics: true,
    child: GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTapDown: (_) {
        HapticFeedback.heavyImpact();
        onStop();
      },
      child: Container(
        width: width,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: Ar.signal,
          borderRadius: BorderRadius.circular(radius),
        ),
        child: compact
            ? const AIcon(Shapes.stop, size: 18, color: Ar.ground, fill: true)
            : Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const AIcon(
                    Shapes.stop,
                    size: 20,
                    color: Ar.ground,
                    fill: true,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'STOP',
                    style: mono(
                      15,
                      color: Ar.ground,
                      spacing: 1.8,
                      weight: 500,
                    ),
                  ),
                ],
              ),
      ),
    ),
  );
}

/// The microphone: hold it to talk through the robot's microphone (the robot's phone server
/// supports push-to-talk only; always-on listening is not available over this protocol yet).
class VoiceButton extends StatefulWidget {
  const VoiceButton({super.key, required this.link, this.compact = false});
  final RobotLink link;
  final bool compact;

  @override
  State<VoiceButton> createState() => _VoiceButtonState();
}

class _VoiceButtonState extends State<VoiceButton> {
  bool _held = false;

  void _set(bool held) {
    if (_held == held) return;
    final link = widget.link;
    if (held && !link.pttAvailable) return;
    setState(() => _held = held);
    if (held) {
      HapticFeedback.selectionClick();
      link.pttPress();
    } else {
      link.pttRelease();
    }
  }

  @override
  Widget build(BuildContext context) {
    final link = widget.link;
    final available = link.pttAvailable;
    final title = _held
        ? 'Listening'
        : (available ? 'Hold to talk' : 'Voice off');
    final sub = _held
        ? 'release to send'
        : (available
              ? 'robot’s microphone'
              : (link.pttReason ?? 'unavailable'));
    return Semantics(
      button: true,
      label: 'Hold to talk to the robot',
      excludeSemantics: true,
      child: Listener(
        onPointerDown: (_) => _set(true),
        onPointerUp: (_) => _set(false),
        onPointerCancel: (_) => _set(false),
        child: CustomPaint(
          painter: _held
              ? null
              : _DashedRRect(
                  radius: 18,
                  color: Ar.lineHard,
                  dashed: !available,
                ),
          child: Container(
            padding: EdgeInsets.symmetric(horizontal: widget.compact ? 10 : 16),
            decoration: BoxDecoration(
              color: _held ? Ar.raised : Colors.transparent,
              borderRadius: BorderRadius.circular(18),
              border: _held ? Border.all(color: Ar.lineHard) : null,
            ),
            child: Row(
              children: [
                Container(
                  width: widget.compact ? 36 : 44,
                  height: widget.compact ? 36 : 44,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _held ? Ar.text : null,
                    border: _held ? null : Border.all(color: Ar.lineHard),
                  ),
                  child: AIcon(
                    Shapes.mic,
                    size: 20,
                    color: _held ? Ar.ground : Ar.muted,
                    stroke: 1.7,
                  ),
                ),
                SizedBox(width: widget.compact ? 8 : 12),
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: sans(widget.compact ? 14 : 15, weight: 500),
                      ),
                      if (!widget.compact) ...[
                        const SizedBox(height: 3),
                        Text(
                          sub,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: mono(11),
                        ),
                      ],
                    ],
                  ),
                ),
                if (_held && !widget.compact) const Waveform(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The six little level bars shown while the microphone is open (the design's waveform).
class Waveform extends StatefulWidget {
  const Waveform({super.key, this.width = 34, this.height = 22});
  final double width;
  final double height;

  @override
  State<Waveform> createState() => _WaveformState();
}

class _WaveformState extends State<Waveform>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _c,
    builder: (context, _) => CustomPaint(
      size: Size(widget.width, widget.height),
      painter: _WavePainter(_c.value),
    ),
  );
}

class _WavePainter extends CustomPainter {
  _WavePainter(this.t);
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    const colors = [Ar.muted, Ar.text, Ar.text, Ar.text, Ar.muted, Ar.tick];
    final slot = size.width / 6;
    for (var i = 0; i < 6; i++) {
      final level =
          0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * (t + i / 6)));
      final h = math.max(2.0, size.height * level);
      final x = i * slot;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(x, (size.height - h) / 2, 3, h),
          const Radius.circular(1.5),
        ),
        Paint()..color = colors[i],
      );
    }
  }

  @override
  bool shouldRepaint(_WavePainter old) => old.t != t;
}

class _DashedRRect extends CustomPainter {
  _DashedRRect({
    required this.radius,
    required this.color,
    required this.dashed,
  });
  final double radius;
  final Color color;
  final bool dashed;

  @override
  void paint(Canvas canvas, Size size) {
    final rrect = RRect.fromRectAndRadius(
      (Offset.zero & size).deflate(0.5),
      Radius.circular(radius),
    );
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = color;
    if (!dashed) {
      canvas.drawRRect(rrect, paint);
      return;
    }
    final path = Path()..addRRect(rrect);
    for (final metric in path.computeMetrics()) {
      for (var d = 0.0; d < metric.length; d += 8) {
        canvas.drawPath(
          metric.extractPath(d, math.min(d + 4, metric.length)),
          paint,
        );
      }
    }
  }

  @override
  bool shouldRepaint(_DashedRRect old) => old.dashed != dashed;
}
