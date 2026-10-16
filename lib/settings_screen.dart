import 'package:flutter/material.dart';

import 'drive_mode.dart';
import 'health_screen.dart';
import 'motion.dart';
import 'prefs.dart';
import 'robot_link.dart';
import 'theme.dart';
import 'voice_screen.dart';
import 'widgets/common.dart';
import 'widgets/icons.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({
    super.key,
    required this.link,
    required this.prefs,
    required this.motion,
  });
  final RobotLink link;
  final Prefs prefs;
  final MotionInput motion;

  String _cap(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: Listenable.merge([link, prefs]),
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
                          Text('Settings', style: sans(16, weight: 500)),
                          const SizedBox(width: 44),
                        ],
                      ),
                    ),
                    const SizedBox(height: 18),
                    if (link.hasPersona) ...[
                      const CapLabel('ARYA’S MOOD'),
                      const SizedBox(height: 10),
                      _moodTabs(),
                      const SizedBox(height: 10),
                      _speakerRow(context),
                      const SizedBox(height: 18),
                    ],
                    _card([
                      _row(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text('Default layout', style: sans(15)),
                            GestureDetector(
                              behavior: HitTestBehavior.opaque,
                              onTap: () => prefs.update((p) {
                                p.defaultMode =
                                    DriveMode.values[(p.defaultMode.index + 1) %
                                        DriveMode.values.length];
                              }),
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                  vertical: 6,
                                ),
                                child: Text(
                                  prefs.defaultMode.label.toLowerCase(),
                                  style: mono(12, color: Ar.textDim),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                      _slider(
                        'Top speed',
                        '${(prefs.topSpeed * 100).round()} %',
                        prefs.topSpeed * 100,
                        0,
                        100,
                        (v) => prefs.update(
                          (p) => p.topSpeed = (v / 100).clamp(0.1, 1.0),
                        ),
                      ),
                      _slider(
                        'Stick dead zone',
                        '${(prefs.deadZone * 100).round()} %',
                        prefs.deadZone * 100,
                        0,
                        30,
                        (v) => prefs.update((p) => p.deadZone = v / 100),
                      ),
                      _row(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('Stop when I let go', style: sans(15)),
                                const SizedBox(height: 2),
                                Text(
                                  'dead-man hold, always on',
                                  style: sans(12, color: Ar.muted),
                                ),
                              ],
                            ),
                            const Opacity(
                              opacity: 0.8,
                              child: Toggle(on: true, locked: true),
                            ),
                          ],
                        ),
                      ),
                      _row(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text('Haptic ticks', style: sans(15)),
                            Toggle(
                              on: prefs.haptics,
                              label: 'Haptic ticks',
                              onChanged: (v) =>
                                  prefs.update((p) => p.haptics = v),
                            ),
                          ],
                        ),
                      ),
                      _row(
                        last: true,
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text('Left-handed', style: sans(15)),
                            Toggle(
                              on: prefs.leftHanded,
                              label: 'Left-handed',
                              onChanged: (v) =>
                                  prefs.update((p) => p.leftHanded = v),
                            ),
                          ],
                        ),
                      ),
                    ]),
                    const SizedBox(height: 18),
                    _card([
                      _link(
                        context,
                        'Body health',
                        trailing: const Text(
                          '›',
                          style: TextStyle(color: Ar.faint),
                        ),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => HealthScreen(link: link),
                          ),
                        ),
                      ),
                      _link(
                        context,
                        'Voice & history',
                        trailing: const Text(
                          '›',
                          style: TextStyle(color: Ar.faint),
                        ),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) =>
                                VoiceScreen(link: link, motion: motion),
                          ),
                        ),
                      ),
                      _link(
                        context,
                        'Disconnect',
                        last: true,
                        trailing: Text(link.host, style: mono(12)),
                        onTap: () {
                          Navigator.of(context).popUntil((r) => r.isFirst);
                          link.disconnect();
                        },
                      ),
                    ]),
                    const SizedBox(height: 22),
                    Center(
                      child: Text(
                        'everything stays on your network',
                        style: mono(11, color: Ar.faint),
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

  Widget _moodTabs() => Container(
    padding: const EdgeInsets.all(4),
    decoration: BoxDecoration(
      color: Ar.raised,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(
      children: [
        for (final m in link.modes)
          Expanded(
            child: Semantics(
              button: true,
              selected: link.mode == m,
              label: _cap(m),
              excludeSemantics: true,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => link.setMode(m),
                child: Container(
                  height: 40,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: link.mode == m ? Ar.text : Colors.transparent,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  child: Text(
                    _cap(m),
                    style: sans(
                      14,
                      weight: 500,
                      color: link.mode == m ? Ar.ground : Ar.muted,
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    ),
  );

  Widget _speakerRow(BuildContext context) {
    final names = ['guest', ...link.people.map((p) => p.name)];
    Person? me;
    for (final p in link.people) {
      if (p.name == link.speaker) me = p;
    }
    final roast = me == null ? 'no' : (me.roastOk ? 'yes' : 'no');
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        border: Border.all(color: Ar.line),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Talking to', style: sans(15)),
              const SizedBox(height: 2),
              Text('roast allowed: $roast', style: sans(12, color: Ar.muted)),
            ],
          ),
          PopupMenuButton<String>(
            tooltip: 'Who is talking',
            color: Ar.raised,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: const BorderSide(color: Ar.line),
            ),
            onSelected: link.setSpeaker,
            itemBuilder: (_) => [
              for (final n in names)
                PopupMenuItem<String>(
                  value: n,
                  child: Text(n, style: sans(14)),
                ),
            ],
            child: Container(
              height: 36,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                border: Border.all(color: Ar.line),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(link.speaker, style: sans(14)),
                  const SizedBox(width: 6),
                  const AIcon(
                    Shapes.chevronDown,
                    size: 14,
                    color: Ar.muted,
                    stroke: 1.8,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _card(List<Widget> rows) => Container(
    decoration: BoxDecoration(
      border: Border.all(color: Ar.line),
      borderRadius: BorderRadius.circular(14),
    ),
    clipBehavior: Clip.antiAlias,
    child: Column(children: rows),
  );

  Widget _row({required Widget child, bool last = false}) => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      border: last ? null : const Border(bottom: BorderSide(color: Ar.line)),
    ),
    child: child,
  );

  Widget _slider(
    String label,
    String value,
    double v,
    double min,
    double max,
    ValueChanged<double> onChanged,
  ) => _row(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: sans(15)),
            Text(value, style: mono(12, color: Ar.textDim)),
          ],
        ),
        SliderTheme(
          data: SliderThemeData(
            trackHeight: 3,
            activeTrackColor: Ar.text,
            inactiveTrackColor: Ar.lineSoft,
            thumbColor: Ar.text,
            overlayColor: Colors.transparent,
            thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 9),
            trackShape: const RoundedRectSliderTrackShape(),
          ),
          child: Slider(
            value: v.clamp(min, max),
            min: min,
            max: max,
            onChanged: onChanged,
          ),
        ),
      ],
    ),
  );

  Widget _link(
    BuildContext context,
    String label, {
    required Widget trailing,
    required VoidCallback onTap,
    bool last = false,
  }) => GestureDetector(
    behavior: HitTestBehavior.opaque,
    onTap: onTap,
    child: _row(
      last: last,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: sans(15)),
          trailing,
        ],
      ),
    ),
  );
}

/// The design's 44x26 switch.
class Toggle extends StatelessWidget {
  const Toggle({
    super.key,
    required this.on,
    this.onChanged,
    this.label,
    this.locked = false,
  });
  final bool on;
  final ValueChanged<bool>? onChanged;
  final String? label;
  final bool locked;

  @override
  Widget build(BuildContext context) {
    final knobColor = locked ? Ar.text : (on ? Ar.ground : Ar.faint);
    final bg = locked ? Ar.faint : (on ? Ar.text : Colors.transparent);
    return Semantics(
      toggled: on,
      label: label,
      excludeSemantics: label != null,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onChanged == null ? null : () => onChanged!(!on),
        child: SizedBox(
          width: 44,
          height: 44,
          child: Center(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 120),
              width: 44,
              height: 26,
              decoration: BoxDecoration(
                color: bg,
                borderRadius: BorderRadius.circular(13),
                border: on || locked ? null : Border.all(color: Ar.lineHard),
              ),
              child: AnimatedAlign(
                duration: const Duration(milliseconds: 120),
                alignment: on ? Alignment.centerRight : Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 3),
                  child: Container(
                    width: 20,
                    height: 20,
                    decoration: BoxDecoration(
                      color: knobColor,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
