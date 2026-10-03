#!/usr/bin/env python3
"""Type text, the router decides, the body acts: Step 6 without voice or chat.

Spawns the body process (like ``bridge_cli``) and talks to it only through the Bridge.
Each line is routed (``brain.router.route``): a stop word stops at once, a command phrase
is sent to the body, anything else prints ``[chat] <text>`` (the chat model arrives in
Step 9). A motion keeper sends heartbeats so a walk keeps going until you say stop (or
``VOICE_WALK_MAX_S`` passes). On the dev laptop the GUI needs the Mesa override (README).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")  # single-threaded BLAS, before numpy loads (also in the child)

import config  # noqa: E402
from body.process import BodyProcess  # noqa: E402
from brain.motion_keeper import MotionKeeper  # noqa: E402
from brain.router import RouteResult, route  # noqa: E402
from bridge import Bridge, Status, make_bridge, new_command  # noqa: E402
from commandline import format_status  # noqa: E402

PUMP_S = 0.05  # status and heartbeat pump period


def describe(result: RouteResult) -> str:
    """One line saying what the router decided and why."""
    if result.kind == "chat":
        near = f" (nearest {result.phrase!r} {result.score:.0f})" if result.phrase else ""
        return f"[chat] {result.text}{near}"
    label = "STOP" if result.kind == "stop" else f"{result.action} {result.params}"
    return f"route: {label} <- {result.phrase!r} score {result.score:.0f}"


class BrainLoop:
    """Route text, send it, keep walks alive. Thread-safe: one lock around the keeper."""

    def __init__(
        self,
        bridge: Bridge,
        clock: Callable[[], float] = time.monotonic,
        max_walk_s: float = config.VOICE_WALK_MAX_S,
    ) -> None:
        self.bridge = bridge
        self.keeper = MotionKeeper(clock, max_walk_s)
        self._lock = threading.Lock()

    def handle_text(self, text: str) -> RouteResult:
        """Route *text* and send the command. A stop is sent first and never waits."""
        result = route(text)
        if result.kind == "chat":
            return result
        command = new_command(result.action or "", result.params)
        self.bridge.send(command)  # immediately; the keeper only observes afterwards
        with self._lock:
            self.keeper.on_sent(command)
        return result

    def pump(self) -> list[Status]:
        """Read the body's statuses, feed the keeper and send the heartbeats that are due."""
        statuses = self.bridge.receive_all()
        with self._lock:
            for status in statuses:
                self.keeper.on_status(status)
            beats = self.keeper.tick()
        for beat in beats:
            self.bridge.send(beat)
        return statuses


def _pump_forever(loop: BrainLoop, done: threading.Event) -> None:
    while not done.wait(PUMP_S):
        for status in loop.pump():
            print(format_status(status), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="PyBullet window")
    mode.add_argument("--headless", action="store_true", help="PyBullet DIRECT (default)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")

    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    done = threading.Event()
    try:
        if not body.wait_ready():
            print("the body process did not start", file=sys.stderr)
            return 1
        loop = BrainLoop(bridge)
        threading.Thread(target=_pump_forever, args=(loop, done), daemon=True).start()
        print("ready. Type e.g. 'walk forward', 'sit down', 'please wave', 'stop', 'quit'.",
              flush=True)
        for line in sys.stdin:
            text = line.strip()
            if text.lower() in ("quit", "exit"):
                break
            if text:
                print(describe(loop.handle_text(text)), flush=True)
        time.sleep(0.3)  # let the last statuses arrive
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
