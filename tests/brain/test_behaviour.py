"""The behaviour layer on a fake clock: greetings, questions, small turns, and STOP winning."""

from __future__ import annotations

from typing import Any

import pytest

import config
from brain.behaviour import STANDING, Behaviour
from bridge import Status
from tests.fakes import FakeClock
from vision.events import (
    FACE_BEARING,
    PERSON_APPEARED,
    PERSON_IDENTIFIED,
    PERSON_LEFT,
    PERSON_UNKNOWN,
    VisionEvent,
)


class Rig:
    def __init__(self, tracking: bool = True) -> None:
        self.clock = FakeClock()
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.said: list[str] = []
        self.known: list[str] = []
        self.guest = 0
        self.asked: list[int] = []
        self.idle = True
        self.quiet = True
        self.flow_active = False
        self.greeted = 0
        self.names = {1: "Priya", 2: "Anna"}
        self.behaviour = Behaviour(
            self._send,
            lambda: self.idle, lambda: self.quiet, self, self.names.get, self.known.append,
            self._guest, self._ask, lambda: self.flow_active, self._greeted, clock=self.clock.now,
            tracking=tracking)

    def say(self, text: str) -> int:
        self.said.append(text)
        return 1

    def _send(self, action: str, params: dict[str, Any]) -> None:
        self.actions.append((action, params))

    def _guest(self) -> None:
        self.guest += 1

    def _ask(self, track_id: int) -> bool:
        self.asked.append(track_id)
        return True

    def _greeted(self) -> None:
        self.greeted += 1

    def stand(self) -> None:
        self.behaviour.on_status(Status("done", 1, {"action": "stand"}, 1, 0.0))
        assert self.behaviour.posture == STANDING

    def event(self, kind: str, track: int = 1, **fields: Any) -> None:
        self.behaviour.on_event(VisionEvent(kind, track, time=self.clock.now(), **fields))

    def tick(self, seconds: float = 0.1) -> None:
        self.clock.sleep(seconds)
        self.behaviour.tick()


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_a_recognised_person_is_greeted_by_name_and_becomes_the_speaker(rig: Rig) -> None:
    rig.stand()
    rig.event(PERSON_APPEARED)
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    assert rig.known == ["Priya"] and rig.said == ["Hi Priya!"] and rig.greeted == 1
    assert rig.actions == [("wave", {})]


def test_a_sitting_robot_greets_but_does_not_wave(rig: Rig) -> None:
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    assert rig.said == ["Hi Priya!"] and rig.actions == []


def test_the_same_person_is_not_greeted_again_within_the_cooldown(rig: Rig) -> None:
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    rig.event(PERSON_LEFT)
    rig.event(PERSON_IDENTIFIED, track=2, person_id=1, score=0.7)
    rig.tick()
    assert rig.said == ["Hi Priya!"]
    rig.clock.sleep(config.BEHAVIOUR_GREET_COOLDOWN_S)
    rig.event(PERSON_LEFT, 2)
    rig.event(PERSON_IDENTIFIED, track=3, person_id=1, score=0.7)
    rig.tick()
    assert rig.said == ["Hi Priya!", "Hi Priya!"]


def test_a_greeting_waits_for_a_quiet_idle_robot_and_then_expires(rig: Rig) -> None:
    rig.stand()
    rig.idle = False
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    assert rig.said == [] and rig.known == ["Priya"]  # who it is is known at once, quietly
    rig.idle = True
    rig.tick()
    assert rig.said == ["Hi Priya!"]
    rig.event(PERSON_LEFT)
    rig.quiet = False
    rig.event(PERSON_IDENTIFIED, track=2, person_id=2, score=0.7)
    rig.clock.sleep(config.BEHAVIOUR_PENDING_MAX_S + 1)
    rig.quiet = True
    rig.tick()
    assert rig.said == ["Hi Priya!"]  # Anna's greeting came too late and was dropped


def test_a_stranger_is_asked_once_and_not_again_soon(rig: Rig) -> None:
    rig.event(PERSON_UNKNOWN)
    rig.tick()
    assert rig.asked == [1]
    rig.event(PERSON_UNKNOWN)  # the same track again
    rig.tick()
    rig.event(PERSON_LEFT)
    rig.event(PERSON_UNKNOWN, track=2)
    rig.tick()
    assert rig.asked == [1]  # a different stranger, but inside the cooldown
    rig.clock.sleep(config.BEHAVIOUR_ASK_NAME_COOLDOWN_S)
    rig.event(PERSON_UNKNOWN, track=3)
    rig.tick()
    assert rig.asked == [1, 3]


def test_a_turn_toward_a_face_is_small_and_aimed(rig: Rig) -> None:
    rig.stand()
    rig.event(FACE_BEARING, angle_deg=35.0)
    rig.tick()
    assert rig.actions == [("turn", {"direction": "left", "angle_deg": 20.0})]
    rig.clock.sleep(config.BEHAVIOUR_TURN_COOLDOWN_S)
    rig.event(FACE_BEARING, angle_deg=-14.0)
    rig.tick()
    assert rig.actions[-1] == ("turn", {"direction": "right", "angle_deg": 14.0})


