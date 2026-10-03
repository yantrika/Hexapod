#!/usr/bin/env python3
"""Drive the simulated hexapod from the keyboard (PyBullet GUI window must have focus).

    W / S   forward / back          A / D   strafe left / right
    Q / E   turn left / right       Space   stop (hold the current pose)
    1       stand                   2       sit            3   wave
    + / -   faster / slower (capped by the config max speed)

Releasing a movement key sets that component to zero (deadman behaviour): the
controller ramps to a halt. This script has no control logic of its own: motion
goes through the ``Controller`` public API only (``set_velocity``, ``stand``,
``sit``, ``wave``, ``stop``). The key-to-command mapping below is pure, so it is
unit tested without a GUI.

On the dev laptop the GUI needs the Mesa override (see README).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pybullet  # noqa: E402

import config  # noqa: E402
from body.clock import FixedRateLoop, FixedStepper  # noqa: E402
from body.controller import Controller, Result  # noqa: E402
from body.gait import BodyVelocity  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

KEY_MAP = """\
  W / S  forward / back      A / D  strafe left / right     Q / E  turn left / right
  Space  stop (hold pose)    1 stand    2 sit    3 wave     + / -  speed up / down
  (release a movement key to ramp that motion to zero)"""

_AXES = {  # key -> (component, sign)
    "w": ("vx", +1.0), "s": ("vx", -1.0),
    "a": ("vy", +1.0), "d": ("vy", -1.0),
    "q": ("yaw_rate", +1.0), "e": ("yaw_rate", -1.0),
}
MOVEMENT_KEYS = frozenset(_AXES)
_ACTIONS = {" ": "stop", "1": "stand", "2": "sit", "3": "wave"}
_FASTER = frozenset({"+", "="})
_SLOWER = frozenset({"-", "_"})

# PyBullet getKeyboardEvents state bits.
KEY_IS_DOWN, KEY_WAS_TRIGGERED, KEY_WAS_RELEASED = 1, 2, 4


def command_from_keys(down: Mapping[str, bool], scale: float) -> BodyVelocity:
    """Body velocity for the keys currently held; opposing keys cancel.

    *scale* (0..1) is a fraction of the config maximum speed and yaw rate.
    """
    axes = {"vx": 0.0, "vy": 0.0, "yaw_rate": 0.0}
    for key, (axis, sign) in _AXES.items():
        if down.get(key, False):
            axes[axis] += sign
    scale = config.clamp(scale, 0.0, config.SPEED_MAX)
    return BodyVelocity(
        axes["vx"] * scale * config.GAIT_MAX_SPEED_M_S,
        axes["vy"] * scale * config.GAIT_MAX_SPEED_M_S,
        axes["yaw_rate"] * scale * math.radians(config.TURN_RATE_MAX_DEG_S),
    )


def actions_from_presses(pressed: Iterable[str]) -> list[str]:
    """Posture actions for the keys pressed this frame; ``stop`` always comes first."""
    actions = [_ACTIONS[key] for key in pressed if key in _ACTIONS]
    return sorted(actions, key=lambda action: action != "stop")


def adjust_scale(scale: float, pressed: Iterable[str]) -> float:
    """New speed scale after the +/- keys pressed this frame, within the allowed range."""
    for key in pressed:
        if key in _FASTER:
            scale += config.TELEOP_SPEED_SCALE_STEP
        elif key in _SLOWER:
            scale -= config.TELEOP_SPEED_SCALE_STEP
    return round(config.clamp(scale, config.TELEOP_SPEED_SCALE_MIN, config.SPEED_MAX), 6)


def update_key_state(
    down: Mapping[str, bool], events: Mapping[int, int]
) -> tuple[dict[str, bool], list[str]]:
    """Apply one frame of PyBullet keyboard events.

    Returns the new held-key state and the keys newly pressed this frame.
    """
    state = dict(down)
    pressed: list[str] = []
    for code, bits in events.items():
        if not 0 <= code < 0x110000:
            continue
        key = chr(code).lower()
        if bits & KEY_WAS_TRIGGERED or (bits & KEY_IS_DOWN and not state.get(key, False)):
            pressed.append(key)
        if bits & KEY_WAS_RELEASED:
            state[key] = False
        elif bits & KEY_IS_DOWN:
            state[key] = True
    return state, pressed


def describe(action: str, result: Result) -> str:
    reason = f" ({result.reason})" if result.reason else ""
    return f"{action}: {result.status}{reason}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--duration", type=float, default=None,
                        help="exit after this many wall seconds (smoke tests)")
    args = parser.parse_args()

    sim = SimBackend(gui=True)
    controller = Controller(sim)
    loop = FixedRateLoop(config.CONTROL_HZ)
    stepper = FixedStepper(config.CONTROL_HZ)
    print(KEY_MAP)
    down: dict[str, bool] = {}
    scale = config.TELEOP_SPEED_SCALE_DEFAULT
    was_moving_keys = False
    last_state = controller.state
    start = time.monotonic()
    try:
        while sim.connected:
            if args.duration is not None and time.monotonic() - start >= args.duration:
                break
            dt = loop.wait()
            try:
                events = sim.client.getKeyboardEvents()
            except pybullet.error:
                break  # window closed
            down, pressed = update_key_state(down, events)
            new_scale = adjust_scale(scale, pressed)
            if new_scale != scale:
                scale = new_scale
                print(f"speed scale {scale:.0%}")

            for action in actions_from_presses(pressed):
                result = getattr(controller, action)()
                if not result.ok:
                    print(describe(action, result))

            moving_keys = any(down.get(key, False) for key in MOVEMENT_KEYS)
            if moving_keys or was_moving_keys:  # keep refreshing while held, zero on release
                command = command_from_keys(down, scale)
                result = controller.set_velocity(command.vx, command.vy, command.yaw_rate)
                if not result.ok and moving_keys and not was_moving_keys:
                    print(describe("move", result))
            was_moving_keys = moving_keys

            try:  # nominal-length ticks; a late loop runs several (see FixedStepper)
                for _ in range(stepper.steps(dt)):
                    controller.tick(stepper.period)
            except pybullet.error:
                break
            if controller.state != last_state:
                last_state = controller.state
                print(f"state: {last_state.value}")
            controller.drain_events()
            sim.update_view()
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
