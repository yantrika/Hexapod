import 'package:flutter/material.dart';

import 'drive_mode.dart';
import 'landscape_screen.dart';
import 'motion.dart';
import 'prefs.dart';
import 'robot_link.dart';
import 'settings_screen.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/hold_button.dart';
import 'widgets/icons.dart';
import 'widgets/joystick.dart';

/// Shared by Drive and Landscape: the speed steps (fractions of the top-speed setting).
const speedSteps = [('slow', 0.4), ('normal', 0.7), ('fast', 1.0)];

class DriveScreen extends StatefulWidget {
  const DriveScreen({
    super.key,
    required this.link,
    required this.prefs,
    required this.motion,
  });
  final RobotLink link;
  final Prefs prefs;
  final MotionInput motion;

  @override
  State<DriveScreen> createState() => _DriveScreenState();
}

class _DriveScreenState extends State<DriveScreen> {
  late DriveMode _mode = widget.prefs.defaultMode;
  int _speedIndex = 1;
  String _posture = 'stand';

  RobotLink get link => widget.link;
  Prefs get prefs => widget.prefs;
  MotionInput get motion => widget.motion;

  @override
  void initState() {
    super.initState();
    _applySpeed();
    prefs.addListener(_applySpeed);
  }

  @override
  void dispose() {
    prefs.removeListener(_applySpeed);
    super.dispose();
  }

  void _applySpeed() =>
      link.speed = prefs.topSpeed * speedSteps[_speedIndex].$2;

