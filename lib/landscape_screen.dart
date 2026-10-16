import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'drive_screen.dart';
import 'motion.dart';
import 'prefs.dart';
import 'robot_link.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/joystick.dart';

/// Dual stick, sideways: a stick under each thumb, the state and STOP in the middle.
class LandscapeScreen extends StatefulWidget {
  const LandscapeScreen({
    super.key,
    required this.link,
    required this.prefs,
    required this.motion,
  });
  final RobotLink link;
  final Prefs prefs;
  final MotionInput motion;

  @override
  State<LandscapeScreen> createState() => _LandscapeScreenState();
}

class _LandscapeScreenState extends State<LandscapeScreen> {
  String _posture = 'stand';

  RobotLink get link => widget.link;
  Prefs get prefs => widget.prefs;
  MotionInput get motion => widget.motion;

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations(const [
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
  }

  @override
  void dispose() {
    SystemChrome.setPreferredOrientations(DeviceOrientation.values);
    motion.reset();
    super.dispose();
  }

  void _leave() => Navigator.of(context).maybePop();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: ListenableBuilder(
          listenable: Listenable.merge([link, prefs, motion]),
          builder: (context, _) => LayoutBuilder(
            builder: (context, box) {
              final stick = math.min(220.0, box.maxHeight - 64);
              final moveStick = _stickColumn(
                stick,
                Joystick(
                  size: stick,
                  knobSize: stick * 70 / 220,
                  deadZone: prefs.deadZone,
                  haptics: prefs.haptics,
                  onChanged: (x, y) {
                    motion.stickX = x;
                    motion.stickY = y;
                    motion.push();
                  },
                ),
                'MOVE · fwd ${motion.forward.toStringAsFixed(2)} · str ${motion.strafe.toStringAsFixed(2)}',
              );
              final turnStick = _stickColumn(
                stick,
                Joystick(
                  size: stick,
                  knobSize: stick * 70 / 220,
                  turn: true,
                  deadZone: prefs.deadZone,
                  haptics: prefs.haptics,
                  onChanged: (x, _) {
                    motion.turnStick = x;
                    motion.push();
                  },
                ),
                'TURN · yaw ${motion.yaw.toStringAsFixed(2)}',
              );
              return Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 44,
                  vertical: 20,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    prefs.leftHanded ? turnStick : moveStick,
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 248),
                      child: SizedBox(width: 248, child: _centre()),
                    ),
                    prefs.leftHanded ? moveStick : turnStick,
                  ],
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _stickColumn(double size, Widget stick, String label) => Column(
    mainAxisAlignment: MainAxisAlignment.center,
    children: [
      stick,
      const SizedBox(height: 12),
      Text(label, style: mono(10, spacing: 0.8)),
    ],
  );

  Widget _centre() {
    final state = link.robotState;
    final title = state.isEmpty
        ? state
        : state[0].toUpperCase() + state.substring(1);
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            BackSquare(onTap: _leave),
            Row(
              children: [
                Container(
                  width: 6,
                  height: 6,
                  decoration: const BoxDecoration(
                    color: Ar.ok,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(link.host, style: mono(11)),
              ],
            ),
          ],
        ),
        const SizedBox(height: 12),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(
            children: [
              Text(title, style: sans(20, weight: 500)),
              const SizedBox(height: 2),
              Text(
                'speed cap ${(prefs.topSpeed * 100).round()} %',
                style: mono(11),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            for (final (i, (id, label)) in const [
              ('stand', 'Stand'),
              ('sit', 'Sit'),
              ('wave', 'Wave'),
            ].indexed) ...[
              if (i > 0) const SizedBox(width: 6),
              Expanded(
                child: PostureChip(
                  label: label,
                  expand: true,
                  selected: _posture == id,
                  onTap: () {
                    setState(() => _posture = id);
                    link.posture(id);
                  },
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: 64,
          child: Row(
            children: [
              Expanded(child: VoiceButton(link: link, compact: true)),
              const SizedBox(width: 8),
              StopButton(onStop: motion.stop, radius: 16),
            ],
          ),
        ),
      ],
    );
  }
}
