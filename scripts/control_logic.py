"""Pure logic of the control window: keys to bridge commands, with no Tk and no clock of its own.

``ControlState`` turns key presses and releases into the ``Send`` actions the window
puts on the bridge. Movement is hold-to-move with a deadman: releasing a key zeroes
that component (debounced, because X11 reports a held key as fake release/press
pairs), heartbeats go out while a movement key is held, losing the window focus
sends ``stop``, and nothing moves while the command entry box has the focus.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import config
from bridge import Status

KEY_HELP = """\
  W / S   forward / back       A / D   strafe left / right      Q / E   turn left / right
  Space   stop (hold pose)     1 stand    2 sit    3 wave       + / -   speed up / down
  Enter or Tab: type a command      Esc: back to the keys
  Hold a key to move; release it to ramp that motion to zero. Losing focus sends stop."""

RELEASE_DEBOUNCE_S = 0.04  # X11 auto-repeat: a fake release is followed by a press within ~30 ms
HEARTBEAT_PERIOD_S = 0.1  # 10 Hz while a movement key is held

MOVEMENT_KEYS = frozenset("wasdqe")
_POSTURE_KEYS = {" ": "stop", "1": "stand", "2": "sit", "3": "wave"}
_FASTER = frozenset("+=")
_SLOWER = frozenset("-_")


@dataclass(frozen=True)
class Send:
    """One command for the bridge."""

    action: str
    params: dict[str, Any]

    @property
    def kind(self) -> str:
        """The action, except that a walk with no component is a ``halt`` (ramp to a stop)."""
        zero_walk = not any(self.params.get(key) for key in ("direction", "strafe", "yaw"))
        return "halt" if self.action == "walk" and zero_walk else self.action


def walk_params(held: frozenset[str], scale: float) -> dict[str, Any]:
    """Params of the one combined ``walk`` message for the movement keys held.

    W/S set ``direction``; A/D set ``strafe`` (+1 = left); Q/E set ``yaw`` (+1 =
    counter-clockwise). Opposing keys cancel and only non-zero components are
    sent, so W alone is the original ``direction`` + ``speed`` message. With no
    component left the result is a zero walk (``strafe`` and ``yaw`` 0), which
    ramps a walk down to a halt. *scale* (0..1) is the ``speed`` parameter.
    """
    forward = ("w" in held) - ("s" in held)
    strafe = ("a" in held) - ("d" in held)
    yaw = ("q" in held) - ("e" in held)
    speed = config.clamp(scale, 0.0, config.SPEED_MAX)
    if not (forward or strafe or yaw):
        return {"strafe": 0.0, "yaw": 0.0, "speed": speed}
    params: dict[str, Any] = {"speed": speed}
    if forward:
        params["direction"] = "fwd" if forward > 0 else "back"
    if strafe:
        params["strafe"] = float(strafe)
    if yaw:
        params["yaw"] = float(yaw)
    return params


def adjust_scale(scale: float, key: str) -> float:
    """The speed scale after a ``+`` or ``-`` key, within the allowed range."""
    if key in _FASTER:
        scale += config.TELEOP_SPEED_SCALE_STEP
    elif key in _SLOWER:
        scale -= config.TELEOP_SPEED_SCALE_STEP
    return round(config.clamp(scale, config.TELEOP_SPEED_SCALE_MIN, config.SPEED_MAX), 6)


class ControlState:
    """Key state machine. Call ``poll()`` every few milliseconds; everything returns Sends."""

    def __init__(
        self,
        clock: Callable[[], float],
        scale: float = config.TELEOP_SPEED_SCALE_DEFAULT,
        debounce_s: float = RELEASE_DEBOUNCE_S,
        heartbeat_period_s: float = HEARTBEAT_PERIOD_S,
    ) -> None:
        self._clock = clock
        self.scale = scale
        self._debounce = debounce_s
        self._beat_period = heartbeat_period_s
        self._held: set[str] = set()
        self._release_at: dict[str, float] = {}  # key -> time its release becomes real
        self._next_beat = 0.0
        self.entry_focused = False

    @property
    def held(self) -> frozenset[str]:
        """Movement keys currently counted as held (a debouncing release still counts)."""
        return frozenset(self._held)

    @property
    def moving(self) -> bool:
        return bool(self._held)

    # --- input events --------------------------------------------------------
    def key_press(self, key: str) -> list[Send]:
        if self.entry_focused:
            return []  # typing "w" in the entry box must never move the robot
        key = key.lower()
        if key in MOVEMENT_KEYS:
            self._release_at.pop(key, None)  # a press right after a release = auto-repeat
            if key in self._held:
                return []
            was_moving = self.moving
            self._held.add(key)
            if not was_moving:
                self._next_beat = self._clock() + self._beat_period
            return [self._walk()]
        if key in _POSTURE_KEYS:
            action = _POSTURE_KEYS[key]
            if action == "stop":
                self._clear()
            return [Send(action, {})]
        if key in _FASTER or key in _SLOWER:
            self.scale = adjust_scale(self.scale, key)
            return [self._walk()] if self.moving else []
        return []

    def key_release(self, key: str) -> None:
        key = key.lower()
        if key in self._held and key not in self._release_at:
            self._release_at[key] = self._clock() + self._debounce

    def poll(self) -> list[Send]:
        """Finish releases that outlasted the debounce, and send the heartbeat when due."""
        now = self._clock()
        due = [key for key, at in self._release_at.items() if now >= at]
        sends: list[Send] = []
        if due:
            for key in due:
                self._held.discard(key)
                del self._release_at[key]
            sends.append(self._walk())
        if self.moving and now >= self._next_beat:
            self._next_beat = now + self._beat_period
            sends.append(Send("heartbeat", {}))
        return sends

    def set_entry_focus(self, focused: bool) -> list[Send]:
        """The entry box gained or lost the focus; gaining it releases every held key."""
        self.entry_focused = focused
        if focused and self.moving:
            self._clear()
            return [self._walk()]
        return []

    def focus_lost(self) -> list[Send]:
        """The window lost the focus: forget the keys and stop (deadman)."""
        self._clear()
        return [Send("stop", {})]

    def close(self) -> list[Send]:
        return self.focus_lost()

    # --- internals -------------------------------------------------------------
    def _walk(self) -> Send:
        return Send("walk", walk_params(frozenset(self._held), self.scale))

    def _clear(self) -> None:
        self._held.clear()
        self._release_at.clear()


# --- state line -----------------------------------------------------------------
STATUS_COLOURS = {
    "accepted": "#1b8a3a", "done": "#1f5fbf",
    "rejected": "#c27a00", "busy": "#c27a00",
    "fallen": "#c0182b", "error": "#c0182b",
}


def next_state_label(
    label: str, status: Status, sent: Mapping[int, str]
) -> str:
    """The posture/state shown on the state line after *status*, given what was sent.

    *sent* maps a command's seq to its ``Send.kind``. The body has no state query in the
    schema, so the label follows its statuses: ``accepted`` starts a posture or motion,
    ``done`` finishes it, ``fallen`` is terminal until the next accepted command.
    """
    action = sent.get(status.ref_seq) if status.ref_seq is not None else None
    if status.status == "fallen":
        return "FALLEN"
    if status.status == "accepted":
        return {"stand": "standing up", "sit": "sitting down", "wave": "waving",
                "walk": "walking", "turn": "turning"}.get(action or "", label)
    if status.status == "done":
        finished = status.detail.get("action")
        if finished == "stop":
            return "holding"
        if finished == "sit":
            return "sitting"
        if finished in ("stand", "wave", "walk", "turn"):
            return "standing"
    if status.status in ("rejected", "busy"):
        state = status.detail.get("state")
        return str(state) if state else label
    return label
