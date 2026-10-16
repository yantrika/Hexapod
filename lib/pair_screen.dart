import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'robot_link.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/icons.dart';

/// Address and PIN. The robot prints its 6-digit PIN in its log when the phone server starts.
/// (The robot cannot be found automatically yet, so the address is typed.)
class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.link});
  final RobotLink link;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  static const _pinLength = 6;
  final _host = TextEditingController(text: 'hexa-pi');
  final _pin = TextEditingController();
  final _pinFocus = FocusNode();

  @override
  void initState() {
    super.initState();
    _pin.addListener(() => setState(() {}));
    _pinFocus.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _host.dispose();
    _pin.dispose();
    _pinFocus.dispose();
    super.dispose();
  }

  void _connect() {
    final host = _host.text.trim();
    if (host.isEmpty || _pin.text.length < _pinLength) return;
    FocusScope.of(context).unfocus();
    widget.link.connect(host, _pin.text);
  }

  @override
  Widget build(BuildContext context) {
    final link = widget.link;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 460),
            child: ListenableBuilder(
              listenable: link,
              builder: (context, _) {
                final busy = link.state == LinkState.connecting;
                final ready =
                    _pin.text.length == _pinLength &&
                    _host.text.trim().isNotEmpty;
                return SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(22, 28, 22, 24),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 36),
                      const AIcon(
                        Shapes.hexagon,
                        size: 40,
                        color: Ar.text,
                        stroke: 1.2,
                      ),
                      const SizedBox(height: 10),
                      Text(
                        'Find your\nrobot',
                        style: sans(
                          34,
                          weight: 500,
                          height: 1.1,
                          spacing: -0.34,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text(
                        'Arya works without the internet. Your phone and the robot only need the same Wi-Fi.',
                        style: sans(
                          15,
                          color: const Color(0xFF9A9CA3),
                          height: 1.5,
                        ),
                      ),
                      const SizedBox(height: 22),
                      _addressPanel(),
                      const SizedBox(height: 22),
                      const CapLabel('PIN SHOWN ON THE ROBOT’S LOG'),
                      const SizedBox(height: 12),
                      _pinBoxes(),
                      const SizedBox(height: 12),
                      Text(
                        'Only one phone can drive at a time.',
                        style: sans(13, color: Ar.muted, height: 1.5),
                      ),
                      if (link.error != null) ...[
                        const SizedBox(height: 14),
                        Text(link.error!, style: sans(14, color: Ar.signal)),
                      ],
                      const SizedBox(height: 28),
                      GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onTap: busy ? null : _connect,
                        child: Opacity(
                          opacity: ready && !busy ? 1 : 0.5,
                          child: Container(
                            height: 56,
                            alignment: Alignment.center,
                            decoration: BoxDecoration(
                              color: Ar.text,
                              borderRadius: BorderRadius.circular(16),
                            ),
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Text(
                                  busy ? 'Connecting…' : 'Connect',
                                  style: sans(
                                    16,
                                    weight: 500,
                                    color: Ar.ground,
                                  ),
                                ),
                                if (!busy) ...[
                                  const SizedBox(width: 10),
                                  const AIcon(
                                    Shapes.right,
                                    size: 16,
                                    color: Ar.ground,
                                    stroke: 1.8,
                                  ),
                                ],
                              ],
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  Widget _addressPanel() => Container(
    decoration: BoxDecoration(
      border: Border.all(color: Ar.line),
      borderRadius: BorderRadius.circular(16),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: Ar.line)),
          ),
          child: const CapLabel('ROBOT ADDRESS'),
        ),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            color: Ar.raised,
            borderRadius: const BorderRadius.vertical(
              bottom: Radius.circular(16),
            ),
          ),
          child: Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: const BoxDecoration(
                  color: Ar.faint,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: TextField(
                  controller: _host,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  enableSuggestions: false,
                  cursorColor: Ar.text,
                  style: sans(16, weight: 500),
                  onChanged: (_) => setState(() {}),
                  decoration: InputDecoration(
                    hintText: 'hexa-pi or 192.168.1.42',
                    hintStyle: sans(16, color: Ar.faint),
                    border: InputBorder.none,
                    contentPadding: const EdgeInsets.symmetric(vertical: 18),
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _pinBoxes() {
    final text = _pin.text;
    return Stack(
      children: [
        Row(
          children: [
            for (var i = 0; i < _pinLength; i++) ...[
              if (i > 0) const SizedBox(width: 8),
              Expanded(
                child: Container(
                  height: 60,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: Ar.raised,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(
                      color: i < text.length
                          ? Ar.text
                          : (i == text.length && _pinFocus.hasFocus
                                ? Ar.signal
                                : Ar.line),
                    ),
                  ),
                  child: Text(
                    i < text.length ? text[i] : '',
                    style: mono(24, color: Ar.text),
                  ),
                ),
              ),
            ],
          ],
        ),
        // One real text field laid invisibly over the boxes, so paste and the keyboard work.
        Positioned.fill(
          child: Opacity(
            opacity: 0,
            child: TextField(
              controller: _pin,
              focusNode: _pinFocus,
              keyboardType: TextInputType.number,
              maxLength: _pinLength,
              autofillHints: const [AutofillHints.oneTimeCode],
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              onSubmitted: (_) => _connect(),
              decoration: const InputDecoration(counterText: ''),
            ),
          ),
        ),
      ],
    );
  }
}
