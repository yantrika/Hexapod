import 'package:flutter/material.dart';

import 'robot_link.dart';
import 'theme.dart';
import 'widgets/hold_button.dart';
import 'widgets/joystick.dart';

enum ControlMode { stick, pad, dual }

class DriveScreen extends StatefulWidget {
  const DriveScreen({super.key, required this.link});
  final RobotLink link;

  @override
  State<DriveScreen> createState() => _DriveScreenState();
}

class _DriveScreenState extends State<DriveScreen> {
  ControlMode _mode = ControlMode.stick;

  // One value per input; the move sent to the robot is their sum, clamped.
  double _stickX = 0, _stickY = 0; // move stick (x right+, y forward+)
  double _turnStick = 0; // dual mode turn stick (right+)
  double _padForward = 0,
      _padStrafe = 0,
      _padYaw = 0; // held pad buttons (left/forward +)
  int _speedIndex = 1;
  static const _speeds = [('slow', 0.3), ('normal', 0.5), ('fast', 0.8)];
  String _posture = 'stand';
  bool _talking = false;

  RobotLink get link => widget.link;

  void _push() {
    link.setMove(
      forward: _stickY + _padForward,
      strafe: -_stickX + _padStrafe,
      yaw: -_turnStick + _padYaw,
    );
  }

  void _setMode(ControlMode mode) {
    setState(() => _mode = mode);
    _stickX = _stickY = _turnStick = _padForward = _padStrafe = _padYaw = 0;
    _push();
  }

  void _stop() {
    _stickX = _stickY = _turnStick = _padForward = _padStrafe = _padYaw = 0;
    link.stop();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: link,
              builder: (context, _) => Padding(
                padding: const EdgeInsets.fromLTRB(18, 10, 18, 18),
                child: Column(
                  children: [
                    _header(),
                    const SizedBox(height: 14),
                    _statusStrip(),
                    const SizedBox(height: 14),
                    _modeTabs(),
                    Expanded(child: Center(child: _controls())),
                    _postureRow(),
                    const SizedBox(height: 12),
                    _bottomRow(),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _header() => SizedBox(
    height: 44,
    child: Row(
      children: [
        const Icon(Icons.hexagon_outlined, size: 22),
        const SizedBox(width: 10),
        Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Arya',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
            ),
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
                Text('connected', style: mono(11)),
              ],
            ),
          ],
        ),
        const Spacer(),
        IconButton(
          tooltip: 'Disconnect',
          onPressed: link.disconnect,
          icon: const Icon(Icons.link_off, color: Ar.muted),
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
        _cell('STATE', link.robotState, border: true),
        _cell('SPEED', _speeds[_speedIndex].$1),
      ],
    ),
  );

