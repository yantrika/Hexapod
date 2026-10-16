import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';
import 'icons.dart';

/// Active while a finger is on it: `onHold(true)` on press, `onHold(false)` on release or cancel.
///
/// `style` picks one of the design's three looks: `primary` (filled, forward), `soft` (raised
/// fill, the move arrows) and `ghost` (outline only, the turn buttons).
enum HoldStyle { primary, soft, ghost }

class HoldButton extends StatefulWidget {
  const HoldButton({
    super.key,
    required this.shapes,
    required this.label,
    required this.onHold,
    this.height = 84,
    this.width,
    this.iconSize = 24,
    this.iconStroke = 1.8,
    this.radius = 16,
    this.style = HoldStyle.soft,
    this.round = false,
    this.haptics = true,
  });

  final String shapes;
  final String label;
  final void Function(bool held) onHold;
  final double height;
  final double? width;
  final double iconSize;
  final double iconStroke;
  final double radius;
  final HoldStyle style;
  final bool round;
  final bool haptics;

  @override
  State<HoldButton> createState() => _HoldButtonState();
}

class _HoldButtonState extends State<HoldButton> {
  bool _down = false;

  void _set(bool down) {
    if (_down == down) return;
    setState(() => _down = down);
    if (down && widget.haptics) HapticFeedback.selectionClick();
    widget.onHold(down);
  }

  @override
  Widget build(BuildContext context) {
    final primary = widget.style == HoldStyle.primary;
    final ghost = widget.style == HoldStyle.ghost;
    final Color fill = primary
        ? (_down ? Ar.textDim : Ar.text)
        : (ghost ? (_down ? Ar.raised : Colors.transparent) : Ar.raised);
    final Color ink = primary
        ? Ar.ground
        : (ghost && !_down ? Ar.textDim : Ar.text);
    return Semantics(
      button: true,
      label: widget.label,
      excludeSemantics: true,
      child: Listener(
        onPointerDown: (_) => _set(true),
        onPointerUp: (_) => _set(false),
        onPointerCancel: (_) => _set(false),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 80),
          width: widget.width,
          height: widget.height,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: fill,
            shape: widget.round ? BoxShape.circle : BoxShape.rectangle,
            borderRadius: widget.round
                ? null
                : BorderRadius.circular(widget.radius),
            border: primary
                ? null
                : Border.all(color: _down ? Ar.text : Ar.line),
          ),
          child: AIcon(
            widget.shapes,
            size: widget.iconSize,
            color: ink,
            stroke: widget.iconStroke,
          ),
        ),
      ),
    );
  }
}
