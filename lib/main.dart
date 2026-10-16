import 'package:flutter/material.dart';

import 'drive_screen.dart';
import 'pair_screen.dart';
import 'robot_link.dart';
import 'theme.dart';

void main() => runApp(const AryaApp());

class AryaApp extends StatefulWidget {
  const AryaApp({super.key});

  @override
  State<AryaApp> createState() => _AryaAppState();
}

class _AryaAppState extends State<AryaApp> with WidgetsBindingObserver {
  final _link = RobotLink();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _link.dispose();
    super.dispose();
  }

  /// Leaving the app stops the robot (the server also stops it when the socket closes).
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) _link.stop();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Arya',
      debugShowCheckedModeBanner: false,
      theme: aryaTheme(),
      home: ListenableBuilder(
        listenable: _link,
        builder: (context, _) => _link.connected
            ? DriveScreen(link: _link)
            : PairScreen(link: _link),
      ),
    );
  }
}
