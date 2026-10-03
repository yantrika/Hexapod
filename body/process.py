"""Body process: consumes bridge commands and drives the controller.

``BodyRunner`` is the in-process core (testable with a fake backend and clock):
each control tick it checks ``stop_event`` first, drains the command queue,
arbitrates (``body/arbitration.py``), dispatches to the ``Controller`` and reports
statuses. ``run_body`` wraps it in a fixed-rate loop for the child process;
``BodyProcess`` is the parent-side handle (spawn, wait ready, clean shutdown).

Status contract: every accepted command is answered ``accepted`` (or
``rejected(reason)`` / ``busy``), finite actions later ``done``; ``fallen`` is sent
once when the body tips over; an unexpected exception becomes ``error``.
"""

from __future__ import annotations

import argparse
import collections
import logging
import math
import multiprocessing as mp
import os
import signal
import time
from typing import Any

import config
from body.arbitration import arbitrate
from body.backend import HexapodBackend
from body.clock import Clock, FixedRateLoop, FixedStepper, MonotonicClock
from body.controller import Controller, Event, Result
from bridge import Bridge, Command, Status, make_bridge

logger = logging.getLogger(__name__)

_BLAS_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
_CHANGE_RAD = 1e-6  # a joint target moved at least this much since the last tick
_TIP_RPY = (1.4, 0.0, 0.0)  # test hook: roll about 80 degrees
_PROBE_FIELDS = (
    "ticks", "work_sum_s", "work_max_s", "change_time", "blas_threads",
    "base_x", "base_y", "base_yaw",  # body pose in the world, for tests
)


class BodyProbe:
    """Shared timing numbers the body writes and a test or tuning script reads.

    Not part of the brain interface: it exists to measure tick cost and the time of
    the last joint-target change. Create it with ``BodyProbe()`` and pass it to both
    ``BodyProcess`` and the parent; it survives pickling into the spawn.
    """

    def __init__(self, context: Any = None) -> None:
        ctx = context if context is not None else mp.get_context("spawn")
        self._values = ctx.Array("d", len(_PROBE_FIELDS))

    def _index(self, name: str) -> int:
        return _PROBE_FIELDS.index(name)

    def get(self, name: str) -> float:
        return float(self._values[self._index(name)])

    def set(self, name: str, value: float) -> None:
        self._values[self._index(name)] = value

    def record_tick(self, work_s: float) -> None:
        with self._values.get_lock():
            self._values[0] += 1
            self._values[1] += work_s
            self._values[2] = max(self._values[2], work_s)

    def reset_window(self) -> None:
        """Zero the tick statistics (ticks, sum, max) to start a measurement window."""
        with self._values.get_lock():
            self._values[0] = self._values[1] = self._values[2] = 0.0

    def window(self) -> tuple[int, float, float]:
        """``(ticks, mean work seconds, max work seconds)`` since ``reset_window``."""
        with self._values.get_lock():
            ticks, total, worst = self._values[0], self._values[1], self._values[2]
        return int(ticks), (total / ticks if ticks else 0.0), float(worst)


