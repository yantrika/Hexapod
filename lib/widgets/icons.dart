import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

/// The design's stroke icons (24x24 viewBox), as inline SVG so they match it exactly.
class AIcon extends StatelessWidget {
  const AIcon(
    this.shapes, {
    super.key,
    this.size = 18,
    this.color = Colors.white,
    this.stroke = 1.6,
    this.fill = false,
    this.label,
  });

  final String shapes;
  final double size;
  final Color color;
  final double stroke;
  final bool fill;
  final String? label;

  static String _hex(Color c) =>
      '#${(c.toARGB32() & 0xFFFFFF).toRadixString(16).padLeft(6, '0')}';

  @override
  Widget build(BuildContext context) {
    final paint = fill
        ? 'fill="${_hex(color)}"'
        : 'fill="none" stroke="${_hex(color)}" stroke-width="$stroke"';
    return Semantics(
      label: label,
      excludeSemantics: label != null,
      child: SvgPicture.string(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" $paint>$shapes</svg>',
        width: size,
        height: size,
      ),
    );
  }
}

class Shapes {
  static const hexagon =
      '<path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z"/><circle cx="12" cy="12" r="2.4"/>';
  static const sliders =
      '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>';
  static const turnLeft =
      '<path d="M8 5H4v4"/><path d="M4.5 8.5A8 8 0 1 1 6 18"/>';
  static const turnRight =
      '<path d="M16 5h4v4"/><path d="M19.5 8.5A8 8 0 1 0 18 18"/>';
  static const up = '<path d="M12 19V5M6 11l6-6 6 6"/>';
  static const down = '<path d="M12 5v14M6 13l6 6 6-6"/>';
  static const left = '<path d="M19 12H5M11 6l-6 6 6 6"/>';
  static const right = '<path d="M5 12h14M13 6l6 6-6 6"/>';
  static const gauge = '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/>';
  static const mic =
      '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>';
  static const stop = '<rect x="5" y="5" width="14" height="14" rx="2.5"/>';
  static const back = '<path d="M15 6l-6 6 6 6"/>';
  static const chevronDown = '<path d="M6 9l6 6 6-6"/>';
  static const check = '<path d="M20 6L9 17l-5-5"/>';
  static const plus = '<path d="M12 5v14M5 12h14"/>';
  static const battery =
      '<rect x="2" y="7" width="17" height="10" rx="2"/><path d="M21.5 10.5v3"/>';
}
