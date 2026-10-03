#!/usr/bin/env python3
"""Jog the 18 joints with PyBullet debug sliders (degrees). DEV TUNING TOOL ONLY.

This is a tuning and calibration aid, not part of the runtime: it bypasses the
controller, gait and bridge on purpose. It uses only the backend API
(``set_joint_targets`` and ``advance``), so the hard-limit clamp layer still
applies: the sliders reach 30 degrees past the hard limits so you can see a
command being clamped; the console reports it.

The slider panel is shown from the start (in plain PyBullet windows it is hidden until
you press G). Each shown leg's angles are also drawn in yellow above the robot and printed
in the terminal. On the dev laptop the GUI needs the Mesa override (see README).
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
from body import kinematics  # noqa: E402
from body.clock import FixedRateLoop  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

OVERSHOOT_DEG = 30.0  # how far the sliders go past the hard limits
READOUT_HZ = 5.0  # console readout rate
CHANGE_DEG = 0.5  # only report joints that moved at least this much


def slider_label(joint_name: str) -> str:
    """Short slider label, e.g. ``RF_femur`` -> ``RF fem``: long names get cut off in the panel."""
    leg, joint = joint_name.split("_")
    return f"{leg} {joint[:3]}"


def format_leg_line(leg: str, applied_rad: np.ndarray) -> str:
    """One readable console line per leg, in degrees: ``RF  cox  +12.0  fem  -30.0  tib   +5.0``."""
    cells = [
        f"{joint[:3]} {math.degrees(angle):+7.1f}"
        for joint, angle in zip(config.JOINTS_PER_LEG, applied_rad, strict=True)
    ]
    return f"{leg}  " + "   ".join(cells)


def label_position(leg: str) -> list[float]:
    """Where a leg's readout floats: above its hip, outside the body, in the world frame."""
    hip = kinematics.hip_position(leg)
    return [float(hip[0]) * 1.8, float(hip[1]) * 1.8, config.BODY_HEIGHT_STAND + 0.12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legs", default=",".join(config.LEG_NAMES),
                        help="legs to show sliders for, e.g. RF or RF,LM (default: all six)")
    parser.add_argument("--duration", type=float, default=None,
                        help="exit after this many wall seconds (smoke tests)")
    args = parser.parse_args()
    shown = [leg.strip().upper() for leg in args.legs.split(",") if leg.strip()]
    unknown = [leg for leg in shown if leg not in config.LEG_NAMES]
    if unknown:
        parser.error(f"unknown legs {unknown}; choose from {', '.join(config.LEG_NAMES)}")

    sim = SimBackend(gui=True, show_panel=True, window_size=(1200, 700))  # panel on from the start
    client = sim.client
    labels: dict[str, int] = {}  # leg -> id of its on-screen text above the robot
    slider_for = {}  # joint index -> slider id; hidden legs stay at 0 (the stand pose)
    for index, name in enumerate(config.JOINT_NAMES):
        if name.split("_")[0] not in shown:
            continue
        low, high = config.JOINT_HARD_LIMITS_DEG[name.split("_")[1]]
        slider_for[index] = client.addUserDebugParameter(
            slider_label(name), low - OVERSHOOT_DEG, high + OVERSHOOT_DEG, 0.0
        )
    print(f"Joint jog: sliders for {', '.join(shown)} (degrees). Values print below.")
    print("  tip: --legs RF shows only one leg's three sliders so the labels stay readable.")
    print("Close the window or Ctrl-C to quit.\n")

    loop = FixedRateLoop(config.CONTROL_HZ)
    start = last_readout = time.monotonic()
    clamped_before: tuple[str, ...] = ()
    reported = np.zeros(config.DOF)
    for i, leg in enumerate(config.LEG_NAMES):
        if leg in shown:
            reported[3 * i : 3 * i + 3] = np.inf  # forces a first readout for the shown legs
    try:
        while sim.connected:
            now = time.monotonic()
            if args.duration is not None and now - start >= args.duration:
                break
            dt = loop.wait()
            try:
                requested = np.zeros(config.DOF)
                for index, slider in slider_for.items():
                    requested[index] = math.radians(client.readUserDebugParameter(slider))
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

            if now - last_readout >= 1.0 / READOUT_HZ:
                last_readout = now
                moved = np.abs(np.degrees(applied - reported)) >= CHANGE_DEG
                for i, leg in enumerate(config.LEG_NAMES):
                    row = slice(3 * i, 3 * i + 3)
                    if moved[row].any():
                        line = format_leg_line(leg, applied[row])
                        print(line)
                        reported[row] = applied[row]
                        if leg in shown:
                            labels[leg] = client.addUserDebugText(
                                line,
                                label_position(leg),
                                textColorRGB=[1, 1, 0],
                                textSize=1.6,
                                replaceItemUniqueId=labels.get(leg, -1),
                            )
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