  Widget _cell(String label, String value, {bool border = false}) => Expanded(
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        border: border ? const Border(right: BorderSide(color: Ar.line)) : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: mono(10, spacing: 0.8)),
          const SizedBox(height: 4),
          Text(
            value,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500),
          ),
        ],
      ),
    ),
  );

  Widget _modeTabs() => Container(
    padding: const EdgeInsets.all(4),
    decoration: BoxDecoration(
      color: Ar.raised,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(
      children: [
        for (final (mode, label) in [
          (ControlMode.stick, 'Stick'),
          (ControlMode.pad, 'Pad'),
          (ControlMode.dual, 'Dual'),
        ])
          Expanded(
            child: GestureDetector(
              onTap: () => _setMode(mode),
              child: Container(
                height: 40,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: _mode == mode ? Ar.text : Colors.transparent,
                  borderRadius: BorderRadius.circular(9),
                ),
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: _mode == mode ? Ar.ground : Ar.muted,
                  ),
                ),
              ),
            ),
          ),
      ],
    ),
  );

  Widget _controls() => switch (_mode) {
    ControlMode.stick => _stickControls(),
    ControlMode.pad => _padControls(),
    ControlMode.dual => _dualControls(),
  };

  Widget _stickControls() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      _turnButton(Icons.rotate_left, 'Turn left', 1),
      const SizedBox(width: 12),
      Joystick(
        size: 232,
        onChanged: (x, y) {
          _stickX = x;
          _stickY = y;
          _push();
        },
      ),
      const SizedBox(width: 12),
      _turnButton(Icons.rotate_right, 'Turn right', -1),
    ],
  );

  Widget _turnButton(IconData icon, String label, double sign) => SizedBox(
    width: 48,
    child: HoldButton(
      icon: icon,
      label: label,
      height: 48,
      onHold: (held) {
        _padYaw = held ? sign : 0;
        _push();
      },
    ),
  );

  Widget _padControls() {
    Widget hold(
      IconData icon,
      String label,
      void Function(bool) onHold, {
      bool primary = false,
    }) =>
        HoldButton(icon: icon, label: label, onHold: onHold, primary: primary);
    return SizedBox(
      width: 300,
      child: GridView.count(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        crossAxisCount: 3,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 1.05,
        children: [
          hold(Icons.rotate_left, 'Turn left', (h) {
            _padYaw = h ? 1 : 0;
            _push();
          }),
          hold(Icons.arrow_upward, 'Forward', (h) {
            _padForward = h ? 1 : 0;
            _push();
          }, primary: true),
          hold(Icons.rotate_right, 'Turn right', (h) {
            _padYaw = h ? -1 : 0;
            _push();
          }),
          hold(Icons.arrow_back, 'Strafe left', (h) {
            _padStrafe = h ? 1 : 0;
            _push();
          }),
          Center(
            child: Text(
              'HOLD\nTO MOVE',
              textAlign: TextAlign.center,
              style: mono(10, color: Ar.faint),
            ),
          ),
          hold(Icons.arrow_forward, 'Strafe right', (h) {
            _padStrafe = h ? -1 : 0;
            _push();
          }),
          const SizedBox.shrink(),
          hold(Icons.arrow_downward, 'Back', (h) {
            _padForward = h ? -1 : 0;
            _push();
          }),
          const SizedBox.shrink(),
        ],
      ),
    );
  }

  Widget _dualControls() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Joystick(
            size: 152,
            onChanged: (x, y) {
              _stickX = x;
              _stickY = y;
              _push();
            },
          ),
          const SizedBox(height: 10),
          Text('MOVE', style: mono(10, spacing: 0.8)),
        ],
      ),
      const SizedBox(width: 22),
      Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Joystick(
            size: 152,
            horizontalOnly: true,
            filled: false,
            onChanged: (x, _) {
              _turnStick = x;
              _push();
            },
          ),
          const SizedBox(height: 10),
          Text('TURN', style: mono(10, spacing: 0.8)),
        ],
      ),
    ],
  );

  Widget _postureRow() => Row(
    children: [
      for (final (id, label) in [
        ('stand', 'Stand'),
        ('sit', 'Sit'),
        ('wave', 'Wave'),
      ]) ...[
        GestureDetector(
          onTap: () {
            setState(() => _posture = id);
            link.posture(id);
          },
          child: Container(
            height: 44,
            padding: const EdgeInsets.symmetric(horizontal: 14),
            alignment: Alignment.center,
            margin: const EdgeInsets.only(right: 8),
            decoration: BoxDecoration(
              color: _posture == id ? Ar.raised : Colors.transparent,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: _posture == id ? Ar.text : Ar.line),
            ),
            child: Text(
              label,
              style: TextStyle(
                fontSize: 14,
                color: _posture == id ? Ar.text : Ar.muted,
              ),
            ),
          ),
        ),
      ],
      const Spacer(),
      GestureDetector(
        onTap: () {
          setState(() => _speedIndex = (_speedIndex + 1) % _speeds.length);
          link.speed = _speeds[_speedIndex].$2;
        },
        child: Container(
          height: 44,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Ar.line),
          ),
          child: Text(_speeds[_speedIndex].$1, style: mono(12, color: Ar.text)),
        ),
      ),
    ],
  );

  Widget _bottomRow() => SizedBox(
    height: 76,
    child: Row(
      children: [
        Expanded(
          child: Listener(
            onPointerDown: (_) {
              setState(() => _talking = true);
              link.pttPress();
            },
            onPointerUp: (_) {
              setState(() => _talking = false);
              link.pttRelease();
            },
            onPointerCancel: (_) {
              setState(() => _talking = false);
              link.pttRelease();
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              decoration: BoxDecoration(
                color: _talking ? Ar.raised : Colors.transparent,
                borderRadius: BorderRadius.circular(18),
                border: Border.all(
                  color: _talking ? Ar.text : const Color(0xFF3A3D44),
                ),
              ),
              child: Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: _talking ? Ar.text : null,
                      border: _talking
                          ? null
                          : Border.all(color: const Color(0xFF3A3D44)),
                    ),
                    child: Icon(
                      Icons.mic_none,
                      color: _talking ? Ar.ground : Ar.muted,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        _talking ? 'Listening' : 'Hold to talk',
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      Text('robot’s microphone', style: mono(11)),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 96,
          child: FilledButton(
            onPressed: _stop,
            style: FilledButton.styleFrom(
              backgroundColor: Ar.signal,
              foregroundColor: Ar.ground,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(18),
              ),
            ),
            child: const Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.stop, size: 22),
                SizedBox(height: 2),
                Text(
                  'STOP',
                  style: TextStyle(
                    fontFamily: monoFamily,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 1.6,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    ),
  );
}
