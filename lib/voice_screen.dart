import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'motion.dart';
import 'robot_link.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/icons.dart';

/// What Arya heard, what it did with it, what it said; hold the mic to talk; type to make
/// Arya say something. STOP stays in reach.
class VoiceScreen extends StatefulWidget {
  const VoiceScreen({super.key, required this.link, required this.motion});
  final RobotLink link;
  final MotionInput motion;

  @override
  State<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends State<VoiceScreen>
    with SingleTickerProviderStateMixin {
  final _say = TextEditingController();
  late final AnimationController _wave = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  );
  bool _held = false;

  RobotLink get link => widget.link;

  @override
  void dispose() {
    if (_held) link.pttRelease();
    _say.dispose();
    _wave.dispose();
    super.dispose();
  }

  void _hold(bool held) {
    if (_held == held) return;
    if (held && !link.pttAvailable) return;
    setState(() => _held = held);
    if (held) {
      HapticFeedback.selectionClick();
      link.pttPress();
      _wave.repeat();
    } else {
      link.pttRelease();
      _wave.stop();
    }
  }

  void _send() {
    final text = _say.text.trim();
    if (text.isEmpty || !link.sayAvailable) return;
    link.say(text);
    _say.clear();
  }

  String _clock(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}:${t.second.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      resizeToAvoidBottomInset: true,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: link,
              builder: (context, _) => Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 22),
                child: Column(
                  children: [
                    _header(),
                    const SizedBox(height: 16),
                    _mic(),
                    const SizedBox(height: 8),
                    Expanded(child: _logList()),
                    const SizedBox(height: 16),
                    _composer(),
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
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        const BackSquare(),
        Text('Voice', style: sans(16, weight: 500)),
        Container(
          padding: const EdgeInsets.all(4),
          decoration: BoxDecoration(
            color: Ar.raised,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(
            children: [
              Container(
                height: 36,
                padding: const EdgeInsets.symmetric(horizontal: 10),
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: Ar.text,
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Text('robot mic', style: mono(11, color: Ar.ground)),
              ),
              // The page never sends audio, so the phone's microphone is not offered yet.
              Container(
                height: 36,
                padding: const EdgeInsets.symmetric(horizontal: 10),
                alignment: Alignment.center,
                child: Text('phone', style: mono(11, color: Ar.tick)),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _mic() {
    final available = link.pttAvailable;
    final title = _held
        ? 'Listening'
        : (available ? 'Hold to talk' : 'Voice off');
    final sub = _held
        ? 'release to send'
        : (available
              ? 'robot’s microphone'
              : (link.pttReason ?? 'unavailable'));
    return Padding(
      padding: const EdgeInsets.only(top: 18, bottom: 8),
      child: Column(
        children: [
          SizedBox(
            width: 168,
            height: 168,
            child: Stack(
              alignment: Alignment.center,
              children: [
                Container(
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: Ar.line),
                  ),
                ),
                Container(
                  width: 132,
                  height: 132,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: Ar.lineHard),
                  ),
                ),
                Semantics(
                  button: true,
                  label: 'Hold to talk',
                  excludeSemantics: true,
                  child: Listener(
                    onPointerDown: (_) => _hold(true),
                    onPointerUp: (_) => _hold(false),
                    onPointerCancel: (_) => _hold(false),
                    child: Container(
                      width: 96,
                      height: 96,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: _held ? Ar.text : Ar.raised,
                        border: _held ? null : Border.all(color: Ar.lineHard),
                      ),
                      child: AIcon(
                        Shapes.mic,
                        size: 30,
                        color: _held
                            ? Ar.ground
                            : (available ? Ar.text : Ar.faint),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 18),
          Text(title, style: sans(22, weight: 500)),
          const SizedBox(height: 6),
          Text(sub, style: mono(12)),
          const SizedBox(height: 18),
          AnimatedBuilder(
            animation: _wave,
            builder: (context, _) => CustomPaint(
              size: const Size(220, 28),
              painter: _LevelPainter(active: _held, t: _wave.value),
            ),
          ),
        ],
      ),
    );
  }

  Widget _logList() {
    final lines = link.log.reversed.toList();
    return Container(
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: Ar.line)),
      ),
      child: lines.isEmpty
          ? Padding(
              padding: const EdgeInsets.only(top: 18),
              child: Text(
                'Nothing yet. Hold the microphone and say something, or type below.',
                style: sans(14, color: Ar.muted, height: 1.5),
              ),
            )
          : ListView.separated(
              padding: EdgeInsets.zero,
              itemCount: lines.length,
              separatorBuilder: (_, _) => const Divider(
                height: 1,
                thickness: 1,
                color: Color(0xFF1F2125),
              ),
              itemBuilder: (context, i) => _line(lines[i]),
            ),
    );
  }

  Widget _line(VoiceLine l) {
    final who = l.fromYou ? 'you' : 'arya';
    final right = l.fromYou
        ? (l.route == null ? null : '→ ${l.route}')
        : '${link.mode} mode';
    final routeColor = l.fromYou && l.route != null && l.route != 'chat'
        ? Ar.signal
        : Ar.faint;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('$who · ${_clock(l.at)}', style: mono(11, color: Ar.faint)),
              if (right != null)
                Text(
                  right,
                  style: mono(11, color: l.fromYou ? routeColor : Ar.faint),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            l.fromYou ? '“${l.text}”' : l.text,
            style: sans(15, color: l.fromYou ? Ar.text : Ar.textDim),
          ),
        ],
      ),
    );
  }

  Widget _composer() => Row(
    children: [
      Expanded(
        child: TextField(
          controller: _say,
          enabled: link.sayAvailable,
          maxLength: link.sayMaxChars,
          onSubmitted: (_) => _send(),
          textInputAction: TextInputAction.send,
          style: sans(15),
          cursorColor: Ar.text,
          decoration: InputDecoration(
            counterText: '',
            hintText: link.sayAvailable
                ? 'Type for Arya to say…'
                : (link.sayReason ?? 'Typing is off'),
            hintStyle: sans(15, color: Ar.faint),
            filled: true,
            fillColor: Ar.raised,
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 17,
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Ar.line),
            ),
            disabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Ar.line),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Ar.text),
            ),
          ),
        ),
      ),
      const SizedBox(width: 10),
      SizedBox(
        height: 52,
        child: StopButton(
          onStop: widget.motion.stop,
          width: 52,
          radius: 14,
          compact: true,
        ),
      ),
    ],
  );
}

/// The wide level meter under the microphone: flat when idle, moving while the mic is open.
class _LevelPainter extends CustomPainter {
  _LevelPainter({required this.active, required this.t});
  final bool active;
  final double t;

  @override
  void paint(Canvas canvas, Size size) {
    const n = 25;
    final slot = size.width / n;
    for (var i = 0; i < n; i++) {
      final edge =
          (i - (n - 1) / 2).abs() / ((n - 1) / 2); // 0 centre .. 1 edge
      final base = 1 - edge * 0.85;
      final wiggle = 0.5 + 0.5 * math.sin(2 * math.pi * (t * 1.0 + i * 0.13));
      final level = active ? base * (0.3 + 0.7 * wiggle) : 0.0;
      final h = math.max(2.0, size.height * level);
      final color = active ? (edge > 0.6 ? Ar.lineHard : Ar.text) : Ar.lineHard;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(i * slot + (slot - 3) / 2, (size.height - h) / 2, 3, h),
          const Radius.circular(1.5),
        ),
        Paint()..color = color,
      );
    }
  }

  @override
  bool shouldRepaint(_LevelPainter old) => old.t != t || old.active != active;
}
