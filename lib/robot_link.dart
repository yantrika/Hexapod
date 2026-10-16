import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

enum LinkState { disconnected, connecting, connected }

/// The phone's side of the robot's phone-page protocol (`web/protocol.py` on `main`).
///
/// The robot only ever gets high-level requests: walk axes in [-1, 1], stop, stand, sit,
/// wave, ptt, say and persona actions. The server enforces the deadman (`stop` when no move
/// message arrives within `deadman_s`), so a held move is repeated at `send_hz`.
class RobotLink extends ChangeNotifier {
  static const int port = 8765;

  LinkState state = LinkState.disconnected;
  String? error;
  String robotState = 'idle'; // the server's label, from `hello` and `status`
  String lastDetail = '';
  int? pingMs;

  double _sendHz = 10;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _moveTimer;

  double _forward = 0, _strafe = 0, _yaw = 0;
  double speed = 0.5; // normalised 0..1, like `config.SPEED_MAX`
  bool _wasMoving = false;

  bool get connected => state == LinkState.connected;

  Future<void> connect(String host, String pin) async {
    await disconnect();
    state = LinkState.connecting;
    error = null;
    notifyListeners();
    try {
      final uri = Uri.parse(
        'ws://$host:$port/ws?pin=${Uri.encodeQueryComponent(pin)}',
      );
      final channel = WebSocketChannel.connect(uri);
      await channel.ready.timeout(const Duration(seconds: 6));
      _channel = channel;
      state = LinkState.connected;
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
      _send({'action': 'stop'});
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
    final Object? message = jsonDecode(raw);
    if (message is! Map<String, dynamic>) return;
    switch (message['type']) {
      case 'hello':
        _sendHz = (message['send_hz'] as num?)?.toDouble() ?? _sendHz;
        robotState = (message['state'] as String?) ?? robotState;
      case 'status':
        robotState = (message['state'] as String?) ?? robotState;
        lastDetail = (message['detail'] as String?) ?? '';
      default:
        return;
    }
    notifyListeners();
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

  void setMode(String mode) => _send({'action': 'set_mode', 'mode': mode});

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