class BodyRunner:
    """One body: bridge in, controller driven, statuses out. No threads, no sleeping."""

    def __init__(
        self,
        bridge: Bridge,
        backend: HexapodBackend,
        clock: Clock | None = None,
        controller: Controller | None = None,
        probe: BodyProbe | None = None,
    ) -> None:
        self.bridge = bridge
        self.backend = backend
        self._clock: Clock = clock if clock is not None else MonotonicClock()
        self.controller = controller or Controller(backend, self._clock)
        self._probe = probe
        self._status_seq = 0
        self._refs: dict[str, int] = {}  # action -> seq of the command that started it
        self._answered_stops: collections.deque[int] = collections.deque(maxlen=32)
        self.fallen = False
        self._previous_angles = self.controller.angles.copy()

    # --- one control iteration ---------------------------------------------
    def step(self, ticks: int = 1) -> None:
        """Handle the bridge, then run *ticks* nominal controller ticks."""
        started = time.perf_counter()
        self._handle_stop_event()
        self._handle_batch(self.bridge.drain())
        period = 1.0 / config.CONTROL_HZ
        for _ in range(ticks):
            try:
                self.controller.tick(period)
            except Exception as error:  # a broken tick must reach the brain
                logger.exception("controller tick failed")
                self._report("error", None, {"message": f"tick failed: {error}"})
                raise
            self._flush_events()
            self._check_fallen()
        self.backend.update_view()
        self._note_target_change()
        if self._probe is not None:
            self._probe.record_tick(time.perf_counter() - started)

    # --- messages -----------------------------------------------------------
    def _report(
        self, status: str, ref_seq: int | None, detail: dict[str, Any] | None = None
    ) -> None:
        self._status_seq += 1
        message = Status(status, ref_seq, detail or {}, self._status_seq, time.monotonic())
        self.bridge.report(message)

    def _handle_stop_event(self) -> None:
        """The stop fast path: act before draining, and answer it by its own seq."""
        seq = self.bridge.take_stop()
        if seq is None:
            return
        self.controller.stop()
        self.controller.drain_events()  # answered below, with the stop's own ref
        if seq and seq not in self._answered_stops:
            self._answered_stops.append(seq)
            self._report("accepted", seq)
            self._report("done", seq, {"action": "stop", "source": "stop_event"})

    def _handle_batch(self, batch: list[Command]) -> None:
        if not batch:
            return
        result = arbitrate(batch, self._clock.now())
        for command, reason in result.rejected:
            self._report("rejected", command.seq, {"reason": reason})
        for command in result.stops:
            if command.seq in self._answered_stops:
                continue  # the fast path already stopped and answered
            self._answered_stops.append(command.seq)
            self._refs["stop"] = command.seq
            self.controller.stop()
            self._report("accepted", command.seq)
            self._flush_events()
        for _ in range(result.heartbeats):
            self.controller.heartbeat()
        if result.motion is not None:
            self._dispatch(result.motion)

    def _dispatch(self, command: Command) -> None:
        if self.fallen:
            self._report("rejected", command.seq, {"reason": "fallen"})
            return
        try:
            result = self._call_controller(command)
        except Exception as error:
            logger.exception("command %s failed", command.action)
            self._report("error", command.seq, {"message": f"{command.action} failed: {error}"})
            return
        detail = {"state": self.controller.state.value}
        if result.ok:
            self._refs[command.action] = command.seq
            self._report("accepted", command.seq)
        elif result.status == "busy":
            self._report("busy", command.seq, detail)
        else:
            self._report("rejected", command.seq, {"reason": result.reason, **detail})

    def _call_controller(self, command: Command) -> Result:
        ctrl, params = self.controller, command.params
        if command.action == "walk":
            return ctrl.walk(
                params.get("direction"),
                float(params.get("speed", 0.5)),
                float(params.get("strafe", 0.0)),
                float(params.get("yaw", 0.0)),
            )
        if command.action == "turn":
            return ctrl.turn(params["direction"], float(params["angle_deg"]))
        return {"stand": ctrl.stand, "sit": ctrl.sit, "wave": ctrl.wave}[command.action]()

    def _flush_events(self) -> None:
        for event in self.controller.drain_events():
            self._report_event(event)

    def _report_event(self, event: Event) -> None:
        detail: dict[str, Any] = {"action": event.action}
        if event.reason:
            detail["reason"] = event.reason
        self._report(event.kind, self._refs.get(event.action), detail)

    # --- fall and probe -----------------------------------------------------
    def _check_fallen(self) -> None:
        pose = self.backend.get_base_pose()
        roll, pitch, yaw = pose.rpy
        if self._probe is not None:
            self._probe.set("base_x", float(pose.position[0]))
            self._probe.set("base_y", float(pose.position[1]))
            self._probe.set("base_yaw", float(yaw))
        tilt = math.degrees(max(abs(roll), abs(pitch)))
        if not self.fallen and tilt > config.FALL_TILT_DEG:
            self.fallen = True
            self.controller.stop()  # halt the gait; stop is also answered normally if sent
            self.controller.drain_events()
            logger.warning("fallen: tilt %.0f deg", tilt)
            self._report("fallen", None, {"tilt_deg": round(tilt, 1)})
        elif self.fallen and tilt < config.FALL_CLEAR_TILT_DEG:
            self.fallen = False
            logger.info("upright again: tilt %.0f deg", tilt)

    def _note_target_change(self) -> None:
        angles = self.controller.angles
        if self._probe is not None and abs(angles - self._previous_angles).max() > _CHANGE_RAD:
            self._probe.set("change_time", time.monotonic())
        self._previous_angles = angles.copy()


