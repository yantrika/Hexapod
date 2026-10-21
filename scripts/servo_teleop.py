#!/usr/bin/env python3
"""Drive the REAL hexapod from the Pi's terminal keyboard (no voice, no brain process).

    python scripts/servo_teleop.py            (robot held off the ground for the first run)
    python scripts/servo_teleop.py --joints RF_coxa RF_femur RF_tibia    only these servos

    W / S  forward / back     A / D  strafe left / right     Q / E  turn left / right
    Space  stop               1 stand    2 sit    3 wave     + / -  speed up / down
    X      stop, sit and quit

A terminal sends no key-release, so a movement key counts as held for
``config.TERMINAL_KEY_HOLD_S`` after the last press (key repeat keeps it alive); then that motion
ramps to zero. Motion goes only through the ``Controller`` API and ``ServoBackend``; the robot
starts SITTING, press 1 to stand. Every joint it drives must be calibrated. You are asked to type
yes before anything moves. SIGINT/SIGTERM/SIGHUP and X sit the robot down (if standing) and
release the servos.
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
from body import servo_calibration  # noqa: E402
from body.clock import FixedRateLoop, FixedStepper  # noqa: E402
from body.controller import Controller, State  # noqa: E402
from body.servo_backend import ServoBackend  # noqa: E402
from body.servo_calibration import CalibrationError  # noqa: E402
from body.servo_safety import confirm_hardware, guarded, sit_and_release  # noqa: E402

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
    parser.add_argument("--joints", nargs="+", choices=config.JOINT_NAMES, default=None,
                        help="drive only these joints (default all 18); the rest stay released")
    parser.add_argument("--cal-file", type=Path, default=config.SERVO_CAL_FILE,
                        help="calibration file (default %(default)s)")
    args = parser.parse_args()
    try:
        calibration = servo_calibration.load(args.cal_file)
        calibration.require_calibrated(args.joints)
    except CalibrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    count = len(args.joints) if args.joints else config.DOF
    if not confirm_hardware(f"  - {count} servo(s) attach, then the robot sits; keys move it\n"):
        print("aborted: nothing was moved", file=sys.stderr)
        return 1

    import termios
    import tty

    backend = ServoBackend(calibration, joints=args.joints)  # nothing attached yet
    controllers: list[Controller] = []  # filled inside the signal guard (it attaches the servos)
    loop = FixedRateLoop(config.CONTROL_HZ)
    stepper = FixedStepper(config.CONTROL_HZ)
    saved_tty = termios.tcgetattr(sys.stdin)

    def run() -> None:
        controller = Controller(backend, initial_state=State.SITTING)  # attaches, then sits
        controllers.append(controller)
        last_press: dict[str, float] = {}
        scale = config.TELEOP_SPEED_SCALE_DEFAULT
        moving = False
        last_state = controller.state
        print(__doc__)
        tty.setcbreak(sys.stdin.fileno())
        while True:
            dt = loop.wait()
            now = time.monotonic()
            while select.select([sys.stdin], [], [], 0)[0]:
                key = sys.stdin.read(1).lower()
                if key == "x":
                    return  # the cleanup sits the robot down and releases the servos
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

    def cleanup() -> None:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved_tty)

        if controllers:
            controller = controllers[0]

            def tick() -> None:
                for _ in range(stepper.steps(loop.wait())):
                    controller.tick(stepper.period)

            sit_and_release(controller, backend, tick)
        else:
            backend.close()
        print("\nservos released")

    return guarded(run, cleanup)


if __name__ == "__main__":
    raise SystemExit(main())
