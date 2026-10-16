#!/usr/bin/env python3
"""Drive the REAL hexapod from the Pi's terminal keyboard (no voice, no brain process).

    python scripts/servo_teleop.py            (robot held off the ground for the first run)

    W / S  forward / back     A / D  strafe left / right     Q / E  turn left / right
    Space  stop               1 stand    2 sit    3 wave     + / -  speed up / down
    X      stop, sit and quit

A terminal sends no key-release, so a movement key counts as held for
``config.TERMINAL_KEY_HOLD_S`` after the last press (key repeat keeps it alive); then that motion
ramps to zero. Motion goes only through the ``Controller`` API; the robot starts SITTING, press 1
to stand.
"""

from __future__ import annotations

import argparse
import math
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body.clock import FixedRateLoop, FixedStepper  # noqa: E402
from body.controller import Controller, State  # noqa: E402
from body.servo_backend import ServoBackend  # noqa: E402

AXES = {  # key -> (component, sign)
    "w": ("vx", +1.0), "s": ("vx", -1.0),
    "a": ("vy", +1.0), "d": ("vy", -1.0),
    "q": ("yaw_rate", +1.0), "e": ("yaw_rate", -1.0),
}
ACTIONS = {" ": "stop", "1": "stand", "2": "sit", "3": "wave"}


def velocity_from_held(
    last_press: dict[str, float], now: float, scale: float
) -> tuple[float, float, float]:
    """Body velocity for the keys pressed within the hold time; opposing keys cancel."""
    axes = {"vx": 0.0, "vy": 0.0, "yaw_rate": 0.0}
    for key, (axis, sign) in AXES.items():
        if now - last_press.get(key, -1e9) <= config.TERMINAL_KEY_HOLD_S:
            axes[axis] += sign
    scale = config.clamp(scale, 0.0, config.SPEED_MAX)
    return (axes["vx"] * scale * config.GAIT_MAX_SPEED_M_S,
            axes["vy"] * scale * config.GAIT_MAX_SPEED_M_S,
            axes["yaw_rate"] * scale * math.radians(config.TURN_RATE_MAX_DEG_S))


def main() -> int:
    if sys.platform == "win32":
        print("error: Raspberry Pi only (needs a Linux terminal)", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--pca-only", action="store_true",
                        help="drive only the 16 PCA9685 servos (skip the two on GPIO PWM)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the 'robot is off the ground' question")
    args = parser.parse_args()
    if not args.yes:
        answer = input("Robot held OFF the ground, kill switch in reach? Type yes: ")
        if answer.strip().lower() != "yes":
            return 1

    import termios
    import tty

    backend = ServoBackend(pca_only=args.pca_only)
    controller = Controller(backend, initial_state=State.SITTING)
    loop = FixedRateLoop(config.CONTROL_HZ)
    stepper = FixedStepper(config.CONTROL_HZ)
    last_press: dict[str, float] = {}
    scale = config.TELEOP_SPEED_SCALE_DEFAULT
    moving = False
    last_state = controller.state
    old = termios.tcgetattr(sys.stdin)
    print(__doc__)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            dt = loop.wait()
            now = time.monotonic()
            while select.select([sys.stdin], [], [], 0)[0]:
                key = sys.stdin.read(1).lower()
                if key == "x":
                    controller.stop()
                    controller.sit()
                    deadline = (now + config.SIT_STAND_TRANSITION_S
                                + config.SETTLE_S + 2.0)
                    while controller.state is not State.SITTING and time.monotonic() < deadline:
                        for _ in range(stepper.steps(loop.wait())):
                            controller.tick(stepper.period)
                    return 0
                if key in AXES:
                    last_press[key] = now
                elif key in ACTIONS:
                    result = getattr(controller, ACTIONS[key])()
                    if not result.ok:
                        print(f"\r{ACTIONS[key]}: {result.status} ({result.reason})")
                elif key in ("+", "="):
                    scale = min(scale + config.TELEOP_SPEED_SCALE_STEP, config.SPEED_MAX)
                    print(f"\rspeed {scale:.0%}")
                elif key in ("-", "_"):
                    scale = max(scale - config.TELEOP_SPEED_SCALE_STEP,
                                config.TELEOP_SPEED_SCALE_MIN)
                    print(f"\rspeed {scale:.0%}")
            vx, vy, yaw = velocity_from_held(last_press, now, scale)
            held = bool(vx or vy or yaw)
            if held or moving:  # keep refreshing while held, send zero once on release
                controller.set_velocity(vx, vy, yaw)
            moving = held
            for _ in range(stepper.steps(dt)):
                controller.tick(stepper.period)
            if controller.state != last_state:
                last_state = controller.state
                print(f"\rstate: {last_state.value}")
            controller.drain_events()
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        backend.close()
        print("\nservos released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
