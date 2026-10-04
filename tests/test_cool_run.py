"""The temperature guard (scripts/cool_run.py) with a fake thermometer: no heat needed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import config
from scripts import cool_run

SCRIPT = Path(config.PROJECT_ROOT) / "scripts" / "cool_run.py"


def test_the_guard_reads_a_plausible_temperature_or_none() -> None:
    temperature = cool_run.read_temperature_c()
    assert temperature is None or 0 < temperature < 120


def test_a_command_runs_and_its_exit_code_is_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cool_run, "read_temperature_c", lambda: 40.0)
    command = ["cool_run", "--", sys.executable, "-c", "raise SystemExit(7)"]
    monkeypatch.setattr(sys, "argv", command)
    assert cool_run.main() == 7


def test_an_unguarded_run_is_not_killed_but_still_reports_the_peak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cool_run, "read_temperature_c", lambda: 88.0)
    monkeypatch.setattr(
        sys, "argv", ["cool_run", "--unguarded", "--", sys.executable, "-c",
                      "import time; time.sleep(2.5)"],
    )
    assert cool_run.main() == 0  # 88 C would have been killed (and never started) when guarded
    err = capsys.readouterr().err
    assert "UNGUARDED" in err and "peak 88 C" in err


def test_the_command_is_killed_when_it_gets_too_hot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cool_run, "read_temperature_c", lambda: 85.0)
    monkeypatch.setattr(
        sys, "argv", ["cool_run", "--start-below", "90", "--", sys.executable, "-c",
                      "import time; time.sleep(60)"],
    )
    assert cool_run.main() == 3  # killed by the guard, long before the sleep ends


def test_it_waits_for_the_machine_to_cool_before_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cool_run, "is_raspberry_pi", lambda: False)  # same limits everywhere
    readings = iter([80.0, 75.0, 60.0, 60.0, 60.0])
    monkeypatch.setattr(cool_run, "read_temperature_c", lambda: next(readings, 60.0))
    monkeypatch.setattr(cool_run.time, "sleep", lambda seconds: None)
    started = []
    real_popen = subprocess.Popen

    def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        started.append(True)
        return real_popen(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(cool_run.subprocess, "Popen", spy)
    monkeypatch.setattr(sys, "argv", ["cool_run", "--", sys.executable, "-c", "pass"])
    assert cool_run.main() == 0 and started == [True]


def test_a_guard_limit_close_to_the_hardware_limit_is_refused() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--kill-at", "86", "--", "true"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2 and "hardware limit" in result.stderr


def test_the_pi_profile_kills_below_the_firmware_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cool_run.PI_LIMITS["kill_at"] < 80.0 < cool_run.PI_LIMITS["critical"]
    assert cool_run.PI_LIMITS["start_below"] < cool_run.PI_LIMITS["kill_at"]
    monkeypatch.setattr(cool_run, "is_raspberry_pi", lambda: True)
    monkeypatch.setattr(sys, "argv", ["cool_run", "--kill-at", "84", "--", "true"])
    with pytest.raises(SystemExit) as refused:  # 84 C is within 2 C of the Pi's 85 C limit
        cool_run.main()
    assert refused.value.code == 2
