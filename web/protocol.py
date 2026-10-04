"""Messages from the phone page: strict validation and the bridge parameters they become.

Pure functions, no I/O and no clock. The page may send only ``walk`` (one combined message for
every held move button), ``stop``, ``stand``, ``sit`` and ``wave``; since Step 12b also
``ptt_press`` / ``ptt_release`` (hold-to-talk with the robot's microphone) and ``say`` (typed
text). Those three never become bridge messages. Numbers are clamped to [-1, 1]; anything
else (an unknown action or field, a bool, a string, NaN, a nested value, bad JSON) is rejected
and never reaches the bridge. The page never sends joint data.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Any

import config

POSTURE_ACTIONS = ("stand", "sit", "wave")
PTT_ACTIONS = ("ptt_press", "ptt_release")
CLIENT_ACTIONS = ("walk", "stop", *POSTURE_ACTIONS, *PTT_ACTIONS, "say")
_WALK_FIELDS = frozenset({"action", "forward", "strafe", "yaw", "speed"})
_SAY_FIELDS = frozenset({"action", "text"})


class ProtocolError(ValueError):
    """A message the server refuses; ``str(error)`` is the short reason sent back."""


@dataclass(frozen=True)
class Request:
    """A validated request. For ``walk``: the clamped axes and speed."""

    action: str
    forward: float = 0.0  # +1 forward, -1 back
    strafe: float = 0.0  # +1 left
    yaw: float = 0.0  # +1 counter-clockwise (turn left)
    speed: float = config.WEB_WALK_SPEED
    text: str = ""  # for ``say``: stripped, 1 to WEB_SAY_MAX_CHARS characters

    @property
    def moving(self) -> bool:
        return self.action == "walk" and bool(self.forward or self.strafe or self.yaw)


def _reject_constant(name: str) -> Any:
    raise ProtocolError(f"not a number: {name}")


def _number(message: dict[str, Any], key: str, low: float, high: float, default: float) -> float:
    value = message.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{key} must be a number")
    if not math.isfinite(value):
        raise ProtocolError(f"{key} must be finite")
    return float(config.clamp(float(value), low, high))


def _say_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ProtocolError("text must be a string")
    text = " ".join(value.split())  # trims and folds every kind of whitespace, newlines too
    if not text:
        raise ProtocolError("text is empty")
    if len(text) > config.WEB_SAY_MAX_CHARS:
        raise ProtocolError(f"text is longer than {config.WEB_SAY_MAX_CHARS} characters")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn") for char in text):
        raise ProtocolError("text has control characters")
    return text


def parse_message(raw: str | bytes) -> Request:
    """Validate one client message. Raises ``ProtocolError`` with the reason."""
    if len(raw) > config.WEB_MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    try:
        message = json.loads(raw, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as error:  # bad JSON, bad UTF-8
        raise ProtocolError("malformed JSON") from error
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    action = message.get("action")
    if not isinstance(action, str) or action not in CLIENT_ACTIONS:
        raise ProtocolError("unknown action")
    if action == "say":
        if not set(message) <= _SAY_FIELDS:
            raise ProtocolError("unknown field")
        return Request("say", text=_say_text(message.get("text")))
    if action != "walk":
        if set(message) != {"action"}:
            raise ProtocolError("unknown field")
        return Request(action)
    if not set(message) <= _WALK_FIELDS:
        raise ProtocolError("unknown field")
    return Request(
        "walk",
        forward=_number(message, "forward", -1.0, 1.0, 0.0),
        strafe=_number(message, "strafe", -1.0, 1.0, 0.0),
        yaw=_number(message, "yaw", -1.0, 1.0, 0.0),
        speed=_number(message, "speed", 0.0, config.SPEED_MAX, config.WEB_WALK_SPEED),
    )


def walk_params(request: Request) -> dict[str, Any]:
    """Params of the one bridge ``walk`` for *request* (the same shape ``control_logic`` sends).

    ``forward`` becomes ``direction`` (its sign); ``strafe`` and ``yaw`` pass through (the
    controller scales and clamps them). No axis at all gives a zero walk, which ramps a walk
    down to a halt.
    """
    if not request.moving:
        return {"strafe": 0.0, "yaw": 0.0, "speed": request.speed}
    params: dict[str, Any] = {"speed": request.speed}
    if request.forward:
        params["direction"] = "fwd" if request.forward > 0 else "back"
    if request.strafe:
        params["strafe"] = request.strafe
    if request.yaw:
        params["yaw"] = request.yaw
    return params
