"""Step 5 checks: the body core in one process (local queues, fake backend, fake clock)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import config
from body.backend import BasePose
from body.controller import Controller, State
from body.process import BodyRunner
from bridge import Bridge, Command, Status, make_local_bridge
from tests.fakes import FakeBackend, FakeClock

DT = 1.0 / config.CONTROL_HZ
WALK: dict[str, Any] = {"direction": "fwd", "speed": 0.5}


class TiltableBackend(FakeBackend):
    """A fake backend whose body orientation a test can set."""

    def __init__(self) -> None:
        super().__init__()
        self.rpy = np.zeros(3)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), self.rpy.copy())


class Rig:
    def __init__(self, state: State = State.STANDING) -> None:
        self.clock = FakeClock()
        self.backend = TiltableBackend()
        self.bridge: Bridge = make_local_bridge()
        controller = Controller(self.backend, self.clock, initial_state=state)
        self.runner = BodyRunner(self.bridge, self.backend, self.clock, controller)
        self.controller = controller
        self._seq = 0

    def send(self, action: str, *, age: float = 0.0, **params: Any) -> int:
        self._seq += 1
        self.bridge.send(Command(action, dict(params), self._seq, self.clock.now() - age))
        return self._seq

    def tick(self, count: int = 1) -> list[Status]:
        out: list[Status] = []
        for _ in range(count):
            self.clock.advance(DT)
            self.runner.step()
            out += self.bridge.receive_all()
        return out

    def seconds(self, seconds: float, heartbeat: bool = True) -> list[Status]:
        out: list[Status] = []
        for _ in range(round(seconds / DT)):
            if heartbeat:
                self.send("heartbeat")
            out += self.tick()
        return out


def kinds(statuses: list[Status]) -> list[tuple[str, int | None]]:
    return [(s.status, s.ref_seq) for s in statuses]


def test_walk_is_accepted_and_stop_answers_accepted_then_done() -> None:
    rig = Rig()
    walk = rig.send("walk", **WALK)
    assert kinds(rig.tick()) == [("accepted", walk)]
    rig.seconds(0.5)
    stop = rig.send("stop")
    assert kinds(rig.tick()) == [("accepted", stop), ("done", stop)]
    assert rig.controller.state is State.HOLDING


def test_stop_beats_a_walk_in_the_same_batch() -> None:
    rig = Rig()
    walk, stop = rig.send("walk", **WALK), rig.send("stop")
    # the stop fast path answers first; the walk in the same batch is then superseded
    assert kinds(rig.tick()) == [("accepted", stop), ("done", stop), ("rejected", walk)]
    assert rig.controller.state is not State.MOVING


def test_stop_event_alone_stops_within_one_tick_and_is_answered() -> None:
    rig = Rig()
    rig.send("walk", **WALK)
    rig.seconds(0.5)
    assert rig.controller.state is State.MOVING
    rig.bridge.stop_seq.value = 77  # the queue message is withheld: only the event arrives
    rig.bridge.stop_event.set()
    statuses = rig.tick()
    assert rig.controller.state is State.HOLDING
    assert kinds(statuses) == [("accepted", 77), ("done", 77)]
    assert not rig.bridge.stop_event.is_set()


def test_duplicate_stop_event_plus_queue_is_harmless_and_answered_once() -> None:
    rig = Rig()
    rig.send("walk", **WALK)
    rig.seconds(0.4)
    stop = rig.send("stop")  # event and queue message both arrive in one tick
    assert kinds(rig.tick()) == [("accepted", stop), ("done", stop)]
    stop_again = rig.send("stop")
    assert kinds(rig.tick()) == [("accepted", stop_again), ("done", stop_again)]
    assert rig.controller.state is State.HOLDING


def test_stop_survives_a_full_queue() -> None:
    rig = Rig()
    for _ in range(config.COMMAND_QUEUE_MAXSIZE * 2):
        rig.send("walk", **WALK)
    stop = rig.send("stop")
    statuses = rig.tick()
    assert ("done", stop) in kinds(statuses)
    assert rig.controller.state is not State.MOVING


def test_stale_motion_command_is_dropped() -> None:
    rig = Rig()
    old = rig.send("walk", age=config.MAX_MESSAGE_AGE_S + 0.2, **WALK)
    statuses = rig.tick()
    assert [(s.status, s.ref_seq, s.detail["reason"]) for s in statuses] == [
        ("rejected", old, "stale")
    ]
    assert rig.controller.state is State.STANDING


def test_stale_stop_is_honoured() -> None:
    rig = Rig()
    rig.send("walk", **WALK)
    rig.seconds(0.4)
    stop = rig.send("stop", age=10.0)
    assert ("done", stop) in kinds(rig.tick())
    assert rig.controller.state is State.HOLDING


def test_stale_heartbeat_is_dropped_silently() -> None:
    rig = Rig()
    rig.send("heartbeat", age=10.0)
    assert rig.tick() == []


def test_fresh_heartbeat_gets_no_reply_and_keeps_the_watchdog_quiet() -> None:
    rig = Rig()
    rig.send("walk", **WALK)
    statuses = rig.seconds(config.WATCHDOG_TIMEOUT_S * 3)
    assert [s.status for s in statuses] == ["accepted"]
    assert rig.controller.state is State.MOVING


def test_watchdog_stops_a_walking_body_and_reports_it() -> None:
    rig = Rig()
    walk = rig.send("walk", **WALK)
    statuses = rig.seconds(config.WATCHDOG_TIMEOUT_S + 2.0, heartbeat=False)
    done = [s for s in statuses if s.status == "done"]
    assert len(done) == 1
    assert done[0].ref_seq == walk
    assert done[0].detail == {"action": "walk", "reason": "watchdog"}
    assert rig.controller.state is State.STANDING


def test_watchdog_leaves_an_idle_body_alone() -> None:
    rig = Rig()
    assert rig.seconds(config.WATCHDOG_TIMEOUT_S * 2, heartbeat=False) == []


def test_walk_while_sitting_is_rejected_invalid_state() -> None:
    rig = Rig(State.SITTING)
    walk = rig.send("walk", **WALK)
    (status,) = rig.tick()
    assert (status.status, status.ref_seq) == ("rejected", walk)
    assert status.detail["reason"] == "invalid_state"


def test_busy_during_a_posture_transition_then_done() -> None:
    rig = Rig(State.SITTING)
    stand = rig.send("stand")
    assert kinds(rig.tick()) == [("accepted", stand)]
    walk = rig.send("walk", **WALK)
    (busy,) = rig.tick()
    assert (busy.status, busy.ref_seq) == ("busy", walk)
    statuses = rig.seconds(config.SIT_STAND_TRANSITION_S + 0.2)
    assert ("done", stand) in kinds(statuses)


def test_turn_finishes_with_done() -> None:
    rig = Rig()
    turn = rig.send("turn", direction="left", angle_deg=30)
    statuses = rig.seconds(6.0)
    assert kinds(statuses) == [("accepted", turn), ("done", turn)]


def test_invalid_params_are_rejected_without_a_crash() -> None:
    rig = Rig()
    walk = rig.send("walk", direction="sideways", speed=1)
    dance = rig.send("dance")
    reasons = {s.ref_seq: s.detail["reason"] for s in rig.tick()}
    assert reasons == {walk: "invalid_params", dance: "unknown_action"}


def test_already_in_state_is_rejected_with_the_state() -> None:
    rig = Rig()
    sit = rig.send("stand")
    (status,) = rig.tick()
    assert status.detail == {"reason": "already_in_state", "state": "standing"}
    assert status.ref_seq == sit


def test_fallen_is_emitted_once_and_motion_is_then_rejected() -> None:
    rig = Rig()
    rig.backend.rpy = np.array([np.radians(config.FALL_TILT_DEG + 20), 0.0, 0.0])
    statuses = rig.tick(5)
    assert [s.status for s in statuses] == ["fallen"]
    assert statuses[0].ref_seq is None
    walk = rig.send("walk", **WALK)
    (status,) = rig.tick()
    assert (status.status, status.ref_seq, status.detail["reason"]) == ("rejected", walk, "fallen")
    stop = rig.send("stop")
    assert ("done", stop) in kinds(rig.tick())  # stop is never rejected
    rig.backend.rpy = np.zeros(3)  # upright again: motion is allowed, no second "fallen"
    rig.tick()
    walk = rig.send("walk", **WALK)
    assert kinds(rig.tick()) == [("accepted", walk)]


def test_a_failing_command_becomes_an_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()

    def boom(*_: object) -> None:
        raise RuntimeError("servo bus on fire")

    monkeypatch.setattr(rig.controller, "wave", boom)
    wave = rig.send("wave")
    (status,) = rig.tick()
    assert (status.status, status.ref_seq) == ("error", wave)
    assert "servo bus on fire" in status.detail["message"]
    stand = rig.send("walk", **WALK)  # the body keeps running
    assert kinds(rig.tick()) == [("accepted", stand)]


def test_a_failing_tick_reports_error_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig()

    def boom(_: float) -> float:
        raise RuntimeError("backend died")

    monkeypatch.setattr(rig.controller, "tick", boom)
    with pytest.raises(RuntimeError):
        rig.tick()
    (status,) = rig.bridge.receive_all()
    assert status.status == "error" and "backend died" in status.detail["message"]


def test_old_style_walk_messages_are_unchanged_and_strafe_walks_are_accepted() -> None:
    rig = Rig()
    old = rig.send("walk", direction="fwd", speed=1.0)
    assert kinds(rig.tick()) == [("accepted", old)]
    rig.seconds(1.0)
    assert rig.controller.target_velocity.vy == 0.0 and rig.controller.target_velocity.vx > 0
    strafe = rig.send("walk", strafe=1.0, speed=0.5)  # latest-wins replaces the old walk
    assert kinds(rig.tick()) == [("accepted", strafe)]
    rig.seconds(0.2)
    assert rig.controller.target_velocity.vx == 0.0 and rig.controller.target_velocity.vy > 0


def test_a_walk_with_out_of_range_strafe_is_rejected_invalid_params() -> None:
    rig = Rig()
    bad = rig.send("walk", strafe=3.0)
    (status,) = rig.tick()
    assert (status.status, status.ref_seq, status.detail["reason"]) == (
        "rejected", bad, "invalid_params")
    assert rig.controller.state is State.STANDING


def test_latest_wins_between_a_strafe_walk_and_a_turn_walk() -> None:
    rig = Rig()
    first = rig.send("walk", strafe=1.0)
    second = rig.send("walk", yaw=1.0)
    assert kinds(rig.tick()) == [("rejected", first), ("accepted", second)]
    assert rig.controller.target_velocity.vy == 0.0 and rig.controller.target_velocity.yaw_rate > 0
