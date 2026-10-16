#!/usr/bin/env python3
"""Calibrate ONE servo from the keyboard (Raspberry Pi). Horn removed, robot off the ground.

    python scripts/servo_jog.py --joint RF_coxa

    a / d    pulse -10 / +10 us        z / c   pulse -50 / +50 us
    0        back to 1500 us           n       note this pulse as CENTRE
    m / M    note this pulse as MIN / MAX (safe, no stall)
    p        print the notes as a table row for body/servo_backend.py
    q        quit (the servo is released)

Only this one joint is driven, through the same drivers as the robot, with a window of
``config.SERVO_PULSE_ABS_MIN_US`` .. ``SERVO_PULSE_ABS_MAX_US``. Send the printed row back so it
can be put into the calibration table.
"""

from __future__ import annotations

import argparse
import select
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body.servo_backend import CALIBRATION, make_drivers  # noqa: E402

STEPS = {"a": -10.0, "d": +10.0, "z": -50.0, "c": +50.0}


def next_pulse(pulse: float, key: str) -> float:
    """The pulse after one key press, kept inside the absolute window."""
    pulse += STEPS.get(key, 0.0)
    if key == "0":
        pulse = 1500.0
    return min(max(pulse, config.SERVO_PULSE_ABS_MIN_US), config.SERVO_PULSE_ABS_MAX_US)


def main() -> int:
    if sys.platform == "win32":
        print("error: Raspberry Pi only (needs a Linux terminal)", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--joint", required=True, choices=config.JOINT_NAMES)
    args = parser.parse_args()
    joint = CALIBRATION[args.joint]

    import termios
    import tty

    drivers = make_drivers({args.joint: joint})
    driver = drivers[joint.driver]
    notes: dict[str, float] = {}
    pulse = 1500.0
    old = termios.tcgetattr(sys.stdin)
    print(f"{args.joint}: {joint.driver} channel {joint.channel}. Keys: a d z c 0 n m M p q")
    try:
        tty.setcbreak(sys.stdin.fileno())
        driver.set_pulse_us(joint.channel, pulse)
        while True:
            print(f"\r{args.joint} pulse {pulse:6.0f} us   ", end="", flush=True)
            if not select.select([sys.stdin], [], [], 0.5)[0]:
                continue
            key = sys.stdin.read(1)
            if key == "q":
                break
            if key in STEPS or key == "0":
                pulse = next_pulse(pulse, key)
                driver.set_pulse_us(joint.channel, pulse)
            elif key in ("n", "m", "M"):
                notes[{"n": "centre", "m": "min", "M": "max"}[key]] = pulse
                print(f"\n  noted {notes}")
            elif key == "p":
                row = ", ".join(f"{k}_us={v:.0f}" for k, v in notes.items())
                print(f"\n  {args.joint}: {row}")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        driver.release()
        print("\nreleased")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
