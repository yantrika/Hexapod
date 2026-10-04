#!/usr/bin/env python3
"""Headless client of the phone page: drive the robot over the WebSocket with no browser.

    python main.py --web --no-speak --no-mic --chat fake      (prints the PIN)
    python scripts/web_check.py --pin 123456 --forward 1 --yaw 0.5 --seconds 2
    python scripts/web_check.py --pin 123456 --action wave
    python scripts/web_check.py --pin 123456 --forward 1 --seconds 2 --leave drop

It sends the same JSON messages as the page (a ``walk`` repeated at 10 Hz while held, then a
release), prints what the server answers and exits 0. Exit 1: the server refused the connection
(the HTTP status is printed: 401 wrong PIN, 409 another controller, 429 locked out). ``--leave
drop`` closes the socket without a ``stop`` to show that the server stops the robot itself.
``WebClient`` is also what the tests use.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


class Refused(Exception):
    """The handshake was refused; ``status`` is the HTTP status code."""

    def __init__(self, status: int) -> None:
        super().__init__(f"refused with HTTP {status}")
        self.status = status


class WebClient:
    """One WebSocket connection to the page's server (blocking, for scripts and tests)."""

    def __init__(self, url: str, pin: str, origin: str | None = None, timeout: float = 5.0) -> None:
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        try:
            self._ws = connect(f"{url.rstrip('/')}/ws?pin={quote(pin)}", origin=cast("Any", origin),
                               open_timeout=timeout, compression=None).__enter__()
        except InvalidStatus as error:
            raise Refused(error.response.status_code) from error

    def send(self, message: dict[str, Any] | str) -> None:
        self._ws.send(message if isinstance(message, str) else json.dumps(message))

    def recv(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """The next server message, or None if nothing arrived (or the socket closed)."""
        try:
            return cast("dict[str, Any]", json.loads(self._ws.recv(timeout=timeout)))
        except Exception:  # noqa: BLE001 - a timeout, or the server closed the socket
            return None

    def messages(self, seconds: float) -> list[dict[str, Any]]:
        """Everything the server sends during the next *seconds*."""
        out: list[dict[str, Any]] = []
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            message = self.recv(min(left, 0.1))
            if message is not None:
                out.append(message)
        return out

    def walk(self, forward: float = 0.0, strafe: float = 0.0, yaw: float = 0.0,
             speed: float | None = None) -> None:
        message: dict[str, Any] = {"action": "walk", "forward": forward, "strafe": strafe,
                                   "yaw": yaw}
        if speed is not None:
            message["speed"] = speed
        self.send(message)

    def hold(self, seconds: float, **axes: float) -> list[dict[str, Any]]:
        """Hold a move for *seconds*, repeating it at the page's rate; returns what came back."""
        period = 1.0 / config.WEB_CLIENT_SEND_HZ
        heard: list[dict[str, Any]] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.walk(**axes)
            heard += self.messages(period)
        return heard

    def close(self) -> None:
        self._ws.close()

    def drop(self) -> None:
        """Vanish like a phone that lost Wi-Fi: close the TCP socket with no closing handshake."""
        import socket

        try:
            self._ws.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._ws.socket.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=f"ws://127.0.0.1:{config.WEB_PORT}")
    parser.add_argument("--pin", required=True)
    parser.add_argument("--forward", type=float, default=0.0, help="-1 back .. +1 forward")
    parser.add_argument("--strafe", type=float, default=0.0, help="-1 right .. +1 left")
    parser.add_argument("--yaw", type=float, default=0.0,
                        help="-1 clockwise .. +1 counter-clockwise")
    parser.add_argument("--seconds", type=float, default=2.0, help="how long to hold the move")
    parser.add_argument("--action", choices=("stop", "stand", "sit", "wave"), default=None,
                        help="send this instead of a move")
    parser.add_argument("--leave", choices=("stop", "drop"), default="stop",
                        help="end with a stop message, or just close the socket")
    args = parser.parse_args(argv)

    try:
        client = WebClient(args.url, args.pin)
    except Refused as error:
        reason = {401: "wrong PIN", 403: "foreign origin", 409: "another controller",
                  429: "locked out"}.get(error.status, "?")
        print(f"refused: HTTP {error.status} ({reason})")
        return 1
    except OSError as error:
        print(f"cannot connect: {error}")
        return 1

    def show(messages: list[dict[str, Any]]) -> None:
        for message in messages:
            print(json.dumps(message))

    show(client.messages(0.3))  # the hello
    if args.action is not None:
        client.send({"action": args.action})
        show(client.messages(1.0))
    elif args.forward or args.strafe or args.yaw:
        show(client.hold(args.seconds, forward=args.forward, strafe=args.strafe, yaw=args.yaw))
        client.walk()  # release: ramp down to a halt
        show(client.messages(0.5))
    if args.leave == "stop":
        client.send({"action": "stop"})
        show(client.messages(0.5))
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
