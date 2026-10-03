"""Hexapod controller: posture and motion state machine on top of the gait planner.

Public API (what the bridge will call; each command returns a ``Result``):
``set_velocity(vx, vy, yaw_rate)``, ``walk(direction, speed)``,
``turn(direction, angle_deg)``, ``heartbeat()``, ``stand()``, ``sit()``,
``wave()``, ``stop()``, and ``tick(dt)``, which the caller drives from a
wall-clock loop (``body.clock.FixedRateLoop``). ``drain_events()`` returns the
``done`` events of finite actions. The controller only talks to the
``HexapodBackend`` interface.

All clamping lives here: velocities go through ``gait.limit_command`` (max
speed, yaw rate and stride), speed and angle parameters through
``config.clamp``. Velocity commands are slewed (``VELOCITY_RAMP_S``), never
stepped, because ``gait.plan`` expects a ramped input.

States:
- ``STANDING`` / ``SITTING``: idle poses.
- ``MOVING``: gait running; walks until stopped, replaced, or the watchdog
  expires (no command or heartbeat within ``WATCHDOG_TIMEOUT_S`` ramps to zero).
  Turns finish by themselves.
- ``STANDING_UP`` / ``SITTING_DOWN`` / ``WAVING`` / ``SETTLING``: busy; new
  commands except ``stop`` get ``busy``.
- ``HOLDING``: after ``stop`` interrupted something; joint targets are frozen
  and the gait phase is kept. ``walk``/``turn`` resume from that phase (blended
  in); ``sit``/``stand`` first settle all six feet to the neutral stance, then
  run the transition. ``stop`` while idle (standing or sitting) changes nothing.

Rejections: ``already_in_state``, ``invalid_state`` (e.g. walking while sitting,
waving unless standing) and ``invalid_params``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

import config
from body import gait, poses
from body.backend import HexapodBackend
from body.clock import Clock, MonotonicClock
from body.gait import BodyVelocity

logger = logging.getLogger(__name__)

JointArray = NDArray[np.float64]
_ZERO = 1e-12


class State(StrEnum):
    STANDING = "standing"
    SITTING = "sitting"
    MOVING = "moving"
    WAVING = "waving"
    STANDING_UP = "standing_up"
    SITTING_DOWN = "sitting_down"
    SETTLING = "settling"
    HOLDING = "holding"


_BUSY = {State.WAVING, State.STANDING_UP, State.SITTING_DOWN, State.SETTLING}


@dataclass(frozen=True)
class Result:
    """Outcome of a command: ``accepted``, ``rejected`` (with a reason) or ``busy``."""

    status: str
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class Event:
    """A finished action: ``kind`` is ``"done"``; ``action`` says what, ``reason`` why."""

    kind: str
    action: str
    reason: str | None = None


ACCEPTED = Result("accepted")
BUSY = Result("busy")


def rejected(reason: str) -> Result:
    return Result("rejected", reason)


def _smooth(s: float) -> float:
    """Smoothstep: 0 at 0, 1 at 1, zero slope at both ends."""
    s = min(1.0, max(0.0, s))
    return s * s * (3.0 - 2.0 * s)


def _is_zero(v: BodyVelocity) -> bool:
    return abs(v.vx) + abs(v.vy) + abs(v.yaw_rate) < _ZERO


def _approach(value: float, target: float, max_step: float) -> float:
    return value + max(-max_step, min(max_step, target - value))


def wave_angles(t: float) -> JointArray:
    """Joint angles ``(6, 3)`` at time *t* of the wave animation (raise, wave, lower)."""
    angles = np.zeros((len(config.LEG_NAMES), 3))
    edge = min(t, config.WAVE_DURATION_S - t) / config.WAVE_BLEND_S
    blend = _smooth(edge)
    leg = config.LEG_NAMES.index(config.WAVE_LEG)
    swing = math.sin(2.0 * math.pi * config.WAVE_FREQUENCY_HZ * t)
    angles[leg] = (
        math.radians(config.WAVE_COXA_AMPLITUDE_DEG) * swing * blend,
        math.radians(config.WAVE_FEMUR_DEG) * blend,
        math.radians(config.WAVE_TIBIA_DEG) * blend,
    )
    return angles


class Controller:
    """State machine that turns commands into joint targets, one ``tick(dt)`` at a time."""

    def __init__(
        self,
        backend: HexapodBackend,
        clock: Clock | None = None,
        params: gait.GaitParams = gait.DEFAULT_PARAMS,
        initial_state: State = State.STANDING,
    ) -> None:
        if initial_state not in (State.STANDING, State.SITTING):
            raise ValueError("initial_state must be STANDING or SITTING")
        self._backend = backend
        self._clock: Clock = clock if clock is not None else MonotonicClock()
        self._params = params
        self.state: State = initial_state
        self.phase = 0.0
        self.velocity = BodyVelocity()  # current, ramped
        self.target_velocity = BodyVelocity()  # commanded, clamped
        self.angles: JointArray = (
            poses.STAND_ANGLES if initial_state is State.STANDING else poses.SIT_ANGLES
        ).copy()
        self._events: list[Event] = []
        self._last_command_time = self._clock.now()
        self._cause = "walk"  # what MOVING was doing when it ends: walk or turn
        self._expired = False  # the watchdog ended it
        self._pending: str | None = None  # "sit" / "stand" queued behind a stop or settle
        self._turn_remaining_deg: float | None = None
        self._held: JointArray = self.angles.copy()
        self._progress = 0.0  # seconds into the current transition, wave, settle or blend
        self._height_from = config.BODY_HEIGHT_STAND
        self._height_to = config.BODY_HEIGHT_SIT
        self._blend_from: JointArray | None = None
        self._blend_time = 0.0
        self._backend.set_joint_targets(self.angles)

    # --- Commands ----------------------------------------------------------
    def set_velocity(self, vx: float, vy: float, yaw_rate: float) -> Result:
        """Walk with a body velocity (m/s, m/s, rad/s). Repeat it, or call ``heartbeat``."""
        if not all(math.isfinite(v) for v in (vx, vy, yaw_rate)):
            return rejected("invalid_params")
        if self.state is State.SITTING:
            return rejected("invalid_state")
        if self.state in _BUSY:
            return BUSY
        self._last_command_time = self._clock.now()
        self._turn_remaining_deg = None
        self._pending = None
        self._cause = "walk"
        self._expired = False
        self.target_velocity = gait.limit_command(BodyVelocity(vx, vy, yaw_rate), self._params)
        if self.state is State.MOVING or _is_zero(self.target_velocity):
            return ACCEPTED
        if self.state is State.HOLDING:  # resume from the held phase, blended in
            self._blend_from = self._held.copy()
            self._blend_time = 0.0
        self.state = State.MOVING
        return ACCEPTED

    def walk(
        self, direction: str | None, speed: float, strafe: float = 0.0, yaw: float = 0.0
    ) -> Result:
        """Walk at *speed* (0..1 of the max speed) with up to three components.

        *direction* is ``"fwd"``, ``"back"`` or None (no forward component);
        *strafe* and *yaw* are in [-1, 1] (+1 = left / counter-clockwise) and are
        scaled by *speed* too. The result is clamped to the max speed and yaw rate
        by ``gait.limit_command``. All zero components ramp a walk down to a halt.
        """
        components = (speed, strafe, yaw)
        if direction not in ("fwd", "back", None) or not all(map(math.isfinite, components)):
            return rejected("invalid_params")
        if abs(strafe) > 1.0 or abs(yaw) > 1.0:
            return rejected("invalid_params")
        speed = config.clamp(speed, config.SPEED_MIN, config.SPEED_MAX)
        forward = {"fwd": 1.0, "back": -1.0, None: 0.0}[direction]
        return self.set_velocity(
            forward * speed * self._params.max_speed_m_s,
            strafe * speed * self._params.max_speed_m_s,
            yaw * speed * self._params.max_yaw_rate_rad_s,
        )

    def turn(self, direction: str, angle_deg: float) -> Result:
        """Turn ``"left"`` or ``"right"`` in place by *angle_deg*, then finish."""
        if direction not in ("left", "right") or not math.isfinite(angle_deg):
            return rejected("invalid_params")
        angle = config.clamp(angle_deg, config.TURN_ANGLE_MIN_DEG, config.TURN_ANGLE_MAX_DEG)
        sign = 1.0 if direction == "left" else -1.0
        result = self.set_velocity(0.0, 0.0, sign * self._params.max_yaw_rate_rad_s)
        if result.ok:
            self._turn_remaining_deg = angle
            self._cause = "turn"
        return result

    def heartbeat(self) -> None:
        """Keep the watchdog from expiring without changing the command."""
        self._last_command_time = self._clock.now()

    def stand(self) -> Result:
        if self.state is State.STANDING:
            return rejected("already_in_state")
        if self.state in _BUSY:
            return BUSY
        if self.state is State.SITTING:
            self._start_stand_up()
        elif self.state is State.MOVING:
            self._stop_motion_then("stand")
        else:  # HOLDING
            self._start_settle("stand")
        return ACCEPTED

    def sit(self) -> Result:
        if self.state is State.SITTING:
            return rejected("already_in_state")
        if self.state in _BUSY:
            return BUSY
        if self.state is State.STANDING:
            self._start_sit_down()
        elif self.state is State.MOVING:
            self._stop_motion_then("sit")
        else:  # HOLDING
            self._start_settle("sit")
        return ACCEPTED

    def wave(self) -> Result:
        if self.state in _BUSY:
            return BUSY
        if self.state is not State.STANDING:
            return rejected("invalid_state")
        self.state = State.WAVING
        self._progress = 0.0
        return ACCEPTED

    def stop(self) -> Result:
        """Interrupt anything and hold the current pose. Always accepted, idempotent."""
        interruptible = {State.MOVING} | _BUSY
        if self.state in interruptible:
            self._held = self.angles.copy()
            self.state = State.HOLDING
            self.velocity = BodyVelocity()
            self.target_velocity = BodyVelocity()
            self._turn_remaining_deg = None
            self._pending = None
            self._blend_from = None
        self._events.append(Event("done", "stop"))
        return ACCEPTED

    def drain_events(self) -> list[Event]:
        events, self._events = self._events, []
        return events

    # --- Time ----------------------------------------------------------------
    def tick(self, dt: float) -> float:
        """Send this tick's targets, advance the backend by *dt* wall seconds, update state.

        Returns the simulated seconds actually advanced; phase and all timers use
        that value, so a slow or stalled loop never desynchronises the gait.
        """
        self._check_watchdog()
        applied = self._backend.set_joint_targets(self._compute_angles())
        self.angles = applied.reshape(len(config.LEG_NAMES), 3)
        stepped = self._backend.advance(dt)
        self._update(stepped)
        return stepped

    # --- Internals -----------------------------------------------------------
    def _start_stand_up(self) -> None:
        self._height_from, self._height_to = config.BODY_HEIGHT_SIT, config.BODY_HEIGHT_STAND
        self._progress = 0.0
        self.state = State.STANDING_UP

    def _start_sit_down(self) -> None:
        self._height_from, self._height_to = config.BODY_HEIGHT_STAND, config.BODY_HEIGHT_SIT
        self._progress = 0.0
        self.state = State.SITTING_DOWN

    def _start_settle(self, then: str) -> None:
        self._pending = then
        self._progress = 0.0
        self.state = State.SETTLING

    def _stop_motion_then(self, action: str) -> None:
        self._pending = action
        self._turn_remaining_deg = None
        self.target_velocity = BodyVelocity()

    def _check_watchdog(self) -> None:
        if self.state is not State.MOVING or _is_zero(self.target_velocity):
            return
        if self._clock.now() - self._last_command_time > config.WATCHDOG_TIMEOUT_S:
            logger.warning("watchdog: no command for %.1f s; ramping to zero",
                           config.WATCHDOG_TIMEOUT_S)
            self.target_velocity = BodyVelocity()
            self._turn_remaining_deg = None
            self._expired = True

    def _compute_angles(self) -> JointArray:
        state = self.state
        if state is State.STANDING:
            return poses.STAND_ANGLES
        if state is State.SITTING:
            return poses.SIT_ANGLES
        if state is State.HOLDING:
            return self._held
        if state is State.MOVING:
            planned = gait.plan(self.phase, self.velocity, self._params).joint_angles
            if self._blend_from is not None:
                weight = _smooth(self._blend_time / config.RESUME_BLEND_S)
                return self._blend_from * (1.0 - weight) + planned * weight
            return planned
        if state is State.WAVING:
            return wave_angles(self._progress)
        if state is State.SETTLING:
            weight = _smooth(self._progress / config.SETTLE_S)
            return self._held * (1.0 - weight) + poses.STAND_ANGLES * weight
        weight = _smooth(self._progress / config.SIT_STAND_TRANSITION_S)  # sit / stand transition
        height = self._height_from + (self._height_to - self._height_from) * weight
        return poses.pose_angles(height)

    def _update(self, dt: float) -> None:
        state = self.state
        if state is State.MOVING:
            self._update_motion(dt)
        elif state is State.WAVING:
            self._progress += dt
            if self._progress >= config.WAVE_DURATION_S:
                self._finish(State.STANDING, "wave")
        elif state in (State.STANDING_UP, State.SITTING_DOWN):
            self._progress += dt
            if self._progress >= config.SIT_STAND_TRANSITION_S:
                if state is State.STANDING_UP:
                    self._finish(State.STANDING, "stand")
                else:
                    self._finish(State.SITTING, "sit")
        elif state is State.SETTLING:
            if self._progress >= config.SETTLE_S:  # the exact neutral stance was just sent
                self._finish_settle()
            else:
                self._progress = min(config.SETTLE_S, self._progress + dt)

    def _finish(self, state: State, action: str, reason: str | None = None) -> None:
        self.state = state
        self._events.append(Event("done", action, reason))

    def _finish_settle(self) -> None:
        pending, self._pending = self._pending, None
        if pending == "sit":
            self._start_sit_down()
        else:
            self._finish(State.STANDING, "stand")

    def _update_motion(self, dt: float) -> None:
        p = self._params
        lin_step = p.max_speed_m_s / config.VELOCITY_RAMP_S * dt
        yaw_step = p.max_yaw_rate_rad_s / config.VELOCITY_RAMP_S * dt
        t, v = self.target_velocity, self.velocity
        self.velocity = BodyVelocity(
            _approach(v.vx, t.vx, lin_step),
            _approach(v.vy, t.vy, lin_step),
            _approach(v.yaw_rate, t.yaw_rate, yaw_step),
        )
        self.phase = (self.phase + dt / p.period_s) % 1.0
        if self._blend_from is not None:
            self._blend_time += dt
            if self._blend_time >= config.RESUME_BLEND_S:
                self._blend_from = None

        if self._turn_remaining_deg is not None and not _is_zero(self.target_velocity):
            yaw = self.velocity.yaw_rate
            self._turn_remaining_deg -= math.degrees(abs(yaw) * dt)
            yaw_accel = p.max_yaw_rate_rad_s / config.VELOCITY_RAMP_S
            braking = math.degrees(yaw * yaw / (2.0 * yaw_accel))
            if self._turn_remaining_deg <= braking:  # brake so the turn ends on the angle
                self.target_velocity = BodyVelocity()
                self._turn_remaining_deg = None

        if _is_zero(self.target_velocity) and _is_zero(self.velocity):
            pending, self._pending = self._pending, None
            cause, self._cause = self._cause, "walk"
            expired, self._expired = self._expired, False
            self.velocity = BodyVelocity()  # exactly zero, not "within rounding of zero"
            self._blend_from = None
            if pending == "sit":
                self._start_sit_down()
            else:
                reason = "watchdog" if expired else None
                self._finish(State.STANDING, pending or cause, reason)
