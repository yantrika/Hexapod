import 'package:flutter/material.dart';

import 'robot_link.dart';
import 'theme.dart';
import 'widgets/common.dart';

/// The robot's body at a glance. The robot does not report battery, temperature, tilt or
/// joint angles over the phone link yet, so those show "—" until it does.
class HealthScreen extends StatelessWidget {
  const HealthScreen({super.key, required this.link});
  final RobotLink link;

  static const _legs = ['RF', 'RM', 'RR', 'LR', 'LM', 'LF'];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: link,
              builder: (context, _) => SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 22),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SizedBox(
                      height: 44,
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const BackSquare(),
                          Text('Body', style: sans(16, weight: 500)),
                          SizedBox(
                            width: 44,
                            child: Text(
                              '50 Hz',
                              textAlign: TextAlign.right,
                              style: mono(11),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 14),
                    Container(
                      padding: const EdgeInsets.fromLTRB(10, 10, 10, 4),
                      decoration: BoxDecoration(
                        border: Border.all(color: Ar.line),
                        borderRadius: BorderRadius.circular(16),
                      ),
                      child: AspectRatio(
                        aspectRatio: 340 / 220,
                        child: CustomPaint(painter: _BodyPainter()),
                      ),
                    ),
                    const SizedBox(height: 14),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const CapLabel('JOINT HEADROOM'),
                        Text('coxa · femur · tibia', style: mono(10)),
                      ],
                    ),
                    const SizedBox(height: 10),
                    for (final leg in _legs)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 34,
                              child: Text(leg, style: mono(11)),
                            ),
                            for (var i = 0; i < 3; i++) ...[
                              if (i > 0) const SizedBox(width: 8),
                              Expanded(
                                child: Container(
                                  height: 4,
                                  decoration: BoxDecoration(
                                    color: Ar.lineSoft,
                                    borderRadius: BorderRadius.circular(2),
                                  ),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                    Text(
                      'waiting for the robot to report joint angles',
                      style: mono(10, color: Ar.faint),
                    ),
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        Expanded(
                          child: _tile('BATTERY', '— V', 'no sensor yet'),
                        ),
                        const SizedBox(width: 10),
                        Expanded(child: _tile('PI 5', '—', 'not reported')),
                      ],
                    ),
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        Expanded(
                          child: _tile('TILT', '—', 'no tilt sensor yet'),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: _tile(
                            'LINK',
                            'live',
                            'stops if silent ${link.deadmanS.toStringAsFixed(1)} s',
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 22),
                    Row(
                      children: [
                        Expanded(child: _action('Relax legs', null)),
                        const SizedBox(width: 10),
                        Expanded(child: _action('Servos on', Ar.ok)),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _tile(String label, String value, String sub) => Container(
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      border: Border.all(color: Ar.line),
      borderRadius: BorderRadius.circular(14),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        CapLabel(label),
        const SizedBox(height: 6),
        Text(value, style: mono(22, color: Ar.text)),
        const SizedBox(height: 6),
        Text(sub, style: sans(12, color: Ar.muted)),
      ],
    ),
  );

  // Not wired: the phone protocol has no relax or servo-power request.
  Widget _action(String label, Color? dot) => Opacity(
    opacity: 0.45,
    child: Container(
      height: 52,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: dot == null ? Colors.transparent : Ar.raised,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: dot == null ? Ar.line : Ar.text),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          if (dot != null) ...[
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
            ),
            const SizedBox(width: 8),
          ],
          Text(label, style: sans(15)),
        ],
      ),
    ),
  );
}

/// Top view of the six legs (the design's 340x220 drawing).
class _BodyPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final k = size.width / 340;
    canvas.scale(k);
    final body = Path()
      ..moveTo(145, 40)
      ..relativeLineTo(50, 0)
      ..relativeLineTo(26, 70)
      ..relativeLineTo(-26, 70)
      ..relativeLineTo(-50, 0)
      ..relativeLineTo(-26, -70)
      ..close();
    canvas.drawPath(
      body,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = Ar.tick,
    );
    canvas.drawCircle(const Offset(170, 110), 4, Paint()..color = Ar.tick);

    final leg = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..color = Ar.text;
    void polyline(List<Offset> pts) {
      final p = Path()..moveTo(pts.first.dx, pts.first.dy);
      for (final o in pts.skip(1)) {
        p.lineTo(o.dx, o.dy);
      }
      canvas.drawPath(p, leg);
    }

    polyline(const [Offset(145, 44), Offset(104, 28), Offset(70, 42)]);
    polyline(const [Offset(119, 110), Offset(78, 110), Offset(46, 126)]);
    polyline(const [Offset(145, 176), Offset(104, 192), Offset(70, 178)]);
    polyline(const [Offset(195, 44), Offset(236, 28), Offset(270, 42)]);
    polyline(const [Offset(221, 110), Offset(262, 110), Offset(294, 126)]);
    polyline(const [Offset(195, 176), Offset(236, 192), Offset(270, 178)]);

    void label(
      String text,
      double x,
      double y, {
      TextAlign align = TextAlign.left,
    }) {
      final tp = TextPainter(
        text: TextSpan(
          text: text,
          style: mono(10, color: Ar.faint),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final dx = align == TextAlign.center ? x - tp.width / 2 : x;
      tp.paint(canvas, Offset(dx, y - tp.height + 2));
    }

    label('FRONT', 170, 14, align: TextAlign.center);
    label('LF', 40, 56);
    label('LM', 16, 140);
    label('LR', 40, 198);
    label('RF', 282, 56);
    label('RM', 304, 140);
    label('RR', 280, 198);
  }

  @override
  bool shouldRepaint(_BodyPainter old) => false;
}
