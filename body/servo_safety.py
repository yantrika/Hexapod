"""Safety helpers around real servos: the "type yes" confirmation, signal-driven shutdown and the
"sit, then release" sequence. No hardware access of its own: it works on a ``HexapodBackend`` and
a ``Controller``.

SIGINT, SIGTERM and SIGHUP (a closed terminal, a dropped SSH session) all end in ``cleanup``,
which sits the robot down when it is standing and then releases the servos. SIGKILL and power loss
cannot be caught in software; see ``docs/HARDWARE.md`` (the OE line, the kill-9 bench test).
"""

from __future__ import annotations

import logging
import signal
import sys
from collections.abc import Callable, Sequence
from types import FrameType
from typing import TextIO

import config
from body.backend import HexapodBackend
from body.clock import Clock, MonotonicClock
from body.controller import Controller, State

logger = logging.getLogger(__name__)

# SIGHUP (a closed terminal, a dropped SSH session) exists on Linux, the Pi; Windows has none.
SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = tuple(
    getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))

CONFIRM_TEXT = (
    "REAL SERVOS will move.\n"
    "  - the robot is held OFF the ground (or the legs are free to move)\n"
    "  - the servo supply kill switch is within reach\n"
    "  - the calibration file was reviewed for the joints being driven\n"
)


class ShutdownRequested(BaseException):  # not an Exception: no `except Exception` swallows it
    """Raised in the main thread when SIGINT, SIGTERM or SIGHUP arrives."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def confirm_hardware(
    prompt: str = "",
    *,
    ask: Callable[[str], str] = input,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
) -> bool:
    """Interactive gate before any servo moves: needs a terminal and the exact word ``yes``."""
    stream = stdin if stdin is not None else sys.stdin
    if not stream.isatty():
        print("error: hardware needs an interactive terminal to confirm (type yes)",
              file=sys.stderr)
        return False
    print(CONFIRM_TEXT + prompt, file=out or sys.stdout)
    try:
        return ask("Type yes to continue: ").strip() == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


def guarded(
    body: Callable[[], None],
    cleanup: Callable[[], None],
    signals: Sequence[signal.Signals] = SHUTDOWN_SIGNALS,
) -> int:
    """Run *body*; a signal ends it and *cleanup* always runs exactly once.

    Returns 0, or ``128 + signal`` when a signal ended the body. Further signals that arrive
    during the cleanup are ignored, so a second Ctrl-C cannot cut the sit-down short.
    Must be called from the main thread.
    """
    state = {"fired": False}

    def handler(signum: int, _frame: FrameType | None) -> None:
        if state["fired"]:
            return
        state["fired"] = True
        raise ShutdownRequested(signum)

    previous = {sig: signal.signal(sig, handler) for sig in signals}
    code = 0
    try:
        try:
            body()
        except ShutdownRequested as request:
            logger.info("signal %s: shutting down", signal.Signals(request.signum).name)
            code = 128 + request.signum
        finally:
            state["fired"] = True  # the cleanup below must not be interrupted
            try:
                cleanup()
            except Exception:
                logger.exception("cleanup failed")
                code = code or 1
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
    return code


def sit_and_release(
    controller: Controller,
    backend: HexapodBackend,
    tick: Callable[[], None],
    timeout_s: float = config.SERVO_SHUTDOWN_SIT_TIMEOUT_S,
    clock: Clock | None = None,
) -> None:
    """Sit the robot down if it is not sitting (at most *timeout_s*), then close the backend.

    *tick* runs one paced controller tick. The backend is closed whatever happens here.
    """
    clk: Clock = clock if clock is not None else MonotonicClock()
    try:
        if controller.state is not State.SITTING:
            controller.stop()
            controller.sit()
            deadline = clk.now() + timeout_s
            while controller.state is not State.SITTING and clk.now() < deadline:
                tick()
            if controller.state is not State.SITTING:
                logger.warning("shutdown: not sitting after %.1f s; releasing anyway", timeout_s)
    except Exception:
        logger.exception("shutdown: sit-down failed; releasing anyway")
    finally:
        backend.close()
