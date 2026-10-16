import 'dart:async';
import 'dart:convert';

import 'package:arya_controller/motion.dart';
import 'package:arya_controller/robot_link.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// A stand-in for the robot's phone server: records what the app sends, lets a test push
/// what the robot would send.
class FakeRobot with StreamChannelMixin<dynamic> implements WebSocketChannel {
  final _incoming = StreamController<dynamic>();
  final sent = <Map<String, dynamic>>[];
  late final _sink = _FakeSink(sent);

  void push(Map<String, Object?> message) => _incoming.add(jsonEncode(message));

  @override
  Future<void> get ready => Future.value();
  @override
  Stream get stream => _incoming.stream;
  @override
  WebSocketSink get sink => _sink;
  @override
  String? get protocol => null;
  @override
  int? get closeCode => null;
  @override
  String? get closeReason => null;
}

class _FakeSink implements WebSocketSink {
  _FakeSink(this.sent);
  final List<Map<String, dynamic>> sent;

  @override
  void add(dynamic data) =>
      sent.add(jsonDecode(data as String) as Map<String, dynamic>);
  @override
  void addError(Object error, [StackTrace? stackTrace]) {}
  @override
  Future<void> addStream(Stream<dynamic> stream) async {}
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {}
  @override
  Future<void> get done => Future.value();
}

Future<(RobotLink, FakeRobot, List<Uri>)> _connect() async {
  final robot = FakeRobot();
  final uris = <Uri>[];
  final link = RobotLink(
    open: (uri) {
      uris.add(uri);
      return robot;
    },
  );
  await link.connect('hexa-pi', '123456');
  return (link, robot, uris);
}

Future<void> _settle() => Future<void>.delayed(Duration.zero);

