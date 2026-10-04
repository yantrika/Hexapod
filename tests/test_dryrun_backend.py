"""The dry-run backend: no physics, no hardware, same clamp layer as every backend."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import config
from body.dryrun_backend import DryRunBackend
from body.process import make_backend


def test_targets_are_clamped_and_reported_back(tmp_path: Path) -> None:
    backend = DryRunBackend(tmp_path / "d.log")
    applied = backend.set_joint_targets(np.full(config.DOF, 10.0))
    assert np.allclose(applied, DryRunBackend.clamp_joint_targets(np.full(config.DOF, 10.0)))
    assert np.all(applied < 10.0)
    assert np.allclose(backend.get_joint_angles(), applied)
    assert backend.applied_count == 1


def test_the_body_never_tips_and_time_advances_exactly(tmp_path: Path) -> None:
    backend = DryRunBackend(tmp_path / "d.log")
    assert backend.advance(0.02) == 0.02
    assert np.allclose(backend.get_base_pose().rpy, 0.0)
    assert not np.any(backend.get_joint_velocities())


def test_wrong_size_is_rejected_like_every_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DryRunBackend(tmp_path / "d.log").set_joint_targets(np.zeros(3))


class Ticker:
    """A hand-driven clock."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def lines(backend: DryRunBackend) -> list[str]:
    for handler in backend._log.handlers:
        handler.flush()
    return backend.log_path.read_text().splitlines()


def test_nothing_is_logged_while_the_robot_stands_still(tmp_path: Path) -> None:
    clock = Ticker()
    backend = DryRunBackend(tmp_path / "d.log", clock)
    for _ in range(500):  # 10 s of identical targets at 50 Hz
        clock.now += 0.02
        backend.set_joint_targets(np.zeros(config.DOF))
    assert len(lines(backend)) == 1 and "FIRST" in lines(backend)[0]


def test_moving_is_logged_at_a_limited_rate_then_one_rest_line(tmp_path: Path) -> None:
    clock = Ticker()
    backend = DryRunBackend(tmp_path / "d.log", clock)
    backend.set_joint_targets(np.zeros(config.DOF))
    for step in range(1, 251):  # 5 s of a swinging target at 50 Hz
        clock.now += 0.02
        backend.set_joint_targets(np.full(config.DOF, 0.5 * np.sin(step * 0.05)))
    moving = [line for line in lines(backend) if "MOVING" in line]
    assert 5 <= len(moving) <= 11  # at most 2 Hz for 5 s (slow turning points skip), not 250
    for _ in range(40):  # then it holds still for 0.8 s
        clock.now += 0.02
        backend.set_joint_targets(np.full(config.DOF, 0.3))
    rest = [line for line in lines(backend) if "REST" in line]
    assert len(rest) == 1  # exactly one line for the new pose, however long it holds
    for _ in range(200):
        clock.now += 0.02
        backend.set_joint_targets(np.full(config.DOF, 0.3))
    assert len([line for line in lines(backend) if "REST" in line]) == 1


def test_a_line_shows_each_leg_in_degrees(tmp_path: Path) -> None:
    backend = DryRunBackend(tmp_path / "d.log", Ticker())
    backend.set_joint_targets(np.radians(np.tile([10.0, -20.0, 30.0], 6)))
    line = lines(backend)[0]
    assert all(f"{leg}[" in line for leg in config.LEG_NAMES)
    assert "RF[ +10.0  -20.0  +30.0]" in line and line.count("[") == 6


def test_the_log_goes_to_its_own_file_not_the_console(tmp_path: Path,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    backend = DryRunBackend(tmp_path / "d.log", Ticker())
    with caplog.at_level("DEBUG"):
        backend.set_joint_targets(np.zeros(config.DOF))
    assert not [r for r in caplog.records if "FIRST" in r.getMessage()]
    assert lines(backend)


def test_make_backend_builds_the_dry_run_and_rejects_unknown_names() -> None:
    assert isinstance(make_backend(True, "dryrun"), DryRunBackend)
    with pytest.raises(ValueError):
        make_backend(True, "servo")


def test_the_body_starts_without_pybullet() -> None:
    """Importing the process module and building the dry-run backend never imports pybullet."""
    code = ("import sys; sys.modules['pybullet'] = None; from body.process import make_backend; "
            "make_backend(True, 'dryrun'); print('ok')")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=60)
    assert result.stdout.strip().endswith("ok"), result.stderr
