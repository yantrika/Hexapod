"""Plain temperature reading for the measurement scripts (logged information, never a limit)."""

from __future__ import annotations

import glob
import os
import subprocess


def throttle_report() -> str | None:
    """``vcgencmd get_throttled`` (Pi only): None if it is unavailable."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _cpu_sensor_paths() -> list[str]:
    """The thermal zones and the CPU core sensors (other board sensors run hotter by design)."""
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
    values = []
    for path in _cpu_sensor_paths():
        try:
            with open(path) as handle:
                values.append(int(handle.read().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return max(values) if values else None
