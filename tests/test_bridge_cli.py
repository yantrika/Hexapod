"""Step 5 checks: the bridge_cli line parser and one end-to-end run."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

import config
from bridge import Status
from commandline import format_status, parse_line

# stop leaves the body holding: sit settles the feet first, then lowers
SIT_WAIT_S = config.SETTLE_S + config.SIT_STAND_TRANSITION_S + 0.7
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("stand", ("stand", {})),
        ("  SIT  ", ("sit", {})),
        ("wave", ("wave", {})),
        ("stop", ("stop", {})),
        ("walk", ("walk", {"direction": "fwd", "speed": 0.5})),
        ("walk fwd 0.5", ("walk", {"direction": "fwd", "speed": 0.5})),
        ("walk back 1", ("walk", {"direction": "back", "speed": 1.0})),
        ("turn left", ("turn", {"direction": "left", "angle_deg": 90.0})),
        ("turn right 45", ("turn", {"direction": "right", "angle_deg": 45.0})),
        ("", None),
        ("dance", None),
        ("walk sideways", None),
        ("walk fwd fast", None),
        ("strafe left", ("walk", {"strafe": 1.0, "speed": 0.5})),
        ("strafe right 0.8", ("walk", {"strafe": -1.0, "speed": 0.8})),
        ("strafe", None),
        ("strafe up", None),
        ("turn", None),
        ("turn up 10", None),
        ("stand now", None),
    ],
)
def test_parse_line(line: str, expected: tuple[str, dict] | None) -> None:
    assert parse_line(line) == expected


def test_format_status() -> None:
    text = format_status(Status("rejected", 7, {"reason": "invalid_state"}, 1, 0.0))
    assert "rejected" in text and "ref=7" in text and "reason=invalid_state" in text


def test_cli_end_to_end_headless() -> None:
    process = subprocess.Popen(
        [sys.executable, "scripts/bridge_cli.py", "--headless"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None
    for line in ("walk fwd 0.5", "bogus", "stop", "sit", "quit"):
        process.stdin.write(line + "\n")
        process.stdin.flush()
        time.sleep(SIT_WAIT_S if line == "sit" else 0.5)
    out, _ = process.communicate(timeout=30)
    assert process.returncode == 0
    assert "<- accepted" in out and "not a command" in out
    assert "<- done" in out and "action=stop" in out and "action=sit" in out
