import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

enum LinkState { disconnected, connecting, connected }

/// One line of the voice log: what the person said, what Arya did with it, what Arya said.
class VoiceLine {
  VoiceLine({required this.fromYou, required this.text, required this.at});

  final bool fromYou;
  String text;
  final DateTime at;
  String? route; // for `fromYou` lines: "walk fwd", "chat", ...
}

/// One person Arya remembers (from the `persona` event).
class Person {
  const Person(this.id, this.name, this.facts, this.roastOk);
  final int id;
  final String name;
  final int facts;
  final bool roastOk;
}

/// The phone's side of the robot's phone-page protocol (`web/protocol.py` on `main`).
///
/// The robot only ever gets high-level requests: walk axes in [-1, 1], stop, stand, sit,
/// wave, ptt, say and persona actions. The server enforces the deadman (`stop` when no move
/// message arrives within `deadman_s`), so a held move is repeated at `send_hz`.
class RobotLink extends ChangeNotifier {
  RobotLink({WebSocketChannel Function(Uri uri)? open})
    : _open = open ?? WebSocketChannel.connect;

  final WebSocketChannel Function(Uri uri) _open;

  static const int port = 8765;
  static const int _maxLog = 60;

  LinkState state = LinkState.disconnected;
  String? error;
  String host = '';
  String robotState = 'idle'; // the server's label, from `hello` and `status`
  String lastDetail = '';

  // from `hello`
  bool pttAvailable = true;
  String? pttReason;
  bool sayAvailable = true;
  String? sayReason;
  int sayMaxChars = 120;
  double deadmanS = 0.3; // the robot stops a moving walk if no move message arrives for this long

  // from the `persona` event (null: this robot has no persona)
  String mode = 'calm';
  List<String> modes = const ['calm', 'funny', 'roast'];
  String speaker = 'guest';
  List<Person> people = const [];
  bool hasPersona = false;

  final List<VoiceLine> log = [];

  double _sendHz = 10;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _moveTimer;

  double _forward = 0, _strafe = 0, _yaw = 0;
  double speed = 0.5; // normalised 0..1, like `config.SPEED_MAX`
  bool _wasMoving = false;

  bool get connected => state == LinkState.connected;

  Future<void> connect(String address, String pin) async {
    await disconnect();
    state = LinkState.connecting;
    error = null;
    host = address;
    notifyListeners();
    try {
      final uri = Uri.parse(
        'ws://$address:$port/ws?pin=${Uri.encodeQueryComponent(pin)}',
      );
      final channel = _open(uri);
      await channel.ready.timeout(const Duration(seconds: 6));
      _channel = channel;
      state = LinkState.connected;
      log.clear();
      _subscription = channel.stream.listen(
        _onMessage,
        onDone: _onClosed,
        onError: (_) => _onClosed(),
      );
      _moveTimer = Timer.periodic(
        Duration(milliseconds: (1000 / _sendHz).round()),
        (_) => _tick(),
      );
    } on TimeoutException {
      _fail('The robot did not answer. Same Wi-Fi?');
    } catch (e) {
      final text = e.toString();
      _fail(
        text.contains('401')
            ? 'Wrong PIN.'
            : text.contains('429')
            ? 'Too many wrong PINs. Wait a minute.'
            : text.contains('1013') || text.contains('503')
            ? 'Another phone is already driving.'
            : 'Could not connect.',
      );
    }
    notifyListeners();
  }

  Future<void> disconnect() async {
    _moveTimer?.cancel();
    _moveTimer = null;
    await _subscription?.cancel();
    _subscription = null;
    final channel = _channel;
    _channel = null;
    if (channel != null) {
      channel.sink.add(jsonEncode({'action': 'stop'}));
      await channel.sink.close();
    }
    _forward = _strafe = _yaw = 0;
    _wasMoving = false;
    state = LinkState.disconnected;
    notifyListeners();
  }

  void _fail(String message) {
    error = message;
    state = LinkState.disconnected;
  }

  void _onClosed() {
    _moveTimer?.cancel();
    _moveTimer = null;
    _channel = null;
    if (state == LinkState.connected) {
      error = 'Connection lost. The robot stopped.';
    }
    state = LinkState.disconnected;
    notifyListeners();
  }

