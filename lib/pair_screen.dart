import 'package:flutter/material.dart';

import 'robot_link.dart';
import 'theme.dart';

/// Address and PIN. The robot prints its PIN (6 digits) in its log when the phone server starts.
class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.link});
  final RobotLink link;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  final _host = TextEditingController(text: '192.168.1.');
  final _pin = TextEditingController();

  @override
  void dispose() {
    _host.dispose();
    _pin.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final link = widget.link;
    final busy = link.state == LinkState.connecting;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 28),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.hexagon_outlined, size: 40, color: Ar.text),
                  const SizedBox(height: 14),
                  const Text(
                    'Find your\nrobot',
                    style: TextStyle(
                      fontSize: 34,
                      fontWeight: FontWeight.w500,
                      height: 1.1,
                    ),
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'Arya works without the internet. Your phone and the robot only need the same Wi-Fi.',
                    style: TextStyle(
                      fontSize: 15,
                      height: 1.5,
                      color: Ar.muted,
                    ),
                  ),
                  const SizedBox(height: 28),
                  Text('ROBOT ADDRESS', style: mono(10, spacing: 0.8)),
                  const SizedBox(height: 8),
                  _field(_host, 'hexa-pi or 192.168.1.42', TextInputType.url),
                  const SizedBox(height: 18),
                  Text(
                    'PIN FROM THE ROBOT’S LOG',
                    style: mono(10, spacing: 0.8),
                  ),
                  const SizedBox(height: 8),
                  _field(_pin, '6 digits', TextInputType.number, obscure: true),
                  const SizedBox(height: 10),
                  Text(
                    'Only one phone can drive at a time.',
                    style: const TextStyle(fontSize: 13, color: Ar.muted),
                  ),
                  if (link.error != null) ...[
                    const SizedBox(height: 14),
                    Text(
                      link.error!,
                      style: const TextStyle(fontSize: 14, color: Ar.signal),
                    ),
                  ],
                  const SizedBox(height: 28),
                  SizedBox(
                    width: double.infinity,
                    height: 56,
                    child: FilledButton(
                      onPressed: busy
                          ? null
                          : () => link.connect(
                              _host.text.trim(),
                              _pin.text.trim(),
                            ),
                      style: FilledButton.styleFrom(
                        backgroundColor: Ar.text,
                        foregroundColor: Ar.ground,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                        textStyle: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      child: Text(busy ? 'Connecting…' : 'Connect'),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _field(
    TextEditingController c,
    String hint,
    TextInputType type, {
    bool obscure = false,
  }) {
    return TextField(
      controller: c,
      keyboardType: type,
      obscureText: obscure,
      style: mono(18, color: Ar.text),
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: mono(16, color: Ar.faint),
        filled: true,
        fillColor: Ar.raised,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 18,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: Ar.line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: Ar.text),
        ),
      ),
    );
  }
}