  void _setMode(DriveMode mode) {
    setState(() => _mode = mode);
    motion.reset();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: Listenable.merge([link, prefs, motion]),
              builder: (context, _) => Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 22),
                child: Column(
                  children: [
                    _header(context),
                    const SizedBox(height: 14),
                    _statusStrip(),
                    const SizedBox(height: 14),
                    _heard(),
                    const SizedBox(height: 14),
                    _tabs(),
                    Expanded(
                      child: Center(
                        child: FittedBox(
                          fit: BoxFit.scaleDown,
                          child: _controls(context),
                        ),
                      ),
                    ),
                    _postureRow(),
                    const SizedBox(height: 14),
                    SizedBox(
                      height: 76,
                      child: Row(
                        children: [
                          Expanded(child: VoiceButton(link: link)),
                          const SizedBox(width: 10),
                          StopButton(onStop: motion.stop),
                        ],
                      ),
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

  // --- header, status, last heard -------------------------------------------------------------

  Widget _header(BuildContext context) => SizedBox(
    height: 44,
    child: Row(
      children: [
        const AIcon(Shapes.hexagon, size: 22, color: Ar.text),
        const SizedBox(width: 10),
        Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Arya',
              style: sans(15, weight: 600, spacing: 0.3, height: 1.1),
            ),
            const SizedBox(height: 2),
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
        const Spacer(),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          decoration: BoxDecoration(
            border: Border.all(color: Ar.line),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Row(
            children: [
              const AIcon(
                Shapes.battery,
                size: 16,
                color: Ar.textDim,
                stroke: 1.4,
              ),
              const SizedBox(width: 6),
              Text('— V', style: mono(11, color: Ar.textDim)),
            ],
          ),
        ),
        const SizedBox(width: 8),
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) =>
                  SettingsScreen(link: link, prefs: prefs, motion: motion),
            ),
          ),
          child: Semantics(
            button: true,
            label: 'Settings',
            excludeSemantics: true,
            child: Container(
              width: 44,
              height: 44,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Ar.line),
              ),
              child: const AIcon(Shapes.sliders, size: 18, color: Ar.text),
            ),
          ),
        ),
      ],
    ),
  );

  Widget _statusStrip() => Container(
    decoration: BoxDecoration(
      border: Border.all(color: Ar.line),
      borderRadius: BorderRadius.circular(14),
    ),
    child: Row(
      children: [
        _cell(
          'STATE',
          _title(link.robotState),
          sans(16, weight: 500),
          border: true,
        ),
        _cell(
          'SPEED',
          '${(link.speed * 100).round()} %',
          mono(16, color: Ar.text, weight: 500),
          border: true,
        ),
        _cell('PI TEMP', '—', mono(16, color: Ar.text, weight: 500)),
      ],
    ),
  );

  String _title(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

  Widget _cell(
    String label,
    String value,
    TextStyle style, {
    bool border = false,
  }) => Expanded(
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        border: border ? const Border(right: BorderSide(color: Ar.line)) : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CapLabel(label),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: style,
          ),
        ],
      ),
    ),
  );

  Widget _heard() {
    VoiceLine? last;
    for (final l in link.log.reversed) {
      if (l.fromYou) {
        last = l;
        break;
      }
    }
    return SizedBox(
      height: 18,
      child: last == null
          ? null
          : Row(
              children: [
                Text('heard', style: mono(12, color: Ar.text)),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    '“${last.text}”',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: mono(12),
                  ),
                ),
                if (last.route != null) ...[
                  const SizedBox(width: 8),
                  Text('→', style: mono(12, color: Ar.tick)),
                  const SizedBox(width: 8),
                  Text(last.route!, style: mono(12, color: Ar.signal)),
                ],
              ],
            ),
    );
  }

  Widget _tabs() => Container(
    padding: const EdgeInsets.all(4),
    decoration: BoxDecoration(
      color: Ar.raised,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(
      children: [
        for (final mode in DriveMode.values)
          Expanded(
            child: Semantics(
              button: true,
              selected: _mode == mode,
              label: mode.label,
              excludeSemantics: true,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => _setMode(mode),
                child: Container(
                  height: 40,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: _mode == mode ? Ar.text : Colors.transparent,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  child: Text(
                    mode.label,
                    style: sans(
                      14,
                      weight: 500,
                      color: _mode == mode ? Ar.ground : Ar.muted,
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    ),
  );

  // --- the three layouts -------------------------------------------------------------------

  Widget _controls(BuildContext context) => switch (_mode) {
    DriveMode.stick => _stick(),
    DriveMode.pad => _pad(),
    DriveMode.dual => _dual(context),
  };

  Widget _turnRound(String shapes, String label, double sign) => HoldButton(
    shapes: shapes,
    label: label,
    width: 48,
    height: 48,
    round: true,
    iconSize: 18,
    iconStroke: 1.6,
    style: HoldStyle.ghost,
    haptics: prefs.haptics,
    onHold: (held) {
      motion.padYaw = held ? sign : 0;
      motion.push();
    },
  );

  Widget _stick() => Column(
    mainAxisSize: MainAxisSize.min,
    children: [
      Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _turnRound(Shapes.turnLeft, 'Turn left', 1),
          const SizedBox(width: 14),
          Joystick(
            size: 236,
            knobSize: 76,
            big: true,
            deadZone: prefs.deadZone,
            haptics: prefs.haptics,
            onChanged: (x, y) {
              motion.stickX = x;
              motion.stickY = y;
              motion.push();
            },
          ),
          const SizedBox(width: 14),
          _turnRound(Shapes.turnRight, 'Turn right', -1),
        ],
      ),
      const SizedBox(height: 14),
      Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _readout('fwd', motion.forward),
          const SizedBox(width: 18),
          _readout('strafe', motion.strafe),
          const SizedBox(width: 18),
          _readout('yaw', motion.yaw),
        ],
      ),
    ],
  );

  Widget _readout(String name, double v) => Text.rich(
    TextSpan(
      children: [
        TextSpan(text: '$name '),
        TextSpan(
          text: v.toStringAsFixed(2),
          style: mono(11, color: Ar.text),
        ),
      ],
    ),
    style: mono(11),
  );

  Widget _pad() {
    Widget hold(
      String shapes,
      String label,
      void Function(bool) onHold, {
      HoldStyle style = HoldStyle.soft,
      double iconSize = 24,
      double stroke = 1.8,
    }) => HoldButton(
      shapes: shapes,
      label: label,
      onHold: onHold,
      style: style,
      iconSize: iconSize,
      iconStroke: stroke,
      haptics: prefs.haptics,
    );
    void yaw(bool h, double s) {
      motion.padYaw = h ? s : 0;
      motion.push();
    }

    void fwd(bool h, double s) {
      motion.padForward = h ? s : 0;
      motion.push();
    }

    void strafe(bool h, double s) {
      motion.padStrafe = h ? s : 0;
      motion.push();
    }

    return SizedBox(
      width: 300,
      child: GridView.count(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        crossAxisCount: 3,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 94 / 84,
        children: [
          hold(
            Shapes.turnLeft,
            'Turn left',
            (h) => yaw(h, 1),
            style: HoldStyle.ghost,
            iconSize: 22,
            stroke: 1.6,
          ),
          hold(
            Shapes.up,
            'Forward',
            (h) => fwd(h, 1),
            style: HoldStyle.primary,
          ),
          hold(
            Shapes.turnRight,
            'Turn right',
            (h) => yaw(h, -1),
            style: HoldStyle.ghost,
            iconSize: 22,
            stroke: 1.6,
          ),
          hold(Shapes.left, 'Strafe left', (h) => strafe(h, 1)),
          Center(
            child: Text(
              'HOLD\nTO MOVE',
              textAlign: TextAlign.center,
              style: mono(10, color: Ar.faint).copyWith(height: 1.5),
            ),
          ),
          hold(Shapes.right, 'Strafe right', (h) => strafe(h, -1)),
          const SizedBox.shrink(),
          hold(Shapes.down, 'Back', (h) => fwd(h, -1)),
          const SizedBox.shrink(),
        ],
      ),
    );
  }

  Widget _dual(BuildContext context) {
    final moveStick = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Joystick(
          size: 152,
          knobSize: 58,
          dashed: false,
          deadZone: prefs.deadZone,
          haptics: prefs.haptics,
          onChanged: (x, y) {
            motion.stickX = x;
            motion.stickY = y;
            motion.push();
          },
        ),
        const SizedBox(height: 10),
        const CapLabel('MOVE'),
      ],
    );
    final turnStick = Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Joystick(
          size: 152,
          knobSize: 58,
          turn: true,
          deadZone: prefs.deadZone,
          haptics: prefs.haptics,
          onChanged: (x, _) {
            motion.turnStick = x;
            motion.push();
          },
        ),
        const SizedBox(height: 10),
        const CapLabel('TURN'),
      ],
    );
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: prefs.leftHanded
              ? [turnStick, const SizedBox(width: 22), moveStick]
              : [moveStick, const SizedBox(width: 22), turnStick],
        ),
        const SizedBox(height: 14),
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () {
            motion.reset();
            Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) =>
                    LandscapeScreen(link: link, prefs: prefs, motion: motion),
              ),
            );
          },
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('rotate for full dual view', style: mono(11)),
                const SizedBox(width: 6),
                const AIcon(
                  Shapes.turnRight,
                  size: 12,
                  color: Ar.muted,
                  stroke: 1.6,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  // --- posture and speed ---------------------------------------------------------------------

  Widget _postureRow() => Row(
    children: [
      for (final (id, label) in const [
        ('stand', 'Stand'),
        ('sit', 'Sit'),
        ('wave', 'Wave'),
      ])
        Padding(
          padding: const EdgeInsets.only(right: 8),
          child: PostureChip(
            label: label,
            selected: _posture == id,
            onTap: () {
              setState(() => _posture = id);
              link.posture(id);
            },
          ),
        ),
      const Spacer(),
      GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () {
          setState(() => _speedIndex = (_speedIndex + 1) % speedSteps.length);
          _applySpeed();
        },
        child: Semantics(
          button: true,
          label: 'Speed limit',
          excludeSemantics: true,
          child: Container(
            height: 44,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border.all(color: Ar.line),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              children: [
                const AIcon(
                  Shapes.gauge,
                  size: 14,
                  color: Ar.muted,
                  stroke: 1.8,
                ),
                const SizedBox(width: 8),
                Text(
                  speedSteps[_speedIndex].$1,
                  style: mono(12, color: Ar.text),
                ),
              ],
            ),
          ),
        ),
      ),
    ],
  );
}

/// Stand / Sit / Wave chip.
class PostureChip extends StatelessWidget {
  const PostureChip({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
    this.expand = false,
  });
  final String label;
  final bool selected;
  final VoidCallback onTap;
  final bool expand;

  @override
  Widget build(BuildContext context) => GestureDetector(
    behavior: HitTestBehavior.opaque,
    onTap: onTap,
    child: Semantics(
      button: true,
      selected: selected,
      label: label,
      excludeSemantics: true,
      child: Container(
        height: 44,
        alignment: Alignment.center,
        padding: expand ? null : const EdgeInsets.symmetric(horizontal: 14),
        decoration: BoxDecoration(
          color: selected ? Ar.raised : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: selected ? Ar.text : Ar.line),
        ),
        child: Text(
          label,
          style: sans(14, color: selected ? Ar.text : Ar.muted),
        ),
      ),
    ),
  );
}
