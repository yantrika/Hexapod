#!/usr/bin/env python3
"""Hold every servo at its centre pulse so the horns and legs can be attached in the neutral pose.

    python scripts/servo_center.py             all 18 joints
    python scripts/servo_center.py --pca-only  skip the two GPIO servos (no hardware-PWM setup yet)
    python scripts/servo_center.py --joint RF_coxa --joint RF_femur   only these

Servo power ON, horns/legs OFF while this runs. Fit each horn at the neutral angle (clean angle 0),
tighten the screw, then press Enter: the servos are released. Uses the calibration table, so run
``servo_jog.py`` first if the centre pulses are not measured yet (the default is 1500 us).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body.servo_backend import CALIBRATION, make_drivers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--joint", action="append", choices=config.JOINT_NAMES,
                        help="only this joint (repeat for several)")
    parser.add_argument("--pca-only", action="store_true", help="skip the GPIO servos")
    args = parser.parse_args()
    names = args.joint or [n for n in config.JOINT_NAMES
                           if not (args.pca_only and CALIBRATION[n].driver == "gpio")]
    table = {n: CALIBRATION[n] for n in names}
    drivers = make_drivers(table)
    try:
        for name, joint in table.items():
            drivers[joint.driver].set_pulse_us(joint.channel, joint.pulse_us(0.0))
            print(f"{name:9s} {joint.driver} ch{joint.channel:<2d} -> {joint.pulse_us(0.0):.0f} us")
        input("Servos held at centre. Fit the horns now, then press Enter to release... ")
    finally:
        for driver in drivers.values():
            driver.release()
        print("released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
