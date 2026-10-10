"""Step 13 (P1): the whole app with Arya: a REAL dry-run body process, FakeChat, the phone page.

Typed lines go through the page's ``say``: the persona routes, the persona prompt, the memory
extractor (the rule-based stand-in) and the persona events back to the page. Heavy (starts the
body process): run it alone.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

import config
from brain.app import AppOptions, HexaApp
from brain.memory import Memory
from scripts.web_check import WebClient
from tests.fakes import FakeStt, collect, wait_until
from voice.audio import QueueSource

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
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        hello = client.recv(3)
        assert hello is not None and hello["persona"]["speaker"] == "guest"
        got: list[dict[str, Any]] = []

        def persona_now() -> dict[str, Any]:
            collect(client, got)
            return [m for m in got if m.get("type") == "persona"][-1]

        client.send({"action": "set_mode", "mode": "roast"})
        wait_until(lambda: app.arya.mode == "roast", what="roast mode")
        client.say("my name is anna")
        wait_until(lambda: app.arya.speaker == "Anna", what="the speaker")
        assert persona_now()["speaker"] == "Anna"

        for turns, line in enumerate(("I like cricket and tea", "what is a hexapod"), start=1):
            time.sleep(config.WEB_SAY_MIN_INTERVAL_S + 0.1)  # the page may say one line per 0.3 s
            client.say(line)
            wait_until(lambda n=turns: app.chat is not None and len(app.chat.history) >= n,
                       timeout=15, what="the reply")
        person = Memory(db).get_person("anna")  # a second reader: the file is shared
        assert person is not None
        wait_until(lambda: Memory(db).fact_count(person.id) >= 1, timeout=15, what="the fact")
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
