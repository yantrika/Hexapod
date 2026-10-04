#!/usr/bin/env python3
"""Phone-page latency over the network: laptop -> Pi and back, with no browser.

    python main.py --lan --backend dryrun --no-mic --no-speak --chat fake      (on the Pi)
    python scripts/measure_lan.py --url ws://hexa.local:8765 --pin 424242      (on this machine)

Per trial it sends `sit` or `stand` alternately and times the send until the server's first
status answer (accepted or rejected) arrives, then waits for the move to finish. It also times
the typed `say` reply (it includes the fake chat's own delay), and the WebSocket connect.
Median, p95 and max are printed. Run it from a machine on the same Wi-Fi as the Pi: the
numbers include the Wi-Fi.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.web_check import WebClient  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summary(label: str, values: list[float]) -> None:
    print(f"{label}: median {statistics.median(values):.0f} ms, "
          f"p95 {percentile(values, 0.95):.0f} ms, max {max(values):.0f} ms "
          f"({len(values)} samples)")


def wait_for(client: WebClient, wanted: str, timeout: float) -> float | None:
    """Seconds until a message of type *wanted* arrives (None on timeout)."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        message = client.recv(0.05)
        if message and message.get("type") == wanted:
            return time.monotonic() - start
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="ws://host:port of the page")
    parser.add_argument("--pin", required=True)
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()

    started = time.monotonic()
    client = WebClient(args.url, args.pin)
    connect_ms = (time.monotonic() - started) * 1000.0
    print(f"connect + handshake: {connect_ms:.0f} ms")
    client.messages(0.3)  # drain the hello
    actions, said = [], []
    for trial in range(args.trials):
        action = "sit" if trial % 2 == 0 else "stand"
        client.send({"action": action})
        seconds = wait_for(client, "status", 3.0)
        if seconds is not None:
            actions.append(seconds * 1000.0)
        client.messages(2.5)  # let the move finish
        client.say("hello")
        seconds = wait_for(client, "said", 3.0)
        if seconds is not None:
            said.append(seconds * 1000.0)
        client.messages(0.5)
    client.send({"action": "stop"})
    if actions:
        summary("button press -> server answer", actions)
    if said:
        summary("typed text -> first reply (fake chat delay)", said)
    client.close()
    return 0 if actions else 1


if __name__ == "__main__":
    raise SystemExit(main())
