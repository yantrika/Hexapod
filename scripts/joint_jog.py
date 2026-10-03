#!/usr/bin/env python3
"""Jog the 18 joints with PyBullet debug sliders (degrees). DEV TUNING TOOL ONLY.

This is a tuning and calibration aid, not part of the runtime: it bypasses the
controller, gait and bridge on purpose. It uses only the backend API
(``set_joint_targets`` and ``advance``), so the hard-limit clamp layer still
applies: the sliders reach 30 degrees past the hard limits so you can see a
command being clamped; the console reports it.

On the dev laptop the GUI needs the Mesa override (see README).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):  # single-threaded BLAS, before numpy
    os.environ.setdefault(_var, "1")

import numpy as np  # noqa: E402
import pybullet  # noqa: E402

import config  # noqa: E402
from body.clock import FixedRateLoop  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

OVERSHOOT_DEG = 30.0  # how far the sliders go past the hard limits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=None,
                        help="exit after this many wall seconds (smoke tests)")
    args = parser.parse_args()

    sim = SimBackend(gui=True)
    client = sim.client
    sliders = []
    for name in config.JOINT_NAMES:
        low, high = config.JOINT_HARD_LIMITS_DEG[name.split("_")[1]]
        sliders.append(
            client.addUserDebugParameter(name, low - OVERSHOOT_DEG, high + OVERSHOOT_DEG, 0.0)
        )
    print("Joint jog: move the sliders (degrees). Close the window or Ctrl-C to quit.")

    loop = FixedRateLoop(config.CONTROL_HZ)
    start = time.monotonic()
    clamped_before: tuple[str, ...] = ()
    try:
        while sim.connected:
            if args.duration is not None and time.monotonic() - start >= args.duration:
                break
            dt = loop.wait()
            try:
                requested = np.radians([client.readUserDebugParameter(s) for s in sliders])
                applied = sim.set_joint_targets(requested)
                sim.advance(dt)
            except pybullet.error:
                break  # window closed
            clamped = tuple(
                f"{name} {math.degrees(r):+.0f} -> {math.degrees(a):+.0f}"
                for name, r, a in zip(config.JOINT_NAMES, requested, applied, strict=True)
                if abs(r - a) > 1e-9
            )
            if clamped != clamped_before and clamped:
                print("clamped (deg):", ", ".join(clamped))
            clamped_before = clamped
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
