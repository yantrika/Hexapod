"""Joint jog helpers: readable labels and readout lines (no GUI)."""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("pybullet")  # the simulator is not installed on the Pi

import config  # noqa: E402
from scripts import joint_jog  # noqa: E402


def test_slider_labels_are_short_and_unique() -> None:
    labels = [joint_jog.slider_label(name) for name in config.JOINT_NAMES]
    assert len(set(labels)) == config.DOF
    assert all(len(label) <= 6 for label in labels)
    assert labels[:3] == ["RF cox", "RF fem", "RF tib"]


def test_leg_line_shows_degrees_with_sign() -> None:
    line = joint_jog.format_leg_line("RF", np.radians([12.0, -30.0, 5.0]))
    assert line.startswith("RF")
    assert "+12.0" in line and "-30.0" in line and "+5.0" in line
    assert "cox" in line and "fem" in line and "tib" in line
    assert joint_jog.format_leg_line("LM", np.zeros(3)).count("+0.0") == 3
    assert math.isclose(joint_jog.CHANGE_DEG, 0.5)
