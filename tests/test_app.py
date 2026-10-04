"""Step 10b: HexaApp with fakes (a real headless body process, no Vosk, no audio devices)."""

from __future__ import annotations

import os
import threading
import time

import pytest

import config
from brain.app import AppOptions, HexaApp, StartupError
from brain.chat import FakeChat
from tests.fakes import FakeStt, marker_block, wait_until
from voice.audio import QueueSource

SCRIPT = {1: [("final", "sit down")], 2: [("final", "tell me a joke")]}


def make_app(**kwargs: object) -> tuple[HexaApp, QueueSource, FakeChat]:
    source, backend = QueueSource(), FakeChat(("Why did the robot cross the road?",))
    options = AppOptions(listen=str(kwargs.pop("listen", "always")), chat="fake", no_speak=True,
                         no_mic=True)
    app = HexaApp(options, stt=FakeStt(SCRIPT), source=source, chat_backend=backend)
    return app, source, backend


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_start_sit_chat_and_a_clean_shutdown_without_orphans() -> None:
    before = {t.name for t in threading.enumerate()}
    app, source, backend = make_app()
    app.start()
    assert app.body is not None and app.hub is not None and app.brain is not None
    pid = app.body.pid
    assert pid is not None and pid_alive(pid)
    seen = app.hub.subscribe("test")
    source.push(marker_block(1))
    deadline = time.monotonic() + 10
    done = False
    while time.monotonic() < deadline and not done:
        status = seen.get(timeout=0.1)
        done = status is not None and status.status == "done"
    assert done, "the robot did not finish sitting"
    wait_until(lambda: app.brain is not None and app.brain.body_idle(), 5, "the body to idle")
    source.push(marker_block(2))
    wait_until(lambda: backend.calls == 1, what="the chat reply")
    app.shutdown()
    assert not pid_alive(pid)  # the body process is gone
    time.sleep(1)
    left = [t.name for t in threading.enumerate() if t.name not in before]
    assert left == [], left
    app.shutdown()  # idempotent


def test_push_to_talk_is_driven_through_the_app_without_a_tty() -> None:
    app, source, _ = make_app(listen="ptt")
    app.start()
    try:
        assert app.ptt is not None and not app.ptt.active
        source.push(marker_block(2))  # nobody is listening: dropped
        assert app.voice is not None
        wait_until(lambda: app.voice is not None and app.voice.blocks_seen >= 1, what="a block")
        assert app.voice.blocks_idle == 1
        app.set_listening(True)
        assert app.ptt.active
        assert app.toggle_listening() is False and not app.ptt.active
    finally:
        app.shutdown()


def test_a_failed_start_cleans_up_and_raises_startup_error() -> None:
    options = AppOptions(chat="off", no_speak=True, no_mic=True,
                         model=str(config.PROJECT_ROOT / "no-such-model"))
    app = HexaApp(options)
    with pytest.raises(StartupError, match="Vosk model not found"):
        app.start()
    assert app.body is None  # nothing was spawned


def test_the_body_dying_is_reported() -> None:
    app, _, _ = make_app()
    app.start()
    try:
        assert app.body is not None and app.body.pid is not None
        os.kill(app.body.pid, 9)
        with pytest.raises(RuntimeError, match="body process died"):
            app.wait(threading.Event(), poll_s=0.05)
    finally:
        app.shutdown()
