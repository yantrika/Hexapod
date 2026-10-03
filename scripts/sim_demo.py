#!/usr/bin/env python3
"""Hold a pose, or run a scripted sequence through the controller, in the PyBullet sim.

Examples:
    python scripts/sim_demo.py --headless --pose stand       # backend only, prints numbers
    python scripts/sim_demo.py --pose cycle                  # GUI: stand and sit alternately
    python scripts/sim_demo.py --headless --script "stand,walk,stop,sit"
    python scripts/sim_demo.py --script "wave,walk:5,turn:90,strafe:3,stop,sit,stand"

Script steps (comma separated, optional ``:value``):
    walk[:s] back[:s] strafe[:s]   continuous motion for s seconds (default 4)
    turn[:deg]                     turn left in place by deg degrees (default 90)
    stand sit wave                 posture actions (waits until finished)
    stop                           interrupt and hold the pose (observed for 1 s)
    wait[:s]                       do nothing for s seconds (default 1)

``--script`` drives the ``Controller`` (``--headless`` runs on a simulated clock
as fast as possible; the GUI runs on the wall clock at the control rate). The GUI
needs the Mesa override on the dev laptop (see README).
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
from body import poses  # noqa: E402
from body.clock import FixedRateLoop, FixedStepper, ManualClock, MonotonicClock  # noqa: E402
from body.controller import Controller, Result, State  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

CYCLE_HOLD_S = 2.5
DEFAULT_MOTION_S = 4.0
IDLE = (State.STANDING, State.SITTING, State.HOLDING)


def report(sim: SimBackend, label: str) -> None:
    roll, pitch = sim.tilt_deg()
    speed = float(np.abs(sim.get_joint_velocities()).max())
    print(
        f"{label}: height={sim.body_height():.4f} m  roll={roll:+.3f} deg  "
        f"pitch={pitch:+.3f} deg  max joint speed={speed:.4f} rad/s"
    )


def parse_script(text: str) -> list[tuple[str, float | None]]:
    steps = []
    for token in (t.strip() for t in text.split(",") if t.strip()):
        name, _, value = token.partition(":")
        if name not in {"walk", "back", "strafe", "turn", "stand", "sit", "wave", "stop", "wait"}:
            raise SystemExit(f"unknown script step: {token!r}")
        steps.append((name, float(value) if value else None))
    return steps


class ScriptRunner:
    """Runs script steps against a controller, on a simulated or a wall clock."""

    def __init__(self, sim: SimBackend, headless: bool) -> None:
        self.sim = sim
        self.headless = headless
        self.clock: ManualClock | MonotonicClock = ManualClock() if headless else MonotonicClock()
        self.controller = Controller(sim, self.clock)
        self.loop = None if headless else FixedRateLoop(config.CONTROL_HZ, self.clock)
        self.stepper = FixedStepper(config.CONTROL_HZ)
        self.period = 1.0 / config.CONTROL_HZ
        self.alive = True

    def tick(self, heartbeat: bool = False) -> float:
        """One control tick; returns the simulated seconds it advanced."""
        if heartbeat:
            self.controller.heartbeat()
        if isinstance(self.clock, ManualClock):
            dt = 1.0 / config.CONTROL_HZ
            self.clock.advance(dt)
        else:
            assert self.loop is not None
            dt = self.loop.wait()
        if not self.sim.connected:
            self.alive = False
            return 0.0
        try:  # always nominal-length ticks; a late loop runs several (see FixedStepper)
            return sum(self.controller.tick(self.period) for _ in range(self.stepper.steps(dt)))
        except pybullet.error:
            self.alive = False  # window closed
            return 0.0

    def run_for(self, seconds: float, heartbeat: bool = False) -> None:
        """Run for *seconds* of simulated time (so the GUI and headless runs agree)."""
        elapsed = 0.0
        while self.alive and elapsed < seconds:
            elapsed += self.tick(heartbeat)

    def run_until_idle(self, limit_s: float = 15.0) -> None:
        elapsed = 0.0
        while self.alive and self.controller.state not in IDLE and elapsed < limit_s:
            elapsed += self.tick()

    def step(self, name: str, value: float | None) -> None:
        c = self.controller
        result: Result | None = None
        if name in ("walk", "back", "strafe"):
            seconds = value if value is not None else DEFAULT_MOTION_S
            result = (
                c.set_velocity(0.0, config.GAIT_MAX_SPEED_M_S, 0.0)
                if name == "strafe"
                else c.walk("fwd" if name == "walk" else "back", 1.0)
            )
            if result.ok:
                self.run_for(seconds, heartbeat=True)
        elif name == "turn":
            result = c.turn("left", value if value is not None else 90.0)
            if result.ok:
                self.run_until_idle()
        elif name in ("stand", "sit", "wave"):
            result = getattr(c, name)()
            if result.ok:
                self.run_until_idle()
        elif name == "stop":
            result = c.stop()
            self.run_for(1.0)
        elif name == "wait":
            self.run_for(value if value is not None else 1.0)
        if result is not None and not result.ok:
            print(f"  {name}: {result.status}" + (f" ({result.reason})" if result.reason else ""))


def run_script(steps: list[tuple[str, float | None]], headless: bool) -> None:
    sim = SimBackend(gui=not headless)
    try:
        runner = ScriptRunner(sim, headless)
        runner.run_for(1.0)  # settle
        for name, value in steps:
            if not runner.alive:
                return
            before = sim.get_base_pose()
            yaw0 = float(before.rpy[2])
            runner.step(name, value)
            after = sim.get_base_pose()
            dx = float(after.position[0] - before.position[0])
            dy = float(after.position[1] - before.position[1])
            forward = dx * math.cos(yaw0) + dy * math.sin(yaw0)
            lateral = -dx * math.sin(yaw0) + dy * math.cos(yaw0)
            heading = math.degrees(float(after.rpy[2]) - yaw0)
            label = name + (f":{value:g}" if value is not None else "")
            print(
                f"{label:12s} state={runner.controller.state.value:12s} "
                f"forward={forward:+.3f} m lateral={lateral:+.3f} m heading={heading:+7.1f} deg "
                f"height={sim.body_height():.4f} m"
            )
        if runner.alive:
            report(sim, "final")
    except KeyboardInterrupt:
        pass
    finally:
        sim.close()


def run_pose(args: argparse.Namespace) -> None:
    sim = SimBackend(gui=not args.headless)
    try:
        sequence = {"stand": [poses.STAND_ANGLES], "sit": [poses.SIT_ANGLES],
                    "cycle": [poses.STAND_ANGLES, poses.SIT_ANGLES]}[args.pose]
        labels = {"stand": ["stand"], "sit": ["sit"], "cycle": ["stand", "sit"]}[args.pose]
        duration = args.duration if args.duration is not None else (3.0 if args.headless else None)
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
                sim_time += sim.advance(dt)
            except pybullet.error:
                break  # window closed
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--headless", action="store_true", help="PyBullet DIRECT mode, no window")
    parser.add_argument("--pose", choices=["stand", "sit", "cycle"], default="stand")
    parser.add_argument("--duration", type=float, default=None,
                        help="--pose only: seconds of sim time (default 3 headless, else forever)")
    parser.add_argument("--script", default=None, help="comma separated steps (see above)")
    args = parser.parse_args()
    if args.script is not None:
        run_script(parse_script(args.script), args.headless)
    else:
        run_pose(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
