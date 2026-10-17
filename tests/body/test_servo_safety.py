"""The "type yes" gate, signal-driven shutdown (real signals to this process) and the
sit-then-release sequence."""

from __future__ import annotations

import io
import os
import signal
from collections.abc import Callable

import numpy as np
import pytest

import config
from body.controller import Controller, State
from body.servo_safety import ShutdownRequested, confirm_hardware, guarded, sit_and_release
from tests.fakes import FakeBackend, FakeClock


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def ask_with(answer: str | type[BaseException]) -> Callable[[str], str]:
    def ask(_prompt: str) -> str:
        if isinstance(answer, str):
            return answer
        raise answer()

    return ask


def test_confirmation_needs_a_terminal_and_the_word_yes() -> None:
    out = io.StringIO()
    assert confirm_hardware(ask=ask_with("yes"), stdin=Tty(), out=out)
    assert "REAL SERVOS" in out.getvalue()
    assert confirm_hardware(ask=ask_with("  yes \n"), stdin=Tty(), out=out)
    for answer in ("y", "YES", "no", "", "yes please"):
        assert not confirm_hardware(ask=ask_with(answer), stdin=Tty(), out=out)
    assert not confirm_hardware(ask=ask_with(EOFError), stdin=Tty(), out=out)
    assert not confirm_hardware(ask=ask_with(KeyboardInterrupt), stdin=Tty(), out=out)


def test_no_terminal_means_no_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    assert not confirm_hardware(ask=ask_with("yes"), stdin=io.StringIO("yes\n"))
    assert "interactive terminal" in capsys.readouterr().err


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_a_signal_ends_the_body_and_runs_the_cleanup_once(sig: signal.Signals) -> None:
    calls: list[str] = []
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def body() -> None:
        calls.append("body")
        os.kill(os.getpid(), sig)
        calls.append("not reached")

    code = guarded(body, lambda: calls.append("cleanup"))
    assert code == 128 + sig
    assert calls == ["body", "cleanup"]
    assert {s: signal.getsignal(s) for s in before} == before  # handlers restored


def test_a_signal_during_the_cleanup_is_ignored() -> None:
    steps: list[str] = []

    def cleanup() -> None:
        os.kill(os.getpid(), signal.SIGTERM)  # a second Ctrl-C while sitting down
        os.kill(os.getpid(), signal.SIGHUP)
        steps.append("sat down")
        steps.append("released")

    def body() -> None:
        os.kill(os.getpid(), signal.SIGINT)

    assert guarded(body, cleanup) == 128 + signal.SIGINT
    assert steps == ["sat down", "released"]


def test_a_signal_during_a_normal_cleanup_is_ignored_too() -> None:
    steps: list[str] = []

    def cleanup() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        steps.append("released")

    assert guarded(lambda: None, cleanup) == 0
    assert steps == ["released"]


def test_the_cleanup_runs_when_the_body_raises_and_a_failing_cleanup_is_reported() -> None:
    calls: list[str] = []

    def body() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        guarded(body, lambda: calls.append("cleanup"))
    assert calls == ["cleanup"]

    def bad_cleanup() -> None:
        raise OSError("bus")

    assert guarded(lambda: None, bad_cleanup) == 1


def test_shutdown_requested_is_not_an_exception() -> None:
    assert not issubclass(ShutdownRequested, Exception)  # `except Exception` cannot swallow it


class Recorded(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def standing_controller() -> tuple[Controller, Recorded, FakeClock]:
    clock = FakeClock()
    backend = Recorded()
    return Controller(backend, clock, initial_state=State.STANDING), backend, clock


def ticker(controller: Controller, clock: FakeClock) -> Callable[[], None]:
    def tick() -> None:
        clock.sleep(0.02)
        controller.tick(0.02)

    return tick


def test_a_standing_robot_sits_down_and_then_the_backend_is_closed() -> None:
    controller, backend, clock = standing_controller()
    sit_and_release(controller, backend, ticker(controller, clock), clock=clock)
    assert controller.state is State.SITTING and backend.closed == 1
    assert np.allclose(backend.history[-1], controller.angles)


def test_a_walking_robot_stops_sits_and_is_closed() -> None:
    controller, backend, clock = standing_controller()
    controller.set_velocity(0.05, 0.0, 0.0)
    tick = ticker(controller, clock)
    for _ in range(30):
        tick()
    sit_and_release(controller, backend, tick, clock=clock)
    assert controller.state is State.SITTING and backend.closed == 1


def test_a_robot_that_cannot_sit_in_time_is_released_anyway() -> None:
    controller, backend, clock = standing_controller()
    sit_and_release(controller, backend, lambda: clock.sleep(0.5), timeout_s=2.0, clock=clock)
    assert controller.state is not State.SITTING and backend.closed == 1
    assert clock.now() < 1000.0 + 3.0  # it gave up at the timeout


def test_a_failing_tick_still_releases() -> None:
    controller, backend, clock = standing_controller()

    def broken() -> None:
        raise OSError("i2c")

    sit_and_release(controller, backend, broken, clock=clock)
    assert backend.closed == 1


def test_a_sitting_robot_is_just_closed() -> None:
    clock = FakeClock()
    backend = Recorded()
    controller = Controller(backend, clock, initial_state=State.SITTING)
    ticks: list[int] = []
    sit_and_release(controller, backend, lambda: ticks.append(1), clock=clock)
    assert ticks == [] and backend.closed == 1


def test_the_default_timeout_covers_a_full_sit_down() -> None:
    assert config.SERVO_SHUTDOWN_SIT_TIMEOUT_S >= config.SIT_STAND_TRANSITION_S + config.SETTLE_S
