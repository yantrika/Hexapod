"""The body process and real hardware: it powers up sitting, and SIGHUP / SIGTERM (and SIGINT for
real servos) end it cleanly. The dry-run backend stands in for the process wiring; the servo
shutdown sequence itself is tested in test_servo_safety.py."""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import config
from body.controller import State
from body.process import BodyProcess, BodyRunner
from bridge import make_bridge
from tests.fakes import FakeBackend, FakeClock

ROOT = Path(config.PROJECT_ROOT)
SCRIPT = """
from bridge import make_bridge
from body.process import run_body
run_body(make_bridge(), True, None, None, None, "dryrun")
print("returned", flush=True)
"""


SIGHUP = getattr(signal, "SIGHUP", signal.SIGTERM)  # Windows has none; those tests are skipped


class Physical(FakeBackend):
    is_physical = True


def test_a_real_robot_powers_up_sitting_and_a_simulated_one_stands() -> None:
    real = BodyRunner(make_bridge(), Physical(), FakeClock())
    assert real.controller.state is State.SITTING
    sim = BodyRunner(make_bridge(), FakeBackend(), FakeClock())
    assert sim.controller.state is State.STANDING


def test_the_servo_body_gets_a_longer_shutdown_than_the_others() -> None:
    assert config.SERVO_SHUTDOWN_SIT_TIMEOUT_S + 3.0 > config.BODY_SHUTDOWN_TIMEOUT_S
    assert BodyProcess(make_bridge(), True, backend="servo")._backend_kind == "servo"


class Child:
    def __init__(self) -> None:
        self.process = subprocess.Popen([sys.executable, "-c", SCRIPT], cwd=ROOT, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.lines.append(line)

    def wait_for(self, text: str, timeout: float = 30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(text in line for line in self.lines):
                return True
            time.sleep(0.05)
        return False

    def finish(self, timeout: float = 15.0) -> int:
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals: Linux and the Pi only")
@pytest.mark.parametrize("sig", [signal.SIGTERM, SIGHUP])
def test_sigterm_and_sighup_end_the_body_cleanly(sig: signal.Signals) -> None:
    child = Child()
    try:
        assert child.wait_for("body ready"), "".join(child.lines)
        child.process.send_signal(sig)
        assert child.finish() == 0
    finally:
        child.process.kill()
    assert child.wait_for("body stopped", 5.0), "".join(child.lines)


def test_sigint_is_the_parents_to_handle_for_the_simulated_backends() -> None:
    child = Child()
    try:
        assert child.wait_for("body ready"), "".join(child.lines)
        child.process.send_signal(signal.SIGINT)
        time.sleep(0.5)
        assert child.process.poll() is None  # ignored: only the parent decides
        child.process.send_signal(signal.SIGTERM)
        assert child.finish() == 0
    finally:
        child.process.kill()
