import 'package:flutter/material.dart';

import '../theme.dart';

/// Active while a finger is on it: `onHold(true)` on press, `onHold(false)` on release or cancel.
class HoldButton extends StatefulWidget {
  const HoldButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onHold,
    this.height = 84,
    this.primary = false,
  });

  final IconData icon;
  final String label;
  final void Function(bool held) onHold;
  final double height;
  final bool primary;

  @override
  State<HoldButton> createState() => _HoldButtonState();
}

class _HoldButtonState extends State<HoldButton> {
  bool _down = false;

  void _set(bool down) {
    if (_down == down) return;
    setState(() => _down = down);
    widget.onHold(down);
  }

  @override
  Widget build(BuildContext context) {
    final fill = widget.primary
        ? Ar.text
        : (_down ? Ar.raised : Colors.transparent);
    final ink = widget.primary ? Ar.ground : Ar.text;
    return Semantics(
      button: true,
      label: widget.label,
      child: Listener(
        onPointerDown: (_) => _set(true),
        onPointerUp: (_) => _set(false),
        onPointerCancel: (_) => _set(false),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 80),
          height: widget.height,
          decoration: BoxDecoration(
            color: _down && widget.primary ? Ar.textDim : fill,
            borderRadius: BorderRadius.circular(16),
            border: widget.primary
                ? null
                : Border.all(color: _down ? Ar.text : Ar.line),
          ),
          child: Icon(widget.icon, color: ink, size: 24),
        ),
      ),
    );
  }
}