@pytest.mark.parametrize("why", ["near", "stale", "cooldown", "sitting", "busy", "off", "flow"])
def test_no_turn_when(why: str) -> None:
    rig = Rig(tracking=why != "off")
    if why != "sitting":
        rig.stand()
    rig.idle = why != "busy"
    rig.flow_active = why == "flow"
    angle = 5.0 if why == "near" else 30.0
    if why == "cooldown":
        rig.event(FACE_BEARING, angle_deg=30.0)
        rig.tick()
        rig.actions.clear()
    rig.event(FACE_BEARING, angle_deg=angle)
    if why == "stale":
        rig.clock.sleep(config.BEHAVIOUR_BEARING_MAX_AGE_S + 0.5)
    rig.tick()
    assert rig.actions == []


def test_tracking_can_be_switched_on_and_off(rig: Rig) -> None:
    rig.stand()
    rig.behaviour.set_tracking(False)
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick()
    assert rig.actions == []
    rig.behaviour.set_tracking(True)
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick()
    assert len(rig.actions) == 1


def test_a_stop_wins_it_clears_what_waits_and_silences_the_layer(rig: Rig) -> None:
    rig.stand()
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.behaviour.on_stop()
    rig.tick()
    assert rig.said == [] and rig.actions == []
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick(config.BEHAVIOUR_STOP_QUIET_S - 1)
    assert rig.actions == []
    rig.clock.sleep(2)
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick()
    assert [a[0] for a in rig.actions] == ["turn"]


def test_a_stop_from_the_phone_page_is_seen_in_the_statuses(rig: Rig) -> None:
    rig.stand()
    rig.behaviour.on_status(Status("done", 5, {"action": "stop", "source": "stop_event"}, 2, 0.0))
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick()
    assert rig.actions == []


def test_a_fallen_robot_does_not_move_for_people(rig: Rig) -> None:
    rig.stand()
    rig.behaviour.on_status(Status("fallen", None, {"tilt_deg": 80.0}, 3, 0.0))
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.tick()
    assert rig.actions == []


def test_the_speaker_is_a_guest_again_after_the_last_person_left(rig: Rig) -> None:
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    rig.event(PERSON_LEFT)
    rig.tick(config.BEHAVIOUR_LEAVE_GRACE_S - 1)
    assert rig.guest == 0
    rig.tick(2)
    assert rig.guest == 1
    rig.tick(config.BEHAVIOUR_LEAVE_GRACE_S + 1)
    assert rig.guest == 1  # once


def test_coming_back_inside_the_grace_keeps_the_speaker(rig: Rig) -> None:
    rig.event(PERSON_IDENTIFIED, person_id=1, score=0.7)
    rig.tick()
    rig.event(PERSON_LEFT)
    rig.tick(5)
    rig.event(PERSON_APPEARED, 2)
    rig.tick(config.BEHAVIOUR_LEAVE_GRACE_S)
    assert rig.guest == 0


def test_visible_track_is_only_given_for_exactly_one_face(rig: Rig) -> None:
    assert rig.behaviour.visible_track() is None
    rig.event(PERSON_APPEARED, 4)
    assert rig.behaviour.visible_track() == 4
    rig.event(PERSON_APPEARED, 5)
    assert rig.behaviour.visible_track() is None and rig.behaviour.people_in_view() == 2
    rig.event(PERSON_LEFT, 4)
    assert rig.behaviour.visible_track() == 5


def test_nothing_happens_while_an_enrolment_is_open(rig: Rig) -> None:
    rig.stand()
    rig.flow_active = True
    rig.event(FACE_BEARING, angle_deg=30.0)
    rig.event(PERSON_UNKNOWN)
    rig.tick()
    assert rig.actions == [] and rig.asked == []


def test_only_wave_and_small_turns_can_ever_be_sent(rig: Rig) -> None:
    with pytest.raises(ValueError):
        rig.behaviour._do("walk", {"direction": "fwd", "speed": 0.5})
    with pytest.raises(ValueError):
        rig.behaviour._do("turn", {"direction": "left", "angle_deg": 90.0})
    with pytest.raises(ValueError):
        rig.behaviour._do("sit", {})
    assert rig.actions == []


def test_the_body_saying_where_it_is_corrects_the_posture(rig: Rig) -> None:
    rig.behaviour.on_status(Status("rejected", 4, {"reason": "already_in_state",
                                                   "state": "standing"}, 5, 0.0))
    assert rig.behaviour.posture == STANDING
    rig.behaviour.on_status(Status("rejected", 6, {"reason": "already_in_state",
                                                   "state": "sitting"}, 6, 0.0))
    assert rig.behaviour.posture == "sitting"
