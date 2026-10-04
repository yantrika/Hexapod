"""Step 6 end to end: typed text -> router -> bridge -> a real headless body (PyBullet DIRECT)."""

from __future__ import annotations

import math
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import config
from body.process import BodyProbe, BodyProcess
from brain.router import route
from bridge import Status, make_bridge
from scripts.brain_cli import BrainLoop, describe

ROOT = Path(__file__).resolve().parent.parent


class Rig:
    def __init__(self, max_walk_s: float = config.VOICE_WALK_MAX_S) -> None:
        self.bridge = make_bridge()
        self.probe = BodyProbe()
        self.body = BodyProcess(self.bridge, True, self.probe)
        self.brain = BrainLoop(self.bridge, max_walk_s=max_walk_s)
        self.log: list[Status] = []

    def start(self) -> Rig:
        self.body.start()
        assert self.body.wait_ready()
        return self

    def pump(self, seconds: float) -> list[Status]:
        end = time.monotonic() + seconds
        seen: list[Status] = []
        while time.monotonic() < end:
            seen += self.brain.pump()
            time.sleep(0.02)
        self.log += seen
        return seen

    def until(self, match: Callable[[Status], bool], timeout: float) -> Status:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for status in self.pump(0.05):
                if match(status):
                    return status
        raise AssertionError(f"timed out; saw {self.log[-5:]}")

    def pose(self) -> tuple[float, float]:
        return self.probe.get("base_x"), self.probe.get("base_y")


@pytest.fixture
def rig() -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def make(**kwargs: float) -> Rig:
        made.append(Rig(**kwargs).start())
        return made[-1]

    yield make
    for item in made:
        item.brain.close()
        item.body.shutdown()


def test_walk_forward_keeps_walking_without_more_input_then_stop_halts(
    rig: Callable[..., Rig],
) -> None:
    r = rig()
    r.pump(0.3)
    result = r.brain.handle_text("walk forward")
    assert (result.kind, result.action, result.phrase) == ("command", "walk", "walk forward")
    r.until(lambda s: s.status == "accepted", 3.0)
    assert r.brain.keeper.active
    seen = r.pump(config.WATCHDOG_TIMEOUT_S * 3.5)  # several seconds, no further typing
    assert [s.status for s in seen] == [], seen  # no done/watchdog: the heartbeats held it
    stop = r.brain.handle_text("stop!")
    assert stop.kind == "stop" and not r.brain.keeper.active
    done = r.until(lambda s: s.status == "done", 3.0)
    assert done.detail["action"] == "stop"


@pytest.mark.timing
def test_walk_forward_actually_moves_the_body_forward(rig: Callable[..., Rig]) -> None:
    r = rig()
    r.pump(0.5)
    x0, y0 = r.pose()
    r.brain.handle_text("walk forward")
    r.pump(4.0)
    r.brain.handle_text("halt")
    r.until(lambda s: s.status == "done", 3.0)
    x1, y1 = r.pose()
    print(f"walk forward by text for 4 s: forward {(x1 - x0) * 100:.1f} cm, "
          f"sideways {(y1 - y0) * 100:+.1f} cm")
    assert x1 - x0 > 0.08 and abs(y1 - y0) < 0.25 * (x1 - x0)


def test_without_a_stop_the_walk_ends_at_the_max_duration(rig: Callable[..., Rig]) -> None:
    max_walk_s = 2.0
    r = rig(max_walk_s=max_walk_s)
    r.pump(0.3)
    r.brain.handle_text("walk forward")
    r.until(lambda s: s.status == "accepted", 3.0)
    accepted_at = time.monotonic()
    done = r.until(lambda s: s.status == "done", max_walk_s + config.WATCHDOG_TIMEOUT_S + 4.0)
    held_for = time.monotonic() - accepted_at
    print(f"walk with max {max_walk_s:.0f} s: body reported done after {held_for:.2f} s")
    assert done.detail == {"action": "walk", "reason": "watchdog"}
    assert held_for >= max_walk_s  # the keeper never let it lapse early
    assert not r.brain.keeper.active


@pytest.mark.pybullet
def test_turn_left_is_kept_alive_and_finishes_its_angle(rig: Callable[..., Rig]) -> None:
    """Regression: a turn takes about 5 s, far past the 1 s watchdog, so it needs heartbeats."""
    r = rig()
    r.pump(0.3)
    yaw0 = r.probe.get("base_yaw")
    assert r.brain.handle_text("turn left").action == "turn"
    r.until(lambda s: s.status == "accepted", 3.0)
    done = r.until(lambda s: s.status == "done", 15.0)
    turned = math.degrees(r.probe.get("base_yaw") - yaw0)
    print(f"turn left: done {done.detail}, turned {turned:+.1f} deg")
    assert done.detail == {"action": "turn"}  # not reason=watchdog
    assert abs(turned - config.TURN_DEFAULT_ANGLE_DEG) < 20.0
    assert not r.brain.keeper.active


def test_chat_text_sends_nothing_to_the_body(rig: Callable[..., Rig]) -> None:
    r = rig()
    result = r.brain.handle_text("I sat down for lunch")
    assert result.is_chat
    assert describe(result).startswith("[chat] I sat down for lunch")
    assert r.pump(0.5) == []


def test_sit_then_walk_is_rejected_and_not_kept_alive(rig: Callable[..., Rig]) -> None:
    r = rig()
    r.brain.handle_text("sit down")
    r.until(lambda s: s.status == "done", config.SIT_STAND_TRANSITION_S + 4.0)
    r.brain.handle_text("walk forward")
    rejected = r.until(lambda s: s.status == "rejected", 3.0)
    assert rejected.detail["reason"] == "invalid_state"
    assert not r.brain.keeper.active


def test_stop_is_sent_through_the_bridge_without_waiting_for_the_keeper(
    rig: Callable[..., Rig],
) -> None:
    r = rig()
    r.brain.handle_text("walk forward")
    r.until(lambda s: s.status == "accepted", 3.0)
    started = time.monotonic()
    r.brain.handle_text("freeze")  # no pump in between: handle_text itself sends it
    done = r.until(lambda s: s.status == "done", 2.0)
    assert done.detail["action"] == "stop" and time.monotonic() - started < 1.0


def test_describe_lines() -> None:
    walk_line = describe(route("walk forward"))
    assert describe(route("stop")).startswith("route: STOP <- 'stop'")
    assert "walk" in walk_line and "score 100" in walk_line
    assert "(nearest" in describe(route("I sat down"))


def test_brain_cli_end_to_end_headless() -> None:
    process = subprocess.Popen(
        [sys.executable, "scripts/brain_cli.py", "--headless"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None
    for line, wait in (("walk forward", 2.5), ("what is a wave function", 0.3), ("stop", 0.7),
                       ("quit", 0.0)):
        process.stdin.write(line + "\n")
        process.stdin.flush()
        time.sleep(wait)
    out, _ = process.communicate(timeout=30)
    assert process.returncode == 0
    assert "route: walk" in out and "<- accepted" in out
    assert "[chat] what is a wave function" in out
    assert "route: STOP" in out and "action=stop" in out
    assert "reason=watchdog" not in out  # the keeper held the walk for the 2.5 s
