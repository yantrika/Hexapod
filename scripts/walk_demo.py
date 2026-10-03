#!/usr/bin/env python3
"""Walk forward, turn in place, then strafe in the PyBullet simulation.

The loop runs at ``config.CONTROL_HZ``. With the GUI it is driven by wall-clock
time (gait phase and physics advance by the real elapsed time); with
``--headless`` it runs as fast as it can using the nominal tick.

Examples:
    python scripts/walk_demo.py --headless
    python scripts/walk_demo.py                      # GUI (see README for the laptop note)
    python scripts/walk_demo.py --segment-seconds 12 --speed 0.5
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pybullet  # noqa: E402

import config  # noqa: E402
from body import gait, poses  # noqa: E402
from body.gait import BodyVelocity  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

BLEND_S = 1.0  # linear blend between consecutive commands, so feet never jump
SETTLE_S = 1.0


def build_segments(speed: float) -> list[tuple[str, BodyVelocity]]:
    v = config.GAIT_MAX_SPEED_M_S * speed
    w = math.radians(config.TURN_RATE_MAX_DEG_S) * speed
    return [
        ("forward", BodyVelocity(vx=v)),
        ("turn in place", BodyVelocity(yaw_rate=w)),
        ("strafe left", BodyVelocity(vy=v)),
    ]


def blend(a: BodyVelocity, b: BodyVelocity, k: float) -> BodyVelocity:
    k = min(1.0, max(0.0, k))
    return BodyVelocity(
        a.vx + (b.vx - a.vx) * k,
        a.vy + (b.vy - a.vy) * k,
        a.yaw_rate + (b.yaw_rate - a.yaw_rate) * k,
    )


def pose_summary(sim: SimBackend) -> tuple[float, float, float]:
    pose = sim.get_base_pose()
    return float(pose.position[0]), float(pose.position[1]), float(pose.rpy[2])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--headless", action="store_true", help="PyBullet DIRECT mode, no window")
    parser.add_argument("--segment-seconds", type=float, default=8.0)
    parser.add_argument("--speed", type=float, default=1.0, help="fraction of max speed, 0-1")
    args = parser.parse_args()

    segments = build_segments(config.clamp(args.speed, 0.05, 1.0))
    sim = SimBackend(gui=not args.headless)
    tick = 1.0 / config.CONTROL_HZ
    try:
        sim.set_joint_targets(poses.STAND_ANGLES)
        deadline = time.monotonic()

        def pace() -> float:
            """Wait for the next control tick; return the wall time since the last one."""
            nonlocal deadline
            if args.headless:
                return tick
            deadline += tick
            now = time.monotonic()
            if now < deadline:
                time.sleep(deadline - now)
            else:  # running late (slow GUI rendering): do not try to catch up
                deadline = now
            return max(tick, time.monotonic() - (deadline - tick))

        settled = 0.0
        while settled < SETTLE_S:  # settle before walking
            settled += sim.advance(pace())

        phase, previous = 0.0, BodyVelocity()
        for name, command in segments:
            x0, y0, yaw0 = pose_summary(sim)
            max_tilt, elapsed = 0.0, 0.0
            while elapsed < args.segment_seconds:
                wall_dt = pace()
                if not sim.connected:
                    return 0
                step = gait.plan(phase, blend(previous, command, elapsed / BLEND_S))
                sim.set_joint_targets(step.joint_angles)
                try:
                    # Phase and timers follow the simulated time actually stepped, so a
                    # slow GUI slows the whole simulation instead of desynchronising the gait.
                    stepped = sim.advance(wall_dt)
                except pybullet.error:
                    return 0  # window closed
                phase = (phase + stepped / config.GAIT_PERIOD_S) % 1.0
                elapsed += stepped
                roll, pitch = sim.tilt_deg()
                max_tilt = max(max_tilt, abs(roll), abs(pitch))
            previous = command
            x1, y1, yaw1 = pose_summary(sim)
            forward = (x1 - x0) * math.cos(yaw0) + (y1 - y0) * math.sin(yaw0)
            lateral = -(x1 - x0) * math.sin(yaw0) + (y1 - y0) * math.cos(yaw0)
            print(
                f"{name:14s} forward={forward:+.3f} m lateral={lateral:+.3f} m "
                f"heading={math.degrees(yaw1 - yaw0):+.1f} deg  max tilt={max_tilt:.2f} deg  "
                f"height={sim.body_height():.4f} m"
            )
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
