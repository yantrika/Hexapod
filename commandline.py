"""Text commands for the manual front ends (``bridge_cli`` and the control window).

``parse_line`` is the one parser both use: it turns a typed line into the
``(action, params)`` of a bridge command, and nothing else. ``format_status`` is the
one-line rendering of a status message.
"""

from __future__ import annotations

from typing import Any

from bridge import Status

DEFAULT_SPEED = 0.5
DEFAULT_TURN_DEG = 90.0
POSTURES = ("stand", "sit", "wave", "stop", "heartbeat")
HELP = (
    "stand | sit | wave | stop | walk [fwd|back] [speed] | strafe left|right [speed] | "
    "turn left|right [degrees]"
)


def parse_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """Turn a typed line into ``(action, params)``; None if it is not a command.

    ``walk [fwd|back] [speed]``, ``strafe left|right [speed]`` (a continuous walk
    sideways), ``turn left|right [degrees]``, or a bare posture word. Range checks
    are the body's job, not the parser's.
    """
    words = line.lower().split()
    if not words:
        return None
    action, args = words[0], words[1:]
    try:
        if action in POSTURES and not args:
            return action, {}
        if action == "walk" and len(args) <= 2:
            direction = args[0] if args else "fwd"
            speed = float(args[1]) if len(args) > 1 else DEFAULT_SPEED
            if direction in ("fwd", "back"):
                return "walk", {"direction": direction, "speed": speed}
        if action == "strafe" and 1 <= len(args) <= 2 and args[0] in ("left", "right"):
            speed = float(args[1]) if len(args) > 1 else DEFAULT_SPEED
            return "walk", {"strafe": 1.0 if args[0] == "left" else -1.0, "speed": speed}
        if action == "turn" and 1 <= len(args) <= 2 and args[0] in ("left", "right"):
            angle = float(args[1]) if len(args) > 1 else DEFAULT_TURN_DEG
            return "turn", {"direction": args[0], "angle_deg": angle}
    except ValueError:
        return None
    return None


def format_status(status: Status) -> str:
    ref = "-" if status.ref_seq is None else str(status.ref_seq)
    detail = " ".join(f"{k}={v}" for k, v in status.detail.items())
    return f"<- {status.status:<9} ref={ref:<4} {detail}".rstrip()
