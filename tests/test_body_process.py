"""Step 5 checks with real spawned body processes (PyBullet DIRECT), kept light.

Wall-clock bound tests carry ``@pytest.mark.timing`` (run them on a quiet machine with
``pytest -m timing``). One body is shared by most tests (spawning costs about a second);
tests that need a fresh body (tipping over, shutdown, parent death) start their own.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import config
from body.process import BodyProbe, BodyProcess
from bridge import Bridge, Command, Status, make_bridge, new_command

WALK: dict[str, Any] = {"direction": "fwd", "speed": 0.5}
ROOT = Path(__file__).resolve().parent.parent


class Body:
    """A running body plus helpers to talk to it like the brain would."""

    def __init__(self, tip: bool = False) -> None:
        self.bridge: Bridge = make_bridge()
        self.probe = BodyProbe()
        self.tip_event = mp.get_context("spawn").Event() if tip else None
        self.process = BodyProcess(self.bridge, True, self.probe, self.tip_event)
        self.log: list[Status] = []

    def start(self) -> Body:
        self.process.start()
        assert self.process.wait_ready(), "body did not become ready"
        return self

    def send(self, action: str, **params: object) -> Command:
        command = new_command(action, params)
        self.bridge.send(command)
        return command

    def wait(self, match: Callable[[Status], bool], timeout: float = 5.0) -> Status:
        """Collect statuses until one matches (all are appended to ``self.log``)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.bridge.receive(timeout=0.02)
            if status is None:
                continue
            self.log.append(status)
            if match(status):
                return status
        raise AssertionError(f"no matching status in {timeout} s; got {self.log[-6:]}")

    def answer(self, command: Command, kind: str | None = None, timeout: float = 5.0) -> Status:
        """Wait for the status answering *command* (optionally of a given kind)."""
        return self.wait(
            lambda s: s.ref_seq == command.seq and (kind is None or s.status == kind), timeout
        )

    def collect(self, seconds: float) -> list[Status]:
        end = time.monotonic() + seconds
        out = []
        while time.monotonic() < end:
            status = self.bridge.receive(timeout=0.02)
            if status is not None:
                out.append(status)
        return out

    def to_standing(self) -> None:
        """Bring the body to a clean standing state from wherever the last test left it."""
        self.send("stop")
        self.collect(0.2)
        command = self.send("stand")
        self.wait(lambda s: s.ref_seq == command.seq and s.status in ("done", "rejected"), 8.0)
        self.collect(0.1)

    def close(self) -> int | None:
        return self.process.shutdown()


@pytest.fixture(scope="module")
def shared() -> Iterator[Body]:
    body = Body().start()
    yield body
    body.close()


@pytest.fixture
def body(shared: Body) -> Body:
    shared.to_standing()
    shared.bridge.receive_all()
    return shared


@pytest.fixture
def fresh() -> Iterator[Callable[..., Body]]:
    started: list[Body] = []

    def make(tip: bool = False) -> Body:
        started.append(Body(tip).start())
        return started[-1]

    yield make
    for item in started:
        item.close()


def _pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_blas_threads_are_pinned_in_the_child(shared: Body) -> None:
    assert shared.probe.get("blas_threads") == 1.0


def test_walk_then_stop(body: Body) -> None:
    walk = body.send("walk", **WALK)
    assert body.wait(lambda s: s.ref_seq == walk.seq).status == "accepted"
    time.sleep(0.4)
    stop = body.send("stop")
    done = body.wait(lambda s: s.ref_seq == stop.seq and s.status == "done")
    assert done.detail["action"] == "stop"