void main() {
  test('connects to /ws with the PIN in the query', () async {
    final (link, _, uris) = await _connect();
    expect(link.connected, isTrue);
    expect(uris.single.toString(), 'ws://hexa-pi:8765/ws?pin=123456');
  });

  test('reads hello: deadman, voice availability and persona', () async {
    final (link, robot, _) = await _connect();
    robot.push({
      'type': 'hello',
      'state': 'standing',
      'send_hz': 10.0,
      'deadman_s': 0.3,
      'ptt': {
        'available': false,
        'reason': 'the server runs with --no-mic',
        'max_s': 10.0,
      },
      'say': {'available': true, 'reason': null, 'max_chars': 120},
      'persona': {
        'type': 'persona',
        'mode': 'funny',
        'modes': ['calm', 'funny', 'roast'],
        'speaker': 'Anna',
        'mood': 'neutral',
        'memory': true,
        'people': [
          {'id': 4, 'name': 'Anna', 'facts': 1, 'roast_ok': true},
        ],
      },
    });
    await _settle();
    expect(link.robotState, 'standing');
    expect(link.pttAvailable, isFalse);
    expect(link.pttReason, 'the server runs with --no-mic');
    expect(link.sayMaxChars, 120);
    expect(link.deadmanS, 0.3);
    expect(link.hasPersona, isTrue);
    expect(link.mode, 'funny');
    expect(link.speaker, 'Anna');
    expect(link.people.single.roastOk, isTrue);
  });

  test('a held move sends walk with clamped axes and the speed; release sends a zero walk', () async {
    final (link, robot, _) = await _connect();
    link.speed = 0.5;
    link.setMove(forward: 1.7, strafe: -0.25);
    expect(robot.sent.last, {
      'action': 'walk',
      'forward': 1.0,
      'strafe': -0.25,
      'yaw': 0.0,
      'speed': 0.5,
    });
    link.releaseMove();
    expect(robot.sent.last['action'], 'walk');
    expect(robot.sent.last['forward'], 0.0);
    expect(robot.sent.last['strafe'], 0.0);
    expect(robot.sent.last['yaw'], 0.0);
    final count = robot.sent.length;
    link.releaseMove(); // already stopped: nothing more is sent
    expect(robot.sent.length, count);
  });

  test(
    'a held move repeats at the robot’s rate so the deadman never fires',
    () async {
      final (link, robot, _) = await _connect();
      link.setMove(forward: 1);
      final first = robot.sent.length;
      await Future<void>.delayed(const Duration(milliseconds: 350));
      expect(robot.sent.length, greaterThanOrEqualTo(first + 2));
      expect(robot.sent.every((m) => m['action'] == 'walk'), isTrue);
      link.releaseMove();
    },
  );

  test(
    'stop, postures, say and persona requests use the protocol’s exact fields',
    () async {
      final (link, robot, _) = await _connect();
      link.stop();
      link.posture('stand');
      link.posture('wave');
      link.say('hello there');
      link.pttPress();
      link.pttRelease();
      link.setMode('roast');
      link.setSpeaker('Anna');
      expect(robot.sent, [
        {'action': 'stop'},
        {'action': 'stand'},
        {'action': 'wave'},
        {'action': 'say', 'text': 'hello there'},
        {'action': 'ptt_press'},
        {'action': 'ptt_release'},
        {'action': 'set_mode', 'mode': 'roast'},
        {'action': 'set_speaker', 'name': 'Anna'},
      ]);
    },
  );

  test('heard, route and said build the voice log', () async {
    final (link, robot, _) = await _connect();
    robot.push({'type': 'heard', 'text': 'walk forward'});
    robot.push({
      'type': 'route',
      'route': 'command',
      'action': 'walk',
      'text': 'walk forward',
      'early_stop': false,
    });
    robot.push({'type': 'said', 'text': 'Walking forward.'});
    robot.push({'type': 'heard', 'text': 'how are you'});
    robot.push({
      'type': 'route',
      'route': 'chat',
      'action': null,
      'text': 'how are you',
      'early_stop': false,
    });
    await _settle();
    expect(link.log.map((l) => (l.fromYou, l.text, l.route)).toList(), [
      (true, 'walk forward', 'walk'),
      (false, 'Walking forward.', null),
      (true, 'how are you', 'chat'),
    ]);
  });

  test('status updates the robot state label', () async {
    final (link, robot, _) = await _connect();
    robot.push({
      'type': 'status',
      'status': 'ok',
      'detail': 'walking',
      'state': 'walking',
    });
    await _settle();
    expect(link.robotState, 'walking');
    expect(link.lastDetail, 'walking');
  });

  test('unknown or malformed messages are ignored', () async {
    final (link, robot, _) = await _connect();
    robot.push({'type': 'something-new'});
    robot._incoming.add('not json');
    robot._incoming.add(42);
    await _settle();
    expect(link.connected, isTrue);
  });

  test('a refused connection reports why', () async {
    final link = RobotLink(
      open: (_) => throw Exception('WebSocketChannelException: 401'),
    );
    await link.connect('hexa-pi', '000000');
    expect(link.connected, isFalse);
    expect(link.error, 'Wrong PIN.');
  });

  test('STOP zeroes every control, not just the last one touched', () async {
    final (link, robot, _) = await _connect();
    final motion = MotionInput(link);
    motion
      ..stickY = 0.8
      ..padYaw = 1
      ..push();
    motion.stop();
    expect(robot.sent.last, {'action': 'stop'});
    expect(motion.forward, 0);
    expect(motion.yaw, 0);
  });

  test('turn right is negative yaw and strafe left is positive, matching the robot', () async {
    final (link, robot, _) = await _connect();
    final motion = MotionInput(link);
    motion
      ..stickX =
          1 // stick pushed right
      ..push();
    expect(
      robot.sent.last['strafe'],
      -1.0,
    ); // right = negative strafe (left is +)
    motion
      ..stickX = 0
      ..turnStick =
          1 // turn stick pushed right
      ..push();
    expect(
      robot.sent.last['yaw'],
      -1.0,
    ); // clockwise = negative (counter-clockwise is +)
  });

  test(
    'losing the connection says the robot stopped and drops to disconnected',
    () async {
      final (link, robot, _) = await _connect();
      await robot._incoming.close();
      await _settle();
      expect(link.connected, isFalse);
      expect(link.error, contains('stopped'));
    },
  );
}
