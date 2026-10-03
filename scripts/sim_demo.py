#!/usr/bin/env python3
"""Hold a pose in the PyBullet simulation (GUI by default, ``--headless`` for DIRECT).

Examples:
    python scripts/sim_demo.py --pose stand            # GUI, runs until the window closes
    python scripts/sim_demo.py --headless --pose stand # prints measured numbers and exits
    python scripts/sim_demo.py --pose cycle            # stand and sit alternately
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pybullet  # noqa: E402

import config  # noqa: E402
from body import poses  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

CYCLE_HOLD_S = 2.5


def report(sim: SimBackend, label: str) -> None:
    roll, pitch = sim.tilt_deg()
    speed = float(np.abs(sim.get_joint_velocities()).max())
    print(
        f"{label}: height={sim.body_height():.4f} m  roll={roll:+.3f} deg  "
        f"pitch={pitch:+.3f} deg  max joint speed={speed:.4f} rad/s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--headless", action="store_true", help="PyBullet DIRECT mode, no window")
    parser.add_argument("--pose", choices=["stand", "sit", "cycle"], default="stand")
    parser.add_argument("--duration", type=float, default=None,
                        help="seconds of sim time (default: 3 headless, forever with GUI)")
    args = parser.parse_args()

    duration = args.duration if args.duration is not None else (3.0 if args.headless else None)
    sim = SimBackend(gui=not args.headless)
    try:
        sequence = {"stand": [poses.STAND_ANGLES], "sit": [poses.SIT_ANGLES],
                    "cycle": [poses.STAND_ANGLES, poses.SIT_ANGLES]}[args.pose]
        labels = {"stand": ["stand"], "sit": ["sit"], "cycle": ["stand", "sit"]}[args.pose]
        sim.set_joint_targets(sequence[0])

        # Wall-clock driven: the GUI runs in real time, headless as fast as it can.
        tick = 1.0 / config.CONTROL_HZ
        sim_time, next_switch, index = 0.0, CYCLE_HOLD_S, 0
        last = time.monotonic()
        while duration is None or sim_time < duration:
            if args.headless:
                dt = tick
            else:
                time.sleep(tick)
                now = time.monotonic()
                dt, last = now - last, now
            if not sim.connected:
                break
            try:
                sim.advance(dt)
            except pybullet.error:
                break  # window closed
            sim_time += dt
            if len(sequence) > 1 and sim_time >= next_switch:
                report(sim, labels[index])
                index = (index + 1) % len(sequence)
                sim.set_joint_targets(sequence[index])
                next_switch += CYCLE_HOLD_S
        if sim.connected:
            report(sim, f"final ({labels[index]})")
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