def make_backend(headless: bool) -> HexapodBackend:
    """The simulated hexapod, DIRECT (headless) or with the PyBullet window."""
    from body.sim_backend import SimBackend  # lazy: the Pi build loads the servo backend instead

    return SimBackend(gui=not headless)


def run_body(
    bridge: Bridge,
    headless: bool = True,
    probe: BodyProbe | None = None,
    tip_event: Any = None,
    parent_pid: int | None = None,
) -> None:
    """Child-process entry point. Returns when shut down or when the parent dies."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(processName)s %(levelname)s %(message)s"
    )
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent decides when the body stops
    terminated: list[bool] = []
    signal.signal(signal.SIGTERM, lambda *_: terminated.append(True))
    if probe is not None:
        probe.set("blas_threads", float(os.environ.get("OPENBLAS_NUM_THREADS", "-1")))

    backend = make_backend(headless)
    runner = BodyRunner(bridge, backend, probe=probe)
    loop = FixedRateLoop(config.CONTROL_HZ)
    stepper = FixedStepper(config.CONTROL_HZ)
    bridge.mark_ready()
    logger.info("body ready (%s)", "headless" if headless else "gui")
    count = 0
    try:
        while not bridge.shutdown_requested() and not terminated:
            count += 1
            if parent_pid is not None and count % config.PARENT_CHECK_TICKS == 0 \
                    and os.getppid() != parent_pid:
                logger.warning("parent process is gone; shutting down")
                break
            if tip_event is not None and tip_event.is_set():
                tip_event.clear()
                tip = getattr(backend, "reset_base_pose", None)
                if tip is not None:
                    tip([0.0, 0.0, 0.1], _TIP_RPY)
            ticks = stepper.steps(loop.wait())
            if ticks:
                runner.step(ticks)
    except Exception:
        logger.exception("body loop crashed")
        raise SystemExit(1) from None
    finally:
        backend.close()
        logger.info("body stopped")


class BodyProcess:
    """Parent-side handle: spawns the body and shuts it down cleanly."""

    def __init__(
        self,
        bridge: Bridge,
        headless: bool = True,
        probe: BodyProbe | None = None,
        tip_event: Any = None,
    ) -> None:
        self.bridge = bridge
        self._args = (bridge, headless, probe, tip_event, os.getpid())
        self._process: Any = None

    def start(self) -> None:
        for var in _BLAS_VARS:  # the spawned interpreter inherits this before it imports numpy
            os.environ.setdefault(var, "1")
        self._process = mp.get_context("spawn").Process(
            target=run_body, args=self._args, name="hexa-body", daemon=True
        )
        self._process.start()

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def wait_ready(self, timeout: float = config.BODY_START_TIMEOUT_S) -> bool:
        """True once the body reports ready; False on timeout or if it died first."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.bridge.wait_ready(0.05):
                return True
            if not self.alive:
                return False
        return False

    def shutdown(self, timeout: float = config.BODY_SHUTDOWN_TIMEOUT_S) -> int | None:
        """Ask nicely, then terminate, then kill. Returns the exit code."""
        process = self._process
        if process is None:
            return None
        self.bridge.request_shutdown()
        process.join(timeout)
        if process.is_alive():
            logger.warning("body did not exit in %.1f s; terminating", timeout)
            process.terminate()
            process.join(timeout)
        if process.is_alive():
            logger.error("body ignored terminate; killing")
            process.kill()
            process.join(timeout)
        return process.exitcode

    def __enter__(self) -> BodyProcess:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()


def main() -> int:
    """``python -m body.process``: run an idle body until Ctrl-C (for a smoke test)."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="PyBullet DIRECT (default)")
    mode.add_argument("--gui", action="store_true", help="PyBullet window")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    try:
        if not body.wait_ready():
            logger.error("body did not start")
            return 1
        logger.info("idle; Ctrl-C to exit")
        while body.alive:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
