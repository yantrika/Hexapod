#!/usr/bin/env python3
"""Run a command without letting the dev laptop overheat (it powers off at 87 C; it idles at ~59 C).

    python scripts/cool_run.py -- nice -n 19 pytest tests/test_voice_loop.py
    python scripts/cool_run.py --start-below 62 --kill-at 80 -- python scripts/voice_cli.py
    python scripts/cool_run.py --pause-at 78 --resume-below 68 -- pytest tests/test_audio.py

Before starting it waits until the temperature is below ``--start-below``. While the command
runs it checks every second: at ``--kill-at`` it kills the command (exit 3) so the hardware
protection never trips. With ``--pause-at`` it instead freezes the command (SIGSTOP) until the
laptop is below ``--resume-below``: fine for tests, WRONG for timing measurements and anything
that talks to a microphone, so it is off by default. The temperature is the hottest of the
thermal zones (acpitz, whose critical trip is 87 C) and the CPU core sensors (critical 90 C).
"""

from __future__ import annotations

import argparse
import glob
import os
import signal
import subprocess
import sys
import time

CRITICAL_C = 87.0


def _cpu_sensor_paths() -> list[str]:
    """The thermal zones (the kernel's shutdown trip is acpitz) and the CPU core sensors.
    Other board sensors (``dell_smm`` "Other") run hotter by design and have their own limits."""
    paths = glob.glob("/sys/class/thermal/thermal_zone*/temp")
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(hwmon, "name")) as handle:
                if handle.read().strip() == "coretemp":
                    paths += glob.glob(os.path.join(hwmon, "temp*_input"))
        except OSError:
            continue
    return paths


def read_temperature_c() -> float | None:
    """The hottest CPU/thermal-zone sensor in degrees C (None if the system exposes none)."""
    paths = _cpu_sensor_paths()
    values = []
    for path in paths:
        try:
            with open(path) as handle:
                values.append(int(handle.read().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return max(values) if values else None


def wait_until_below(limit_c: float, poll_s: float = 2.0) -> None:
    announced = False
    while (temperature := read_temperature_c()) is not None and temperature >= limit_c:
        if not announced:
            print(f"cool_run: {temperature:.0f} C, waiting for < {limit_c:.0f} C ...",
                  file=sys.stderr, flush=True)
            announced = True
        time.sleep(poll_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-below", type=float, default=64.0, help="wait to start (C)")
    parser.add_argument("--kill-at", type=float, default=82.0, help="kill the command (C)")
    parser.add_argument("--pause-at", type=float, default=None, help="freeze the command (C)")
    parser.add_argument("--resume-below", type=float, default=70.0, help="unfreeze (C)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give a command after --")
    if args.kill_at >= CRITICAL_C - 2:
        parser.error(f"--kill-at must stay well under the {CRITICAL_C:.0f} C hardware limit")

    wait_until_below(args.start_below)
    process = subprocess.Popen(command, start_new_session=True)  # its own process group
    paused = False
    peak = read_temperature_c() or 0.0
    try:
        while process.poll() is None:
            time.sleep(1.0)
            temperature = read_temperature_c()
            if temperature is None:
                continue
            peak = max(peak, temperature)
            if temperature >= args.kill_at:
                print(f"cool_run: {temperature:.0f} C: killing the command to avoid the "
                      f"hardware shutdown", file=sys.stderr, flush=True)
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                return 3
            if args.pause_at is not None:
                if not paused and temperature >= args.pause_at:
                    os.killpg(process.pid, signal.SIGSTOP)
                    paused = True
                    print(f"cool_run: {temperature:.0f} C: paused", file=sys.stderr, flush=True)
                elif paused and temperature < args.resume_below:
                    os.killpg(process.pid, signal.SIGCONT)
                    paused = False
                    print(f"cool_run: {temperature:.0f} C: resumed", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        os.killpg(process.pid, signal.SIGCONT)  # a frozen group would never see the SIGINT
        os.killpg(process.pid, signal.SIGINT)
        process.wait()
    print(f"cool_run: peak {peak:.0f} C", file=sys.stderr)
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
