#!/usr/bin/env python3
"""Hold the chosen servos at their neutral pose (clean angle 0) so horns and legs can be fitted.

    python scripts/servo_center.py --joints RF_coxa
    python scripts/servo_center.py --joints RF_coxa RF_femur RF_tibia

Servo power ON, horns/legs OFF while this runs. Fit each horn at the neutral angle, tighten the
screw, then press Enter: the servos are released. The centre pulse of every chosen joint must have
been measured first (``scripts/servo_jog.py``, docs/HARDWARE.md); this refuses a joint whose
centre is still the 1500 us placeholder.

The joints are driven through ServoBackend (staggered attach, slew limit, pulse window); all other
joints stay released. SIGINT/SIGTERM/SIGHUP release the servos too. The process id is printed for
the kill -9 bench test (docs/HARDWARE.md): SIGKILL cannot be handled and leaves the last pulse.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body import servo_calibration  # noqa: E402
from body.servo_backend import ServoBackend  # noqa: E402
from body.servo_calibration import CalibrationError  # noqa: E402
from body.servo_safety import confirm_hardware, guarded  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--joints", nargs="+", required=True, choices=config.JOINT_NAMES,
                        help="the joints to hold at neutral (all others stay released)")
    parser.add_argument("--cal-file", type=Path, default=config.SERVO_CAL_FILE,
                        help="calibration file (default %(default)s)")
    args = parser.parse_args()
    try:
        calibration = servo_calibration.load(args.cal_file)
        servo_calibration.require_measured(calibration, args.joints, "centre")
    except CalibrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for name in args.joints:
        joint = calibration[name]
        print(f"{name:9s} {joint.driver} ch{joint.channel:<2d} neutral "
              f"{joint.pulse_us(0.0):.0f} us")
    if not confirm_hardware(f"  - {len(args.joints)} servo(s) are held at neutral; horns OFF\n"):
        print("aborted: nothing was moved", file=sys.stderr)
        return 1
    backend = ServoBackend(calibration, joints=args.joints, allow_uncalibrated=True,
                           idle_release_s=None)

    def hold() -> None:
        backend.set_joint_targets(np.zeros(config.DOF))  # staggered attach at neutral
        print(f"held (pid {os.getpid()}). Fit the horns now, then press Enter to release... ")
        input()

    code = guarded(hold, backend.close)
    print("released")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
