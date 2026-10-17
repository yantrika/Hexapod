"""Messages from the phone page: strict validation and the bridge parameters they become.

Pure functions, no I/O and no clock. The page may send only ``walk`` (one combined message for
every held move button), ``stop``, ``stand``, ``sit`` and ``wave``; also
``ptt_press`` / ``ptt_release`` (hold-to-talk) and ``say`` (typed text). Those three never
become bridge messages. A ``ptt_press`` may announce phone audio (``"audio": {"rate": 16000,
"channels": 1, "format": "s16le"}``, exactly that); the raw PCM then follows in binary frames,
checked by ``check_audio_frame``. The persona messages are ``set_mode`` (calm,
funny, roast), ``set_speaker`` (a name or "guest"), ``forget_person`` (an id) and
``set_roast_ok`` (an id and a true/false): brain-side persona and memory actions, also never
bridge messages. Numbers are
clamped to [-1, 1]; anything else (an unknown action or field, a bool, a string, NaN, a nested
value, bad JSON) is rejected and never reaches the bridge. The page never sends joint data.
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
PERSONA_ACTIONS = ("set_mode", "set_speaker", "forget_person", "set_roast_ok")
CLIENT_ACTIONS = ("walk", "stop", *POSTURE_ACTIONS, *PTT_ACTIONS, "say", *PERSONA_ACTIONS)
_WALK_FIELDS = frozenset({"action", "forward", "strafe", "yaw", "speed"})
_SAY_FIELDS = frozenset({"action", "text"})
_PRESS_FIELDS = frozenset({"action", "audio"})
_AUDIO_FORMAT = {"rate": config.WEB_AUDIO_RATE, "channels": config.WEB_AUDIO_CHANNELS,
                 "format": config.WEB_AUDIO_FORMAT}
_MODE_FIELDS = frozenset({"action", "mode"})
_SPEAKER_FIELDS = frozenset({"action", "name"})
_FORGET_FIELDS = frozenset({"action", "id"})
_ROAST_OK_FIELDS = frozenset({"action", "id", "roast_ok"})
_MAX_PERSON_ID = 2**31 - 1


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
    mode: str = ""  # for ``set_mode``: one of config.PERSONA_MODES
    name: str = ""  # for ``set_speaker``: a name or "guest" (the server checks that it exists)
    person_id: int = 0  # for ``forget_person`` and ``set_roast_ok``
    roast_ok: bool = False  # for ``set_roast_ok``
    audio: bool = False  # for ``ptt_press``: the audio comes from the phone, in binary frames

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


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise ProtocolError("name must be a string")
    name = " ".join(value.split())
    if not name:
        raise ProtocolError("name is empty")
    if len(name) > config.MEMORY_NAME_MAX_CHARS:
        raise ProtocolError(f"name is longer than {config.MEMORY_NAME_MAX_CHARS} characters")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn") for char in name):
        raise ProtocolError("name has control characters")
    return name


def _person_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("id must be an integer")
    if not 1 <= value <= _MAX_PERSON_ID:
        raise ProtocolError("id is out of range")
    return value


def _audio_format(value: Any) -> None:
    """Accept exactly the one supported format; anything else is a clear error."""
    if (not isinstance(value, dict) or set(value) != set(_AUDIO_FORMAT)
            or any(type(value[key]) is not type(wanted) or value[key] != wanted
                   for key, wanted in _AUDIO_FORMAT.items())):
        raise ProtocolError(
            "unsupported audio format: only rate 16000, channels 1, format s16le")


def check_audio_frame(data: bytes) -> float:
    """Validate one binary audio frame; returns its length in seconds. Raises ``ProtocolError``."""
    if not data:
        raise ProtocolError("audio frame is empty")
    if len(data) % config.WEB_AUDIO_SAMPLE_BYTES:
        raise ProtocolError("audio frame must hold whole 16-bit samples")
    if len(data) > config.WEB_AUDIO_MAX_FRAME_BYTES:
        raise ProtocolError(f"audio frame is longer than {config.WEB_AUDIO_MAX_FRAME_MS} ms")
    return len(data) / (config.WEB_AUDIO_SAMPLE_BYTES * config.WEB_AUDIO_RATE)


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
    if action == "set_mode":
        if not set(message) <= _MODE_FIELDS:
            raise ProtocolError("unknown field")
        mode = message.get("mode")
        if not isinstance(mode, str) or mode not in config.PERSONA_MODES:
            raise ProtocolError("unknown mode")
        return Request("set_mode", mode=mode)
    if action == "set_speaker":
        if not set(message) <= _SPEAKER_FIELDS:
            raise ProtocolError("unknown field")
        return Request("set_speaker", name=_name(message.get("name")))
    if action == "forget_person":
        if not set(message) <= _FORGET_FIELDS:
            raise ProtocolError("unknown field")
        return Request("forget_person", person_id=_person_id(message.get("id")))
    if action == "set_roast_ok":
        if set(message) != _ROAST_OK_FIELDS:
            raise ProtocolError("unknown field" if not set(message) <= _ROAST_OK_FIELDS
                                else "missing field")
        flag = message.get("roast_ok")
        if not isinstance(flag, bool):
            raise ProtocolError("roast_ok must be true or false")
        return Request("set_roast_ok", person_id=_person_id(message.get("id")), roast_ok=flag)
    if action == "ptt_press":
        if not set(message) <= _PRESS_FIELDS:
            raise ProtocolError("unknown field")
        if "audio" not in message:
            return Request("ptt_press")
        _audio_format(message["audio"])
        return Request("ptt_press", audio=True)
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