@pytest.mark.timing
def test_stop_with_a_full_queue_stops_within_the_bound(body: Body) -> None:
    body.send("walk", **WALK)
    body.wait(lambda s: s.status == "accepted")
    times = []
    for _ in range(5):
        body.to_standing()
        body.send("walk", **WALK)
        body.wait(lambda s: s.status == "accepted")
        for _ in range(config.COMMAND_QUEUE_MAXSIZE * 2):  # the queue is now full of walks
            body.send("walk", **WALK)
        started = time.monotonic()
        stop = body.send("stop")
        body.answer(stop, "done")
        times.append(time.monotonic() - started)
    print(f"stop with a full queue -> done: median {statistics.median(times) * 1000:.0f} ms, "
          f"worst {max(times) * 1000:.0f} ms (bound {config.BODY_TEST_STOP_MAX_S * 1000:.0f} ms)")
    assert max(times) < config.BODY_TEST_STOP_MAX_S


@pytest.mark.timing
def test_stop_event_alone_stops_the_body_and_is_answered(body: Body) -> None:
    body.send("walk", **WALK)
    body.wait(lambda s: s.status == "accepted")
    time.sleep(0.3)
    started = time.monotonic()
    with body.bridge.stop_seq.get_lock():  # the queue message is withheld: only the event
        body.bridge.stop_seq.value = 424242
    body.bridge.stop_event.set()
    done = body.wait(lambda s: s.ref_seq == 424242 and s.status == "done")
    elapsed = time.monotonic() - started
    print(f"stop_event alone -> done: {elapsed * 1000:.0f} ms")
    assert done.detail["source"] == "stop_event"
    assert elapsed < config.BODY_TEST_STOP_MAX_S


def _flood(bridge: Bridge, stop: threading.Event) -> None:
    while not stop.is_set():
        bridge.send(new_command("walk", WALK))


@pytest.mark.timing
def test_flooding_the_queue_does_not_slow_the_body_tick(body: Body) -> None:
    body.send("walk", **WALK)
    body.wait(lambda s: s.status == "accepted")

    def measure(seconds: float, flood: bool) -> tuple[int, float, float]:
        done = threading.Event()
        thread = threading.Thread(target=_flood, args=(body.bridge, done))
        body.probe.reset_window()
        if flood:
            thread.start()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not flood:
                body.send("heartbeat")
            body.bridge.receive_all()  # a brain that listens; the flood is the stress
            time.sleep(0.05)
        done.set()
        if flood:
            thread.join()
        return body.probe.window()

    measure(0.5, flood=False)  # warm up
    idle = measure(2.0, flood=False)
    flooded = measure(2.0, flood=True)
    print(f"body tick work, walking: idle mean {idle[1] * 1000:.2f} ms (max {idle[2] * 1000:.2f}) "
          f"over {idle[0]} ticks; flooded mean {flooded[1] * 1000:.2f} ms "
          f"(max {flooded[2] * 1000:.2f}) over {flooded[0]} ticks")
    assert flooded[1] < idle[1] * config.BODY_TEST_FLOOD_TICK_RATIO
    assert flooded[0] > 0.8 * idle[0]  # and the loop kept its rate


@pytest.mark.timing
def test_a_stalled_brain_does_not_stall_the_body(body: Body) -> None:
    """Nobody reads statuses: the status queue fills and drops, the body keeps ticking."""
    body.probe.reset_window()
    for _ in range(config.STATUS_QUEUE_MAXSIZE * 2):
        body.send("sit" if _ % 2 else "stand")  # each is answered; never read
        time.sleep(0.01)
    ticks, mean, _ = body.probe.window()
    assert ticks > 50 and mean < 0.5 / config.CONTROL_HZ


def test_stale_motion_command_is_dropped(body: Body) -> None:
    old = Command("walk", dict(WALK), 9_000_001, time.monotonic() - config.MAX_MESSAGE_AGE_S - 0.3)
    body.bridge.send(old)
    status = body.wait(lambda s: s.ref_seq == old.seq)
    assert (status.status, status.detail["reason"]) == ("rejected", "stale")


def test_stale_stop_is_honoured(body: Body) -> None:
    body.send("walk", **WALK)
    body.wait(lambda s: s.status == "accepted")
    time.sleep(0.4)
    old = Command("stop", {}, 9_000_002, time.monotonic() - 10.0)
    body.bridge.send(old)
    done = body.wait(lambda s: s.ref_seq == old.seq and s.status == "done")
    assert done.detail["action"] == "stop"


