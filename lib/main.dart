import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'drive_screen.dart';
import 'motion.dart';
import 'pair_screen.dart';
import 'prefs.dart';
import 'robot_link.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Ar.ground,
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );
  runApp(const AryaApp());
}

class AryaApp extends StatefulWidget {
  const AryaApp({super.key});

  @override
  State<AryaApp> createState() => _AryaAppState();
}

class _AryaAppState extends State<AryaApp> with WidgetsBindingObserver {
  final _link = RobotLink();
  final _prefs = Prefs();
  late final _motion = MotionInput(_link);
  final _navigator = GlobalKey<NavigatorState>();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _link.addListener(() {
      if (!_link.connected) _navigator.currentState?.popUntil((r) => r.isFirst);
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _motion.dispose();
    _prefs.dispose();
    _link.dispose();
    super.dispose();
  }

  /// Leaving the app stops the robot (the server also stops it when the socket closes).
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) _motion.stop();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Arya',
      navigatorKey: _navigator,
      debugShowCheckedModeBanner: false,
      theme: aryaTheme(),
      home: ListenableBuilder(
        listenable: _link,
        builder: (context, _) => _link.connected
            ? DriveScreen(link: _link, prefs: _prefs, motion: _motion)
            : PairScreen(link: _link),
      ),
    );
  }
}