  void _onMessage(dynamic raw) {
    if (raw is! String) return;
    final Object? message;
    try {
      message = jsonDecode(raw);
    } on FormatException {
      return;
    }
    if (message is! Map<String, dynamic>) return;
    switch (message['type']) {
      case 'hello':
        _sendHz = (message['send_hz'] as num?)?.toDouble() ?? _sendHz;
        deadmanS = (message['deadman_s'] as num?)?.toDouble() ?? deadmanS;
        robotState = (message['state'] as String?) ?? robotState;
        final ptt = message['ptt'];
        if (ptt is Map<String, dynamic>) {
          pttAvailable = ptt['available'] == true;
          pttReason = ptt['reason'] as String?;
        }
        final say = message['say'];
        if (say is Map<String, dynamic>) {
          sayAvailable = say['available'] == true;
          sayReason = say['reason'] as String?;
          sayMaxChars = (say['max_chars'] as num?)?.toInt() ?? sayMaxChars;
        }
        final persona = message['persona'];
        if (persona is Map<String, dynamic>) _onPersona(persona);
      case 'status':
        robotState = (message['state'] as String?) ?? robotState;
        lastDetail = (message['detail'] as String?) ?? '';
      case 'persona':
        _onPersona(message);
      case 'heard':
        _addLine(true, (message['text'] as String?) ?? '');
      case 'route':
        _onRoute(message);
      case 'said':
        _addLine(false, (message['text'] as String?) ?? '');
      case 'deadman' || 'ptt_timeout' || 'error':
        break; // the page just keeps going; the robot already stopped
      default:
        return;
    }
    notifyListeners();
  }

  void _onPersona(Map<String, dynamic> p) {
    hasPersona = true;
    mode = (p['mode'] as String?) ?? mode;
    speaker = (p['speaker'] as String?) ?? speaker;
    final list = p['modes'];
    if (list is List) modes = list.whereType<String>().toList();
    final folks = p['people'];
    if (folks is List) {
      people = [
        for (final f in folks.whereType<Map<String, dynamic>>())
          Person(
            (f['id'] as num).toInt(),
            (f['name'] as String?) ?? '?',
            (f['facts'] as num?)?.toInt() ?? 0,
            f['roast_ok'] == true,
          ),
      ];
    }
  }

  void _onRoute(Map<String, dynamic> m) {
    final route = (m['route'] as String?) ?? '';
    final action = m['action'] as String?;
    final label = route == 'command' && action != null
        ? action
        : (route == 'chat' ? 'chat' : route);
    for (var i = log.length - 1; i >= 0; i--) {
      if (log[i].fromYou) {
        log[i].route = label;
        return;
      }
    }
  }

  void _addLine(bool fromYou, String text) {
    if (text.isEmpty) return;
    log.add(VoiceLine(fromYou: fromYou, text: text, at: DateTime.now()));
    if (log.length > _maxLog) log.removeAt(0);
  }

  // --- motion ---------------------------------------------------------------------------

  /// Axes in [-1, 1]: forward +, strafe left +, yaw counter-clockwise (turn left) +.
  void setMove({double forward = 0, double strafe = 0, double yaw = 0}) {
    _forward = forward.clamp(-1.0, 1.0);
    _strafe = strafe.clamp(-1.0, 1.0);
    _yaw = yaw.clamp(-1.0, 1.0);
    _tick(force: true);
  }

  void releaseMove() => setMove();

  void _tick({bool force = false}) {
    final moving = _forward != 0 || _strafe != 0 || _yaw != 0;
    if (moving || (_wasMoving && force)) {
      _send({
        'action': 'walk',
        'forward': _round(_forward),
        'strafe': _round(_strafe),
        'yaw': _round(_yaw),
        'speed': speed,
      });
    }
    _wasMoving = moving;
  }

  double _round(double v) => (v * 100).round() / 100;

  void stop() {
    _forward = _strafe = _yaw = 0;
    _wasMoving = false;
    _send({'action': 'stop'});
  }

  void posture(String action) {
    assert(action == 'stand' || action == 'sit' || action == 'wave');
    _send({'action': action});
  }

  void say(String text) => _send({'action': 'say', 'text': text});

  void pttPress() => _send({'action': 'ptt_press'});

  void pttRelease() => _send({'action': 'ptt_release'});

  void setMode(String value) {
    mode = value; // shown at once; the robot's `persona` event confirms it
    notifyListeners();
    _send({'action': 'set_mode', 'mode': value});
  }

  void setSpeaker(String name) {
    speaker = name;
    notifyListeners();
    _send({'action': 'set_speaker', 'name': name});
  }

  void _send(Map<String, Object?> message) {
    final channel = _channel;
    if (channel == null) return;
    channel.sink.add(jsonEncode(message));
  }

  @override
  void dispose() {
    _moveTimer?.cancel();
    _subscription?.cancel();
    _channel?.sink.close();
    super.dispose();
  }
}
