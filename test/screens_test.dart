import 'package:arya_controller/drive_mode.dart';
import 'package:arya_controller/drive_screen.dart';
import 'package:arya_controller/health_screen.dart';
import 'package:arya_controller/landscape_screen.dart';
import 'package:arya_controller/motion.dart';
import 'package:arya_controller/pair_screen.dart';
import 'package:arya_controller/prefs.dart';
import 'package:arya_controller/robot_link.dart';
import 'package:arya_controller/settings_screen.dart';
import 'package:arya_controller/theme.dart';
import 'package:arya_controller/voice_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

RobotLink _connected() {
  final link = RobotLink()
    ..state = LinkState.connected
    ..host = 'hexa-pi'
    ..robotState = 'idle'
    ..hasPersona = true
    ..people = const [Person(1, 'Deepak', 3, false)]
    ..speaker = 'Deepak';
  link.log
    ..add(
      VoiceLine(
        fromYou: true,
        text: 'Arya, walk forward a bit',
        at: DateTime(2026, 10, 16, 14, 2, 18),
      )..route = 'walk',
    )
    ..add(
      VoiceLine(
        fromYou: false,
        text: 'Walking forward.',
        at: DateTime(2026, 10, 16, 14, 2, 19),
      ),
    )
    ..add(
      VoiceLine(
        fromYou: true,
        text: 'How hot are you right now?',
        at: DateTime(2026, 10, 16, 14, 2, 31),
      )..route = 'chat',
    )
    ..add(
      VoiceLine(
        fromYou: false,
        text: 'Comfortable, thanks for asking.',
        at: DateTime(2026, 10, 16, 14, 2, 33),
      ),
    );
  return link;
}

Future<void> _shoot(
  WidgetTester t,
  Widget screen,
  String name, {
  Size size = const Size(390, 844),
}) async {
  t.view.physicalSize = size;
  t.view.devicePixelRatio = 1;
  addTearDown(t.view.reset);
  await t.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: aryaTheme(),
      home: screen,
    ),
  );
  await t.pump(const Duration(milliseconds: 300));
  await expectLater(
    find.byType(MaterialApp),
    matchesGoldenFile('goldens/$name.png'),
  );
}

void main() {
  setUpAll(() async {
    // `flutter test` does not load the app's fonts by itself; without them every glyph is a block.
    for (final (family, file) in const [
      (sansFamily, 'fonts/InstrumentSans.ttf'),
      (monoFamily, 'fonts/JetBrainsMono.ttf'),
    ]) {
      final loader = FontLoader(family)..addFont(rootBundle.load(file));
      await loader.load();
    }
  });

  testWidgets('pair', (t) async {
    await _shoot(t, PairScreen(link: RobotLink()), 'pair');
  });

  for (final mode in DriveMode.values) {
    testWidgets('drive ${mode.name}', (t) async {
      final link = _connected();
      final prefs = Prefs()..defaultMode = mode;
      await _shoot(
        t,
        DriveScreen(link: link, prefs: prefs, motion: MotionInput(link)),
        'drive_${mode.name}',
      );
    });
  }

  testWidgets('voice', (t) async {
    final link = _connected();
    await _shoot(
      t,
      VoiceScreen(link: link, motion: MotionInput(link)),
      'voice',
    );
  });

  testWidgets('health', (t) async {
    await _shoot(t, HealthScreen(link: _connected()), 'health');
  });

  testWidgets('settings', (t) async {
    final link = _connected();
    await _shoot(
      t,
      SettingsScreen(link: link, prefs: Prefs(), motion: MotionInput(link)),
      'settings',
    );
  });

  testWidgets('landscape', (t) async {
    final link = _connected();
    await _shoot(
      t,
      LandscapeScreen(link: link, prefs: Prefs(), motion: MotionInput(link)),
      'landscape',
      size: const Size(844, 390),
    );
  });
}
