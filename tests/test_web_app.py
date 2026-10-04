"""Step 12a: the phone page against a REAL headless body process (PyBullet DIRECT).

Heavy (it starts the physics body): run it alone, through the heat guard:
``python scripts/cool_run.py -- nice -n 19 pytest tests/test_web_app.py``.
One test builds ``HexaApp`` in-process with the body's ``BodyProbe`` (did the joint targets move,
and when); one runs ``main.py --web`` as a real process (skipped without the Vosk model).
"""

from __future__ import annotations

import re
import signal
import socket
import threading
import time
from pathlib import Path

import pytest

import config
from body.process import BodyProbe
from brain.app import AppOptions, HexaApp
from brain.chat import FakeChat
from scripts.web_check import Refused, WebClient
from tests.fakes import FakeStt, wait_until
from tests.test_main_smoke import Running, pid_alive
from voice.audio import QueueSource

PIN = "135790"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def port_closed(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def test_a_web_walk_moves_the_body_and_a_dropped_connection_stops_it() -> None:
    before = {thread.name for thread in threading.enumerate()}
    probe = BodyProbe()
    options = AppOptions(listen="always", chat="fake", no_speak=True, no_mic=True, web=True,
                         web_port=0, web_pin=PIN)
    app = HexaApp(options, probe=probe, stt=FakeStt({}), source=QueueSource(),
                  chat_backend=FakeChat(("hi",)))
    app.start()
    try:
        assert app.web is not None and app.body is not None
        pid, port = app.body.pid, app.web.port
        assert pid is not None and pid_alive(pid)
        with pytest.raises(Refused):  # the PIN is enforced on the real stack too
            WebClient(f"ws://127.0.0.1:{port}", "000000")
        client = WebClient(f"ws://127.0.0.1:{port}", PIN)
        assert (client.recv(2) or {}).get("type") == "hello"

        started = time.monotonic()
        probe.set("change_time", 0.0)
        heard = client.hold(1.0, forward=1.0, yaw=1.0)  # forward + turn: one combined walk
        assert probe.get("change_time") > started, "the joint targets never moved"
        assert any(m.get("type") == "status" and m["state"] == "walking" for m in heard), heard

        client.drop()  # a vanished phone: no stop message, no closing handshake
        dropped = time.monotonic()
        time.sleep(0.8)
        assert probe.get("change_time") - dropped < 0.35, "the body kept moving after the drop"
        settled = probe.get("change_time")
        time.sleep(0.4)
        assert probe.get("change_time") == settled  # and it stays still (holding the pose)

        second = WebClient(f"ws://127.0.0.1:{port}", PIN)  # the controller slot was freed
        assert (second.recv(2) or {}).get("type") == "hello"
        second.send({"action": "sit"})
        wait_until(lambda: any(m.get("state") == "sitting" for m in second.messages(0.2)),
                   timeout=15, what="the robot sitting")
        second.close()
    finally:
        app.shutdown()
    assert pid is not None and not pid_alive(pid)  # no orphan body
    assert port_closed(port)
    time.sleep(1)
    assert [t.name for t in threading.enumerate() if t.name not in before] == []


@pytest.fixture()
def needs_vosk(shared_vosk: object) -> None:
    """Skips the test when the Vosk model is missing (``main.py`` loads it at start)."""


def test_main_web_as_a_real_process_walks_stops_on_disconnect_and_exits_clean(
    needs_vosk: None, tmp_path: Path
) -> None:
    port = free_port()
    run = Running(["--web", "--web-port", str(port)], tmp_path)
    try:
        assert run.wait_for("hexa is ready", 90), run.text
        match = re.search(r"web PIN: (\d{6})", run.text)
        assert match, run.text  # a generated PIN is printed on the terminal
        pin = match.group(1)
        assert f"web page: http://127.0.0.1:{port}/" in run.text
        pid = run.body_pid
        with pytest.raises(Refused) as refused:
            WebClient(f"ws://127.0.0.1:{port}", "999999" if pin != "999999" else "000000")
        assert refused.value.status == 401

        client = WebClient(f"ws://127.0.0.1:{port}", pin)
        assert (client.recv(2) or {}).get("type") == "hello"
        heard = client.hold(0.8, forward=1.0)
        assert any(m.get("type") == "status" and m["status"] == "accepted" for m in heard), heard
        client.drop()
        assert run.wait_for("web: controller disconnected", 10), run.text
        run.process.send_signal(signal.SIGTERM)
        code = run.finish(40)
    except Exception:
        run.process.kill()
        raise
    assert code == 0, run.text
    assert "hexa stopped" in run.text
    assert not pid_alive(pid)
    assert port_closed(port)
    log = (tmp_path / "hexa.log").read_text()
    assert pin not in log  # the PIN is printed on the terminal only, never logged
    assert re.search(r"status <- done\s+ref=\d+\s+action=stop", log), log[-1500:]
    assert f"/ws?pin={pin}" not in log


def test_lan_flag_is_documented_and_defaults_off() -> None:
    from main import build_parser

    args = build_parser().parse_args([])
    assert args.web is False and args.lan is False and args.web_port == config.WEB_PORT
    assert build_parser().parse_args(["--lan"]).lan is True