def test_stale_heartbeats_are_silent_and_do_not_feed_the_watchdog(body: Body) -> None:
    body.send("walk", **WALK)
    body.wait(lambda s: s.status == "accepted")
    seen: list[Status] = []
    end = time.monotonic() + config.WATCHDOG_TIMEOUT_S + 2.0
    while time.monotonic() < end:
        body.bridge.send(Command("heartbeat", {}, 9_000_003, time.monotonic() - 5.0))
        status = body.bridge.receive(timeout=0.1)
        if status is not None:
            seen.append(status)
    assert [s.status for s in seen] == ["done"]  # only the watchdog speaks; no heartbeat reply
    assert seen[0].detail["reason"] == "watchdog"


def test_watchdog_stops_a_walk_without_heartbeats(body: Body) -> None:
    walk = body.send("walk", **WALK)
    body.wait(lambda s: s.ref_seq == walk.seq)
    started = time.monotonic()
    done = body.wait(lambda s: s.status == "done", config.WATCHDOG_TIMEOUT_S + 3.0)
    print(f"watchdog: walk -> done after {time.monotonic() - started:.2f} s without heartbeat")
    assert done.ref_seq == walk.seq
    assert done.detail == {"action": "walk", "reason": "watchdog"}


def test_heartbeats_keep_a_walk_alive(body: Body) -> None:
    body.send("walk", **WALK)
    statuses = []
    end = time.monotonic() + config.WATCHDOG_TIMEOUT_S * 2.5
    while time.monotonic() < end:
        body.send("heartbeat")
        statuses += body.collect(1.0 / config.HEARTBEAT_HZ)
    assert [s.status for s in statuses] == ["accepted"]


def test_walk_while_sitting_is_rejected_and_busy_during_a_transition(body: Body) -> None:
    sit = body.send("sit")
    assert body.wait(lambda s: s.ref_seq == sit.seq).status == "accepted"
    walk = body.send("walk", **WALK)  # mid-transition
    assert body.wait(lambda s: s.ref_seq == walk.seq).status == "busy"
    body.wait(lambda s: s.ref_seq == sit.seq and s.status == "done", 8.0)
    walk = body.send("walk", **WALK)
    rejected = body.wait(lambda s: s.ref_seq == walk.seq)
    assert (rejected.status, rejected.detail["reason"]) == ("rejected", "invalid_state")


@pytest.mark.pybullet
def test_fallen_is_emitted_exactly_once_when_tipped(fresh: Callable[..., Body]) -> None:
    body = fresh(tip=True)
    assert body.tip_event is not None
    body.tip_event.set()
    fallen = body.wait(lambda s: s.status == "fallen")
    assert fallen.ref_seq is None and fallen.detail["tilt_deg"] > config.FALL_TILT_DEG
    walk = body.send("walk", **WALK)  # at once: the sim robot rights itself within about a second
    rejected = body.wait(lambda s: s.ref_seq == walk.seq)
    assert rejected.detail["reason"] == "fallen"
    later = body.collect(1.5)
    assert [s for s in later if s.status == "fallen"] == []


def test_clean_shutdown_leaves_no_child_processes(fresh: Callable[..., Body]) -> None:
    body = fresh()
    pid = body.process.pid
    assert pid is not None and body.process.alive
    assert body.close() == 0
    assert _pid_gone(pid)
    assert [p for p in mp.active_children() if p.pid == pid] == []


