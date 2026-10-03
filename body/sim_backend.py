"""PyBullet implementation of ``HexapodBackend`` for simulation.

Position control at the fixed physics step ``1 / config.PHYSICS_HZ``. Runs in
DIRECT (headless) or GUI mode. ``advance`` is driven by wall-clock time through
a time accumulator, so simulated speed does not depend on loop iteration rate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pybullet
import pybullet_data
from numpy.typing import ArrayLike
from pybullet_utils import bullet_client

import config
from body import poses
from body.backend import BasePose, HexapodBackend, JointArray


class SimBackend(HexapodBackend):
    """Simulated hexapod. Pass ``gui=True`` for the PyBullet window."""

    def __init__(self, gui: bool | None = None, urdf_path: Path | None = None) -> None:
        super().__init__()
        if gui is None:
            gui = not config.SIM_HEADLESS
        urdf = Path(urdf_path) if urdf_path is not None else config.URDF_PATH
        if not urdf.exists():
            raise FileNotFoundError(f"{urdf} not found; run scripts/generate_urdf.py")

        self._physics_dt = 1.0 / config.PHYSICS_HZ
        self._time_debt = 0.0
        self.gui = gui
        self._pb = bullet_client.BulletClient(
            connection_mode=pybullet.GUI if gui else pybullet.DIRECT
        )
        pb = self._pb
        if gui:
            pb.configureDebugVisualizer(pb.COV_ENABLE_GUI, 0)
            pb.resetDebugVisualizerCamera(
                cameraDistance=0.9, cameraYaw=45, cameraPitch=-25,
                cameraTargetPosition=[0, 0, config.BODY_HEIGHT_STAND / 2],
            )
        pb.setAdditionalSearchPath(pybullet_data.getDataPath())
        pb.setGravity(0, 0, -config.SIM_GRAVITY)
        pb.setTimeStep(self._physics_dt)

        self._ground = pb.loadURDF("plane.urdf")
        pb.changeDynamics(self._ground, -1, lateralFriction=config.GROUND_FRICTION)
        self._robot = pb.loadURDF(
            str(urdf),
            basePosition=[0, 0, config.BODY_HEIGHT_STAND + config.SIM_SPAWN_CLEARANCE_M],
            useFixedBase=False,
            flags=pybullet.URDF_USE_INERTIA_FROM_FILE,
        )

        link_index = {
            pb.getJointInfo(self._robot, i)[12].decode(): i
            for i in range(pb.getNumJoints(self._robot))
        }
        joint_index = {
            pb.getJointInfo(self._robot, i)[1].decode(): i
            for i in range(pb.getNumJoints(self._robot))
        }
        self._joint_ids = [joint_index[name] for name in config.JOINT_NAMES]
        self._foot_ids = [link_index[f"{leg}_foot"] for leg in config.LEG_NAMES]
        for foot in self._foot_ids:
            pb.changeDynamics(self._robot, foot, lateralFriction=config.FOOT_FRICTION)

        self.reset_joint_angles(poses.STAND_ANGLES)
        self.set_joint_targets(poses.STAND_ANGLES)

    # --- HexapodBackend ----------------------------------------------------
    def _apply(self, angles: JointArray) -> None:
        for joint_id, angle in zip(self._joint_ids, angles, strict=True):
            self._pb.setJointMotorControl2(
                self._robot,
                joint_id,
                pybullet.POSITION_CONTROL,
                targetPosition=float(angle),
                force=config.JOINT_MAX_FORCE_NM,
                maxVelocity=config.JOINT_MAX_VELOCITY_RAD_S,
                positionGain=config.JOINT_POSITION_GAIN,
                velocityGain=config.JOINT_VELOCITY_GAIN,
            )

    def get_joint_angles(self) -> JointArray:
        states = self._pb.getJointStates(self._robot, self._joint_ids)
        return np.array([s[0] for s in states], dtype=np.float64)

    def get_joint_velocities(self) -> JointArray:
        states = self._pb.getJointStates(self._robot, self._joint_ids)
        return np.array([s[1] for s in states], dtype=np.float64)

    def get_base_pose(self) -> BasePose:
        position, orientation = self._pb.getBasePositionAndOrientation(self._robot)
        rpy = self._pb.getEulerFromQuaternion(orientation)
        return BasePose(np.array(position, dtype=np.float64), np.array(rpy, dtype=np.float64))

    def advance(self, dt: float) -> float:
        """Step physics for *dt* seconds of wall time at the fixed physics rate.

        Elapsed time is accumulated and converted to whole physics steps, capped
        at ``config.MAX_PHYSICS_CATCHUP_STEPS`` so a stall cannot cause a burst.
        Returns the simulated seconds actually stepped, so callers can keep the
        gait phase in step with the physics when time was dropped.
        """
        self._time_debt += max(0.0, dt)
        steps = int(self._time_debt / self._physics_dt)
        if steps > config.MAX_PHYSICS_CATCHUP_STEPS:
            steps = config.MAX_PHYSICS_CATCHUP_STEPS
            self._time_debt = 0.0  # drop the backlog instead of bursting
        else:
            self._time_debt -= steps * self._physics_dt
        for _ in range(steps):
            self._pb.stepSimulation()
        return steps * self._physics_dt

    def close(self) -> None:
        if self._pb.isConnected():
            self._pb.disconnect()

    # --- Simulation-only helpers ------------------------------------------
    @property
    def client(self) -> bullet_client.BulletClient:
        """The PyBullet client, for GUI-only input in dev tools (keyboard events, sliders)."""
        return self._pb

    @property
    def connected(self) -> bool:
        return bool(self._pb.isConnected())

    def run_for(self, seconds: float) -> None:
        """Step exactly ``round(seconds * PHYSICS_HZ)`` physics steps (uncapped; tests/offline)."""
        for _ in range(round(seconds * config.PHYSICS_HZ)):
            self._pb.stepSimulation()

    def reset_joint_angles(self, angles: ArrayLike) -> None:
        """Teleport the joints to *angles* (no dynamics), clamped to the hard limits."""
        for joint_id, angle in zip(
            self._joint_ids, self.clamp_joint_targets(angles), strict=True
        ):
            self._pb.resetJointState(self._robot, joint_id, float(angle))

    def reset_base_pose(self, position: Sequence[float], rpy: Sequence[float] = (0, 0, 0)) -> None:
        """Teleport the body and zero its velocity."""
        orientation = self._pb.getQuaternionFromEuler(list(rpy))
        self._pb.resetBasePositionAndOrientation(self._robot, list(position), orientation)
        self._pb.resetBaseVelocity(self._robot, [0, 0, 0], [0, 0, 0])

    def foot_positions_body(self) -> np.ndarray:
        """Foot target positions, shape ``(6, 3)``, in the body frame."""
        position, orientation = self._pb.getBasePositionAndOrientation(self._robot)
        inv_pos, inv_orn = self._pb.invertTransform(position, orientation)
        feet = []
        for foot in self._foot_ids:
            world = self._pb.getLinkState(self._robot, foot, computeForwardKinematics=1)[4]
            local, _ = self._pb.multiplyTransforms(inv_pos, inv_orn, world, [0, 0, 0, 1])
            feet.append(local)
        return np.array(feet, dtype=np.float64)

    def body_height(self) -> float:
        """Height of the body origin above the ground plane."""
        return float(self.get_base_pose().position[2])

    def tilt_deg(self) -> tuple[float, float]:
        """``(roll, pitch)`` of the body in degrees."""
        rpy = self.get_base_pose().rpy
        return math.degrees(rpy[0]), math.degrees(rpy[1])

