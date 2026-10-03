#!/usr/bin/env python3
"""Type commands, see the body's status replies: the Step 5 bridge, by hand.

Runs in the parent process, spawns the body process and talks to it only through
the Bridge. Lines are parsed by ``commandline.parse_line``: ``stand``, ``sit``,
``wave``, ``stop``, ``walk fwd 0.5``, ``walk back``, ``strafe left 0.5``,
``turn left 90``, ``heartbeat``, ``quit``. A heartbeat is sent automatically while
the CLI runs (``--no-heartbeat`` shows the watchdog stopping a walk after a second).
On the dev laptop the GUI needs the Mesa override (README).
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
from bridge import Bridge, make_bridge, new_command  # noqa: E402
from commandline import HELP, format_status, parse_line  # noqa: E402


def _listen(bridge: Bridge, stop: threading.Event) -> None:
    while not stop.is_set():
        status = bridge.receive(timeout=0.1)
        if status is not None:
            print(format_status(status), flush=True)


def _beat(bridge: Bridge, stop: threading.Event) -> None:
    while not stop.wait(1.0 / config.HEARTBEAT_HZ):
        bridge.send(new_command("heartbeat"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="PyBullet window")
    mode.add_argument("--headless", action="store_true", help="PyBullet DIRECT (default)")
    parser.add_argument("--no-heartbeat", action="store_true", help="do not send heartbeats")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    done = threading.Event()
    try:
        if not body.wait_ready():
            print("the body process did not start", file=sys.stderr)
            return 1
        threads = [threading.Thread(target=_listen, args=(bridge, done), daemon=True)]
        if not args.no_heartbeat:
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
        time.sleep(0.3)  # let the last statuses arrive
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
