"""Step 12a: validation of the phone page's messages and the walk parameters they become."""

from __future__ import annotations

import json

import pytest

import config
from bridge import Command, validate_command
from web.protocol import ProtocolError, Request, parse_message, walk_params


def message(**fields: object) -> str:
    return json.dumps(fields)


@pytest.mark.parametrize(("raw", "expected"), [
    (message(action="stop"), Request("stop")),
    (message(action="stand"), Request("stand")),
    (message(action="sit"), Request("sit")),
    (message(action="wave"), Request("wave")),
    (message(action="walk"), Request("walk")),
    (message(action="walk", forward=1), Request("walk", forward=1.0)),
    (message(action="walk", forward=-1, yaw=1, speed=0.8),
     Request("walk", forward=-1.0, yaw=1.0, speed=0.8)),
    (message(action="walk", strafe=0.25, yaw=-0.5), Request("walk", strafe=0.25, yaw=-0.5)),
    (b'{"action": "stop"}', Request("stop")),  # a binary frame is fine
    # out of range numbers are CLAMPED, not trusted
    (message(action="walk", forward=5, strafe=-9, yaw=1e9),
     Request("walk", forward=1.0, strafe=-1.0, yaw=1.0)),
    (message(action="walk", speed=7), Request("walk", speed=config.SPEED_MAX)),
    (message(action="walk", speed=-3), Request("walk", speed=0.0)),
])
def test_valid_messages(raw: str | bytes, expected: Request) -> None:
    assert parse_message(raw) == expected


@pytest.mark.parametrize("raw", [
    "",                                                  # empty
    "not json", "{", "[1, 2]", "null", "42", '"walk"',   # malformed or not an object
    "\xff\xfe".encode("latin-1"),                        # not UTF-8
    "{}", message(action=None), message(action=1), message(action=["walk"]),
    message(action="jump"), message(action="heartbeat"),  # unknown action
    message(action="walk ", forward=1), message(action="WALK"),
    message(action="stop", extra=1), message(action="stand", forward=1),  # unknown field
    message(action="walk", angle=3), message(action="walk", direction="fwd"),
    message(action="walk", joints=[0, 0, 0]),             # the page never sends raw joint data
    message(action="walk", forward="1"), message(action="walk", forward=None),
    message(action="walk", forward=True), message(action="walk", yaw=[1]),
    message(action="walk", strafe={"x": 1}),
    '{"action": "walk", "forward": NaN}', '{"action": "walk", "yaw": Infinity}',
    '{"action": "walk", "yaw": -Infinity}',
    message(action="walk", pad="x" * (config.WEB_MAX_MESSAGE_BYTES + 1)),  # too large
    "[" * 5000,                                           # deep nesting must not crash
])
def test_rejected_messages(raw: str | bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_message(raw)


def test_a_failed_validation_names_a_reason() -> None:
    with pytest.raises(ProtocolError, match="unknown action"):
        parse_message(message(action="fly"))
    with pytest.raises(ProtocolError, match="unknown field"):
        parse_message(message(action="stop", x=1))


@pytest.mark.parametrize(("request_", "expected"), [
    (Request("walk", forward=1.0), {"speed": 0.5, "direction": "fwd"}),
    (Request("walk", forward=-1.0), {"speed": 0.5, "direction": "back"}),
    # forward + turn held together: ONE message with direction and yaw
    (Request("walk", forward=1.0, yaw=1.0), {"speed": 0.5, "direction": "fwd", "yaw": 1.0}),
    (Request("walk", forward=1.0, strafe=-1.0, yaw=-1.0),
     {"speed": 0.5, "direction": "fwd", "strafe": -1.0, "yaw": -1.0}),
    (Request("walk", strafe=1.0), {"speed": 0.5, "strafe": 1.0}),
    (Request("walk", yaw=-1.0, speed=0.3), {"speed": 0.3, "yaw": -1.0}),
    (Request("walk"), {"strafe": 0.0, "yaw": 0.0, "speed": 0.5}),  # released: ramp to a halt
])
def test_walk_params(request_: Request, expected: dict) -> None:
    assert walk_params(request_) == expected


@pytest.mark.parametrize("request_", [
    Request("walk", forward=1.0, yaw=1.0), Request("walk", strafe=1.0), Request("walk"),
    Request("walk", forward=-1.0, strafe=1.0, yaw=-1.0),
])
def test_walk_params_are_valid_bridge_walks(request_: Request) -> None:
    command = Command("walk", walk_params(request_), 1, 0.0)
    assert validate_command(command) is None


def test_moving_means_any_axis() -> None:
    assert not Request("walk").moving
    assert Request("walk", yaw=0.1).moving
    assert not Request("stop").moving
