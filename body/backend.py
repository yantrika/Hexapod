"""``HexapodBackend`` abstract base class.

The swappable seam between simulation and real hardware. Both the PyBullet
backend and the servo backend implement this interface; the rest of ``body/``
does not care which one is loaded.

Joint arrays are radians in the clean joint frame (0 = neutral), either flat
``(18,)`` in ``config.JOINT_NAMES`` order or ``(6, 3)`` (leg x joint).

The hard-limit clamp layer lives here, in ``set_joint_targets``, so every
backend enforces ``config.JOINT_HARD_LIMITS_DEG`` the same way. Subclasses
implement ``_apply`` and only ever see clamped targets.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import TracebackType

import numpy as np
from numpy.typing import ArrayLike, NDArray

import config

JointArray = NDArray[np.float64]
Vec3 = NDArray[np.float64]

_LOWER = np.radians(
    [config.JOINT_HARD_LIMITS_DEG[j][0] for _ in config.LEG_NAMES for j in config.JOINTS_PER_LEG]
)
_UPPER = np.radians(
    [config.JOINT_HARD_LIMITS_DEG[j][1] for _ in config.LEG_NAMES for j in config.JOINTS_PER_LEG]
)


@dataclass(frozen=True)
class BasePose:
    """Body pose in the world frame: position (m) and roll/pitch/yaw (rad)."""

    position: Vec3
    rpy: Vec3


class HexapodBackend(ABC):
    """Interface implemented by the simulation and servo backends."""

    def __init__(self) -> None:
        self._joint_targets: JointArray = np.zeros(config.DOF)

    @staticmethod
    def clamp_joint_targets(angles: ArrayLike) -> JointArray:
        """Flatten *angles* to ``(18,)`` and clamp to the hard joint limits.

        Raises ``ValueError`` for a wrong size or non-finite values, which
        cannot be clamped meaningfully.
        """
        flat: JointArray = np.asarray(angles, dtype=np.float64).reshape(-1)
        if flat.size != config.DOF:
            raise ValueError(f"expected {config.DOF} joint angles, got {flat.size}")
        if not np.all(np.isfinite(flat)):
            raise ValueError("joint angles must be finite")
        clamped: JointArray = np.clip(flat, _LOWER, _UPPER)
        return clamped

    def set_joint_targets(self, angles: ArrayLike) -> JointArray:
        """Clamp *angles* to the hard limits, apply them, and return what was applied."""
        clamped = self.clamp_joint_targets(angles)
        self._joint_targets = clamped
        self._apply(clamped)
        return clamped.copy()

    @property
    def joint_targets(self) -> JointArray:
        """The last (clamped) targets that were applied."""
        return self._joint_targets.copy()

    @abstractmethod
    def _apply(self, angles: JointArray) -> None:
        """Send clamped targets, shape ``(18,)``, to the joints."""

    @abstractmethod
    def get_joint_angles(self) -> JointArray:
        """Measured joint angles, shape ``(18,)``."""

    @abstractmethod
    def get_joint_velocities(self) -> JointArray:
        """Measured joint velocities in rad/s, shape ``(18,)``."""

    @abstractmethod
    def get_base_pose(self) -> BasePose:
        """Body pose in the world frame."""

    @abstractmethod
    def advance(self, dt: float) -> float:
        """Advance the backend by *dt* seconds of wall-clock time.

        Returns the seconds actually advanced. It equals *dt* (up to physics-step
        quantisation) unless the simulator had to drop time after a stall.
        """

    def update_view(self) -> None:
        """Optional visual refresh each control iteration (follow camera). Default: nothing."""
        return None

    @abstractmethod
    def close(self) -> None:
        """Release the simulator or hardware."""

    def __enter__(self) -> HexapodBackend:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