def test_the_body_exits_when_its_parent_dies(tmp_path: Path) -> None:
    script = tmp_path / "parent.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from body.process import BodyProcess
        from bridge import make_bridge
        if __name__ == "__main__":  # spawn re-imports this file in the child
            body = BodyProcess(make_bridge())
            body.start()
            assert body.wait_ready()
            print(body.pid, flush=True)
            os._exit(0)  # the parent dies without any cleanup
    """))
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    pid = int(next(line for line in result.stdout.split() if line.isdigit()))  # pybullet chats
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not _pid_gone(pid):
        time.sleep(0.1)
    if not _pid_gone(pid):
        os.kill(pid, 9)
        pytest.fail("the orphaned body process is still running")


@pytest.mark.timing
def test_latency_from_walk_to_first_foot_target_change(body: Body) -> None:
    samples = []
    for _ in range(20):
        body.to_standing()
        body.bridge.receive_all()
        before = body.probe.get("change_time")
        sent = time.monotonic()
        walk = body.send("walk", **WALK)
        accepted = body.answer(walk)
        accept_s = time.monotonic() - sent
        deadline = time.monotonic() + 2.0
        while body.probe.get("change_time") <= max(before, sent) and time.monotonic() < deadline:
            time.sleep(0.001)
        move_s = body.probe.get("change_time") - sent
        assert accepted.status == "accepted"
        samples.append((accept_s, move_s))
    accept = [a for a, _ in samples]
    move = [m for _, m in samples]
    print(f"walk -> accepted:           median {statistics.median(accept) * 1000:.0f} ms, "
          f"worst {max(accept) * 1000:.0f} ms")
    print(f"walk -> first target change: median {statistics.median(move) * 1000:.0f} ms, "
          f"worst {max(move) * 1000:.0f} ms (20 runs)")
    assert statistics.median(accept) < config.BODY_TEST_ACCEPT_MAX_S
    assert max(move) < 3 * config.BODY_TEST_ACCEPT_MAX_S


@pytest.mark.timing
def test_idle_tick_cost_is_reported(body: Body) -> None:
    body.probe.reset_window()
    time.sleep(2.0)
    ticks, mean, worst = body.probe.window()
    print(f"body tick work, standing idle: mean {mean * 1000:.2f} ms, max {worst * 1000:.2f} ms, "
          f"{ticks / 2.0:.1f} ticks/s")
    assert mean < 0.5 / config.CONTROL_HZ


def _pose(body: Body) -> tuple[float, float, float]:
    return body.probe.get("base_x"), body.probe.get("base_y"), body.probe.get("base_yaw")


def _walk_with_heartbeats(body: Body, seconds: float, **params: Any) -> None:
    body.send("walk", **params)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        body.send("heartbeat")
        body.collect(1.0 / config.HEARTBEAT_HZ)


@pytest.mark.timing
def test_a_strafe_walk_moves_the_body_sideways_and_stops_on_stop_event(body: Body) -> None:
    time.sleep(0.5)  # settled
    x0, y0, yaw0 = _pose(body)
    _walk_with_heartbeats(body, 4.0, strafe=1.0, speed=1.0)  # left
    x1, y1, yaw1 = _pose(body)
    left, forward = y1 - y0, x1 - x0
    print(f"strafe left 4 s: sideways {left * 100:+.1f} cm, forward {forward * 100:+.1f} cm, "
          f"heading change {abs(yaw1 - yaw0) * 57.3:.1f} deg")
    assert left > 0.25  # +y is left; 0.5 s ramp, then 0.1 m/s
    assert abs(forward) < 0.25 * left and abs(yaw1 - yaw0) < 0.1

    with body.bridge.stop_seq.get_lock():  # stop_event alone: the queue message is withheld
        body.bridge.stop_seq.value = 515151
    body.bridge.stop_event.set()
    body.wait(lambda s: s.ref_seq == 515151 and s.status == "done")
    time.sleep(0.5)  # the hold pose settles
    x2, y2, _ = _pose(body)
    time.sleep(1.0)
    x3, y3, _ = _pose(body)
    assert abs(y3 - y2) < 0.01 and abs(x3 - x2) < 0.01  # at rest


@pytest.mark.timing
def test_old_style_walk_still_goes_straight_ahead(body: Body) -> None:
    time.sleep(0.5)
    x0, y0, _ = _pose(body)
    _walk_with_heartbeats(body, 3.0, direction="fwd", speed=1.0)
    x1, y1, _ = _pose(body)
    assert x1 - x0 > 0.08 and abs(y1 - y0) < 0.25 * (x1 - x0)
