"""HexaApp with fakes (a real headless body process, no Vosk, no audio devices), and the whole app
with Arya: a REAL dry-run body process, FakeChat, the phone page."""

from __future__ import annotations

import os
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

import config
from brain.app import AppOptions, HexaApp, StartupError
from brain.chat import FakeChat
from brain.memory import Memory
from scripts.web_check import WebClient
from tests.fakes import FakeStt, collect, marker_block, wait_until
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


# --- app persona ------------------------------------------------------------

PIN = "909090"


def test_names_modes_chat_facts_and_forgetting_through_the_page(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EXTRACTOR_IDLE_S", 1.0)
    monkeypatch.setattr(config, "EXTRACTOR_POLL_S", 0.1)
    db = tmp_path / "arya.db"
    options = AppOptions(backend="dryrun", listen="always", chat="fake", no_speak=True,
                         no_mic=True, web=True, web_port=0, web_pin=PIN, memory_path=db)
    app = HexaApp(options, stt=FakeStt({}), source=QueueSource())
    app.start()
    try:
        assert app.web is not None and app.arya is not None and app.extractor is not None
        arya = app.arya
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        hello = client.recv(3)
        assert hello is not None and hello["persona"]["speaker"] == "guest"
        got: list[dict[str, Any]] = []

        def persona_now() -> dict[str, Any]:
            collect(client, got)
            return [m for m in got if m.get("type") == "persona"][-1]

        client.send({"action": "set_mode", "mode": "roast"})
        wait_until(lambda: arya.mode == "roast", what="roast mode")
        client.say("my name is anna")
        wait_until(lambda: arya.speaker == "Anna", what="the speaker")
        assert persona_now()["speaker"] == "Anna"

        def replied(count: int) -> bool:
            return app.chat is not None and len(app.chat.history) >= count

        for turns, line in enumerate(("I like cricket and tea", "what is a hexapod"), start=1):
            time.sleep(config.WEB_SAY_MIN_INTERVAL_S + 0.1)  # the page may say one line per 0.3 s
            client.say(line)
            wait_until(partial(replied, turns), timeout=15, what="the reply")
        person = Memory(db).get_person("anna")  # a second reader: the file is shared
        assert person is not None
        person_id = person.id
        wait_until(lambda: Memory(db).fact_count(person_id) >= 1, timeout=15, what="the fact")
        assert [f.text for f in Memory(db).facts(person.id)][0] == "Likes cricket."
        wait_until(lambda: any(p["facts"] >= 1 for p in persona_now()["people"]),
                   what="the page's fact count")

        client.send({"action": "forget_person", "id": person.id})
        wait_until(lambda: persona_now()["people"] == [], what="forgotten")
        assert Memory(db).get_person("anna") is None
        assert app.arya.speaker == config.GUEST_NAME
        client.close()
    finally:
        app.shutdown()


def test_the_app_without_memory_still_runs_the_persona(tmp_path: Path) -> None:
    options = AppOptions(backend="dryrun", listen="always", chat="fake", no_speak=True,
                         no_mic=True, memory=False, memory_path=tmp_path / "never.db")
    app = HexaApp(options, stt=FakeStt({}), source=QueueSource())
    app.start()
    try:
        assert app.arya is not None and app.memory is None and app.extractor is None
        app.submit_text("funny mode")
        wait_until(lambda: app.arya is not None and app.arya.mode == "funny", what="funny")
        assert not (tmp_path / "never.db").exists()
    finally:
        app.shutdown()


def test_a_database_that_cannot_be_opened_does_not_stop_the_robot(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    options = AppOptions(backend="dryrun", listen="always", chat="fake", no_speak=True,
                         no_mic=True, memory_path=blocker / "arya.db")  # a file is in the way
    app = HexaApp(options, stt=FakeStt({}), source=QueueSource())
    app.start()
    try:
        assert app.arya is not None and app.memory is None
    finally:
        app.shutdown()
