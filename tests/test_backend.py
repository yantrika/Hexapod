"""Step 2 checks: the backend ABC and its hard-limit clamp layer."""

from __future__ import annotations

import math

import numpy as np
import pytest

import config
from body.backend import BasePose, HexapodBackend, JointArray
from body.servo_backend import ServoBackend


class RecordingBackend(HexapodBackend):
    """Minimal concrete backend that records what ``_apply`` receives."""

    def __init__(self) -> None:
        super().__init__()
        self.applied: list[JointArray] = []

    def _apply(self, angles: JointArray) -> None:
        self.applied.append(angles)

    def get_joint_angles(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_joint_velocities(self) -> JointArray:
        return np.zeros(config.DOF)

    def get_base_pose(self) -> BasePose:
        return BasePose(np.zeros(3), np.zeros(3))

    def advance(self, dt: float) -> None:
        pass

    def close(self) -> None:
        pass


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        HexapodBackend()  # type: ignore[abstract]


def test_servo_backend_is_a_stub() -> None:
    with pytest.raises(NotImplementedError):
        ServoBackend()


def test_clamp_layer_limits_targets_before_apply() -> None:
    backend = RecordingBackend()
    wild = np.full((6, 3), 10.0)
    wild[0] = [-10.0, 0.3, 10.0]
    applied = backend.set_joint_targets(wild)
    assert backend.applied[-1] == pytest.approx(applied)
    limit = {j: math.radians(config.JOINT_HARD_LIMITS_DEG[j][1]) for j in config.JOINTS_PER_LEG}
    assert applied[0] == pytest.approx(-limit["coxa"])
    assert applied[1] == pytest.approx(0.3)  # in range, untouched
    assert applied[2] == pytest.approx(limit["tibia"])
    assert np.all(np.abs(applied) <= math.radians(90.0) + 1e-12)


def test_flat_and_matrix_inputs_are_equivalent() -> None:
    backend = RecordingBackend()
    matrix = np.linspace(-1, 1, config.DOF).reshape(6, 3)
    from_matrix = backend.set_joint_targets(matrix)
    assert from_matrix == pytest.approx(backend.set_joint_targets(matrix.ravel()))
    assert backend.joint_targets == pytest.approx(matrix.ravel())


@pytest.mark.parametrize(
    "bad", [np.zeros(17), np.zeros((6, 4)), np.full(18, np.nan), np.full(18, np.inf)]
)
def test_bad_target_arrays_are_rejected(bad: np.ndarray) -> None:
    backend = RecordingBackend()
    with pytest.raises(ValueError):
        backend.set_joint_targets(bad)
    assert backend.applied == []


def test_context_manager_closes() -> None:
    closed: list[bool] = []

    class Closing(RecordingBackend):
        def close(self) -> None:
            closed.append(True)

    with Closing():
        pass
    assert closed == [True]
