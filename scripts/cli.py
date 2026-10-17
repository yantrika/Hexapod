#!/usr/bin/env python3
"""Type commands to the robot, see what the body answers. No voice, no chat model.

    python scripts/cli.py              each line goes through the router, like a spoken sentence
    python scripts/cli.py --raw        each line is a bridge command, no router
    python scripts/cli.py --gui        the same with the PyBullet window (README: OpenGL override)

Default mode: a stop word stops at once, a command phrase ("walk forward", "please wave") is sent
to the body, anything else prints ``[chat] <text>`` and a persona request prints ``[persona]``
(nothing reaches the body). A motion keeper sends heartbeats so a walk keeps going until you say
stop (or ``VOICE_WALK_MAX_S`` passes).

``--raw`` mode: lines are parsed by ``brain.commandline.parse_line``: ``stand``, ``sit``, ``wave``,
``stop``, ``walk fwd 0.5``, ``walk back``, ``strafe left 0.5``, ``turn left 90``, ``heartbeat``.
A heartbeat is sent automatically (``--no-heartbeat`` shows the watchdog stopping a walk after a
second). Raw mode reads the Bridge directly, so never run it beside a status hub.

The CLI runs in the parent process, spawns the body process and talks to it only through the
Bridge. ``quit`` ends it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")  # single-threaded BLAS, before numpy loads (also in the child)

import config  # noqa: E402
from body.process import BodyProcess  # noqa: E402
from brain.brain_loop import BrainLoop  # noqa: E402
from brain.commandline import HELP, format_status, parse_line  # noqa: E402
from brain.router import RouteResult  # noqa: E402
from bridge import Bridge, make_bridge, new_command  # noqa: E402

PUMP_S = 0.05  # status and heartbeat pump period (router mode)


def describe(result: RouteResult) -> str:
    """One line saying what the router decided and why."""
    if result.kind == "chat":
        near = f" (nearest {result.phrase!r} {result.score:.0f})" if result.phrase else ""
        return f"[chat] {result.text}{near}"
    if result.kind == "persona":
        return f"[persona] {result.action} {result.params} (nothing is sent to the body)"
    label = "STOP" if result.kind == "stop" else f"{result.action} {result.params}"
    return f"route: {label} <- {result.phrase!r} score {result.score:.0f}"


def _pump_forever(loop: BrainLoop, done: threading.Event) -> None:
    while not done.wait(PUMP_S):
        for status in loop.pump():
            print(format_status(status), flush=True)


def _listen(bridge: Bridge, stop: threading.Event) -> None:
    while not stop.is_set():
        status = bridge.receive(timeout=0.1)
        if status is not None:
            print(format_status(status), flush=True)


def _beat(bridge: Bridge, stop: threading.Event) -> None:
    while not stop.wait(1.0 / config.HEARTBEAT_HZ):
        bridge.send(new_command("heartbeat"))


def _run_router(bridge: Bridge, done: threading.Event) -> BrainLoop:
    loop = BrainLoop(bridge)
    threading.Thread(target=_pump_forever, args=(loop, done), daemon=True).start()
    print("ready. Type e.g. 'walk forward', 'sit down', 'please wave', 'stop', 'quit'.", flush=True)
    for line in sys.stdin:
        text = line.strip()
        if text.lower() in ("quit", "exit"):
            break
        if text:
            print(describe(loop.handle_text(text)), flush=True)
    return loop


def _run_raw(bridge: Bridge, done: threading.Event, heartbeat: bool) -> None:
    threads = [threading.Thread(target=_listen, args=(bridge, done), daemon=True)]
    if heartbeat:
        threads.append(threading.Thread(target=_beat, args=(bridge, done), daemon=True))
    for thread in threads:
        thread.start()
    print(f"ready. {HELP} | quit", flush=True)
    for line in sys.stdin:
        if line.strip().lower() in ("quit", "exit", "q"):
            break
        parsed = parse_line(line)
        if parsed is None:
            if line.strip():
                print(f"?? {line.strip()!r}: not a command", flush=True)
            continue
        action, params = parsed
        command = new_command(action, params)
        bridge.send(command)
        print(f"-> {action} {params} seq={command.seq}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="PyBullet window")
    mode.add_argument("--headless", action="store_true", help="PyBullet DIRECT (default)")
    parser.add_argument("--raw", action="store_true", help="bridge commands, no router")
    parser.add_argument("--no-heartbeat", action="store_true",
                        help="--raw only: do not send heartbeats")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.raw else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    done = threading.Event()
    loop: BrainLoop | None = None
    try:
        if not body.wait_ready():
            print("the body process did not start", file=sys.stderr)
            return 1
        if args.raw:
            _run_raw(bridge, done, heartbeat=not args.no_heartbeat)
        else:
            loop = _run_router(bridge, done)
        time.sleep(0.3)  # let the last statuses arrive
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        if loop is not None:
            loop.close()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
