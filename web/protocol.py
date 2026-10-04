"""Messages from the phone page: strict validation and the bridge parameters they become.

Pure functions, no I/O and no clock. The page may send only ``walk`` (one combined message for
every held move button), ``stop``, ``stand``, ``sit`` and ``wave``. Numbers are clamped to
[-1, 1]; anything else (an unknown action or field, a bool, a string, NaN, a nested value, bad
JSON) is rejected and never reaches the bridge. The page never sends joint data.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import config

POSTURE_ACTIONS = ("stand", "sit", "wave")
CLIENT_ACTIONS = ("walk", "stop", *POSTURE_ACTIONS)
_WALK_FIELDS = frozenset({"action", "forward", "strafe", "yaw", "speed"})


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
