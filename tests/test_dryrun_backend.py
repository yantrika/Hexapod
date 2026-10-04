"""The dry-run backend: no physics, no hardware, same clamp layer as every backend."""

from __future__ import annotations

import logging
import subprocess
import sys

import numpy as np
import pytest

import config
from body.dryrun_backend import DryRunBackend
from body.process import make_backend


def test_targets_are_clamped_and_reported_back() -> None:
    backend = DryRunBackend()
    applied = backend.set_joint_targets(np.full(config.DOF, 10.0))
    assert np.allclose(applied, DryRunBackend.clamp_joint_targets(np.full(config.DOF, 10.0)))
    assert np.all(applied < 10.0)
    assert np.allclose(backend.get_joint_angles(), applied)
    assert backend.applied_count == 1


def test_the_body_never_tips_and_time_advances_exactly() -> None:
    backend = DryRunBackend()
    assert backend.advance(0.02) == 0.02
    assert np.allclose(backend.get_base_pose().rpy, 0.0)
    assert not np.any(backend.get_joint_velocities())


def test_wrong_size_is_rejected_like_every_backend() -> None:
    with pytest.raises(ValueError):
        DryRunBackend().set_joint_targets(np.zeros(3))


def test_a_summary_is_logged_each_period(caplog: pytest.LogCaptureFixture) -> None:
    backend = DryRunBackend(log_period_s=0.0)
    with caplog.at_level(logging.INFO, logger="body.dryrun_backend"):
        backend.set_joint_targets(np.zeros(config.DOF))
    assert any("dry-run:" in record.getMessage() for record in caplog.records)


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
