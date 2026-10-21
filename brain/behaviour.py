"""The behaviour layer: vision events become a greeting, a question, a wave or a small turn.

Rules, not the LLM, and the only code that turns what the camera sees into body commands
(AGENTS.md): perception may trigger only ``wave`` and small in-place ``turn`` commands (never
walking, never more than ``BEHAVIOUR_TURN_MAX_DEG``), only while the robot stands and is idle, with
cooldowns, and a stop wins: after any stop the layer does nothing for ``BEHAVIOUR_STOP_QUIET_S``
and drops what was waiting. It also does nothing while a spoken enrolment is open (a turn would
ruin the samples).

    person_identified  -> the speaker becomes that person; greet by name (wave if standing)
    person_unknown     -> ask their name, once per stranger per cooldown (brain/face_flow.py)
    face_bearing       -> turn 10-20 degrees toward the face when tracking is on
    person_left        -> after a grace period the speaker is a guest again

Time is injected, so the tests run on a fake clock. Names are never logged (person ids are).
Threads: ``on_event``, ``on_status`` and ``tick`` run on the vision link thread; ``on_stop`` and
``set_tracking`` are called from the voice worker and the phone page, so the state is behind one
lock and nothing slow (speech, the body) runs with it held.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import config
from bridge import Status
from vision.events import (
    FACE_BEARING,
    PERSON_APPEARED,
    PERSON_IDENTIFIED,
    PERSON_LEFT,
    PERSON_UNKNOWN,
    VisionEvent,
)

logger = logging.getLogger(__name__)

STANDING, SITTING, FALLEN = "standing", "sitting", "fallen"
GREET, ASK = "greet", "ask"


class Speech(Protocol):
    def say(self, text: str) -> int: ...


@dataclass
class _Track:
    first_seen: float
    person_id: int | None = None
    unknown: bool = False
    asked: bool = False
    bearing: float | None = None
    bearing_at: float = 0.0


@dataclass
class _Pending:
    kind: str
    track_id: int
    person_id: int | None
    expires: float


class Behaviour:
    def __init__(
        self,
        send_action: Callable[[str, dict[str, Any]], object],
        body_idle: Callable[[], bool],
        quiet: Callable[[], bool],
        speech: Speech,
        person_name: Callable[[int], str | None],
        on_known: Callable[[str], object],
        on_guest: Callable[[], object],
        ask_name: Callable[[int], bool],
        flow_active: Callable[[], bool],
        on_greeted: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        tracking: bool = config.BEHAVIOUR_TRACKING_DEFAULT,
        posture: str = SITTING,
    ) -> None:
        self._send_action = send_action  # BrainLoop.send_action: whitelisted again below
        self._body_idle = body_idle
        self._quiet = quiet  # True when nothing is being said or thought (no reply, no speech)
        self._speech = speech
        self._person_name = person_name
        self._on_known = on_known
        self._on_guest = on_guest
        self._ask_name = ask_name
        self._flow_active = flow_active
        self._on_greeted = on_greeted
        self._clock = clock
        self._lock = threading.Lock()
        self._tracking = tracking
        self._posture = posture  # real servos power up sitting, the simulator standing
        self._tracks: dict[int, _Track] = {}
        self._pending: list[_Pending] = []
        self._greeted: dict[int, float] = {}  # person id -> when they were greeted
        self._last_asked = float("-inf")
        self._last_turn = float("-inf")
        self._silent_until = float("-inf")
        self._vision_speaker = False  # the speaker was set by a recognised face
        self._guest_at: float | None = None
        self.turns_sent = 0
        self.waves_sent = 0

    # -- state ---------------------------------------------------------------------------------
    @property
    def tracking(self) -> bool:
        return self._tracking

    def set_tracking(self, on: bool) -> None:
        self._tracking = on
        logger.info("behaviour: tracking is %s", "on" if on else "off")

    @property
    def posture(self) -> str:
        return self._posture

    def visible_track(self) -> int | None:
        """The track id when exactly one face is in view (what an enrolment needs), else None."""
        with self._lock:
            return next(iter(self._tracks)) if len(self._tracks) == 1 else None

    def people_in_view(self) -> int:
        with self._lock:
            return len(self._tracks)

    # -- inputs --------------------------------------------------------------------------------
    def on_stop(self) -> None:
        """Any stop (voice, phone, window): nothing waits and nothing starts for a while."""
        with self._lock:
            self._pending.clear()
            self._silent_until = self._clock() + config.BEHAVIOUR_STOP_QUIET_S

    def on_status(self, status: Status) -> None:
        """Statuses tell the posture and every stop, whoever sent it."""
        action = status.detail.get("action")
        if status.status == "fallen":
            self._posture = FALLEN
        elif status.status == "done" and action in ("stand", "sit"):
            self._posture = STANDING if action == "stand" else SITTING
        elif status.status == "done" and action == "stop":
            self.on_stop()
        elif status.status in ("rejected", "busy") and status.detail.get("state") in (
                "standing", "sitting"):
            self._posture = str(status.detail["state"])  # the body's own word for where it is

    def on_event(self, event: VisionEvent) -> None:
        now = self._clock()
        with self._lock:
            track = self._tracks.get(event.track_id)
            if event.kind == PERSON_LEFT:
                self._tracks.pop(event.track_id, None)
                self._pending = [p for p in self._pending if p.track_id != event.track_id]
                if not self._tracks and self._vision_speaker:
                    self._guest_at = now + config.BEHAVIOUR_LEAVE_GRACE_S
                return
            if track is None:
                track = self._tracks[event.track_id] = _Track(first_seen=now)
            if event.kind == FACE_BEARING:
                track.bearing, track.bearing_at = event.angle_deg, now
            elif event.kind == PERSON_APPEARED:
                self._guest_at = None
            elif event.kind == PERSON_IDENTIFIED and event.person_id is not None:
                self._identified(track, event, now)
            elif event.kind == PERSON_UNKNOWN and not track.unknown:
                track.unknown = True
                self._unknown(track, event, now)

    def _identified(self, track: _Track, event: VisionEvent, now: float) -> None:
        if track.person_id == event.person_id:
            return
        track.person_id, track.unknown = event.person_id, False
        self._guest_at = None
        assert event.person_id is not None
        self._pending.append(_Pending("identify", event.track_id, event.person_id,
                                      now + config.BEHAVIOUR_PENDING_MAX_S))
        last = self._greeted.get(event.person_id)
        if last is None or now - last >= config.BEHAVIOUR_GREET_COOLDOWN_S:
            self._pending.append(_Pending(GREET, event.track_id, event.person_id,
                                          now + config.BEHAVIOUR_PENDING_MAX_S))

    def _unknown(self, track: _Track, event: VisionEvent, now: float) -> None:
        if now - self._last_asked >= config.BEHAVIOUR_ASK_NAME_COOLDOWN_S and not track.asked:
            self._pending.append(_Pending(ASK, event.track_id, None,
                                          now + config.BEHAVIOUR_PENDING_MAX_S))

    # -- the clock -----------------------------------------------------------------------------
    def tick(self) -> None:
        """Do what is waiting and allowed; called about ten times a second."""
        now = self._clock()
        with self._lock:
            self._pending = [p for p in self._pending if p.expires > now]
            guest_due = self._guest_at is not None and now >= self._guest_at
            if guest_due:
                self._guest_at, self._vision_speaker = None, False
            due = self._take_due(now)
        if guest_due:
            self._on_guest()
        for pending in due:
            self._act(pending, now)
        self._maybe_turn(now)

    def _can_act(self, now: float) -> bool:
        return now >= self._silent_until and self._body_idle() and not self._flow_active()

    def _take_due(self, now: float) -> list[_Pending]:
        """The pending items that may happen now (called with the lock held). Identification is
        quiet, so it always goes; greeting and questions wait for a quiet, idle robot."""
        if not self._pending:
            return []
        can_speak = self._can_act(now) and self._quiet()
        due, keep = [], []
        for pending in self._pending:
            if pending.kind == "identify" or can_speak:
                due.append(pending)
                if pending.kind != "identify":
                    can_speak = False  # one spoken thing per tick; the rest waits
            else:
                keep.append(pending)
        self._pending = keep
        return due

    def _act(self, pending: _Pending, now: float) -> None:
        if pending.kind == "identify":
            assert pending.person_id is not None
            name = self._person_name(pending.person_id)
            if name is not None:
                self._vision_speaker = True
                self._on_known(name)
            return
        if pending.kind == GREET:
            assert pending.person_id is not None
            name = self._person_name(pending.person_id)
            if name is None:
                return
            self._greeted[pending.person_id] = now
            logger.info("behaviour: greeting person %d", pending.person_id)
            self._speech.say(f"Hi {name}!")
            self._on_greeted()
            if config.BEHAVIOUR_WAVE_FOR_GREETING and self._posture == STANDING:
                self._do("wave", {})
        elif pending.kind == ASK:
            with self._lock:
                track = self._tracks.get(pending.track_id)
                if track is None or track.asked:
                    return
                track.asked = True
            if self._ask_name(pending.track_id):
                self._last_asked = now
                logger.info("behaviour: asked a stranger their name")

    # -- turning toward a person ---------------------------------------------------------------
    def _maybe_turn(self, now: float) -> None:
        if (not self._tracking or self._posture != STANDING
                or now - self._last_turn < config.BEHAVIOUR_TURN_COOLDOWN_S
                or not self._can_act(now)):
            return
        with self._lock:
            fresh = [t for t in self._tracks.values()
                     if t.bearing is not None
                     and now - t.bearing_at <= config.BEHAVIOUR_BEARING_MAX_AGE_S]
            target = max(fresh, key=lambda t: t.bearing_at, default=None)
            angle = target.bearing if target is not None else None
        if angle is None or abs(angle) < config.BEHAVIOUR_TURN_MIN_DEG:
            return
        degrees = config.clamp(abs(angle), config.BEHAVIOUR_TURN_MIN_DEG,
                               config.BEHAVIOUR_TURN_MAX_DEG)
        self._last_turn = now
        self._do("turn", {"direction": "left" if angle > 0 else "right", "angle_deg": degrees})

    def _do(self, action: str, params: dict[str, Any]) -> None:
        """The only way this layer moves the robot: stand-still actions and small turns only."""
        if action == "wave":
            self.waves_sent += 1
        elif action == "turn":
            if params.get("angle_deg", 0.0) > config.BEHAVIOUR_TURN_MAX_DEG:
                raise ValueError("behaviour turns are small")
            self.turns_sent += 1
        else:
            raise ValueError(f"behaviour may not send {action!r}")
        self._send_action(action, params)

    def note_greeted(self, person_id: int) -> None:
        """Someone just met Arya another way (a finished enrolment): no greeting on top of it."""
        self._greeted[person_id] = self._clock()
