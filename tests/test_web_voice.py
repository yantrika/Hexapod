"""Step 12b: typed text from the phone takes the spoken path; the app wires hold-to-talk.

The first tests use the voice loop with a fake recognizer and FakeChat (no audio, no body); the
last one runs the whole app with a REAL headless body process, the real Vosk model and a WAV
through a ``FileSource``: press, audio fed, release, the robot sits (skipped without the models;
heavy, run it alone).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from brain.app import AppOptions, HexaApp
from brain.chat import FakeChat
from scripts.web_check import WebClient
from tests.fakes import FakeStt, collect, wait_until
from tests.test_chat_voice import CHAT, SCRIPT, STOP, WALK, Rig
from voice.audio import Block, FileSource, QueueSource
from voice.ptt import PushToTalk


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig(FakeChat(("Why did the robot cross the road? To walk the other side.",)))
    yield made
    made.close()


def type_text(rig: Rig, text: str) -> None:
    rig.loop.submit_text(text)
    wait_until(lambda: rig.loop.events_handled >= rig.loop.events_published, what="the worker")


def sent(rig: Rig) -> list[tuple[str, dict]]:
    return [(command.action, command.params) for command in rig.bridge.drain()]


def test_typed_text_is_routed_exactly_like_the_same_spoken_text(rig: Rig) -> None:
    rig.say(WALK)  # spoken "walk forward"
    spoken = sent(rig)
    type_text(rig, "walk forward")
    typed = sent(rig)
    assert [a for a, _ in spoken] == ["walk"] and typed == spoken
    kinds = [(e.kind, e.route.kind if e.route else None) for e in rig.events]
    assert kinds[:2] == [("final", None), ("route", "command")]
    assert kinds[2:] == [("final", None), ("route", "command")]  # the same events either way


def test_a_typed_stop_stops_the_walk_first(rig: Rig) -> None:
    type_text(rig, "walk forward")
    assert not rig.brain.body_idle()
    type_text(rig, "stop")
    actions = [a for a, _ in sent(rig)]
    assert actions == ["walk", "stop"] and rig.brain.body_idle()
    assert rig.bridge.stop_event.is_set()


def test_a_typed_stop_works_in_ptt_mode_while_not_listening() -> None:
    made = Rig(FakeChat(("hi",)))
    try:
        ptt = PushToTalk()  # idle: a SPOKEN stop would be dropped, a typed one must not be
        made.loop.ptt = ptt
        type_text(made, "walk forward")
        type_text(made, "stop")
        assert [a for a, _ in sent(made)] == ["walk", "stop"] and not ptt.active
    finally:
        made.close()


def test_typed_chat_while_the_body_is_walking_says_tell_me_after_i_stop_and_calls_no_llm(
    rig: Rig,
) -> None:
    type_text(rig, "walk forward")
    type_text(rig, "tell me a joke")
    assert rig.backend.calls == 0  # the LLM was never called
    assert rig.playback.phrases == ["tell_me_after_stop"] and rig.playback.sentences == []


def test_typed_chat_while_idle_reaches_the_llm_and_is_spoken(rig: Rig) -> None:
    type_text(rig, "tell me a joke")
    wait_until(lambda: rig.backend.calls == 1 and not rig.chat.active, what="the reply")
    assert rig.backend.last_messages[-1] == {"role": "user", "content": "tell me a joke"}
    assert rig.playback.sentences[0].startswith("Why did the robot")


def test_typed_text_that_is_not_a_command_never_becomes_one(rig: Rig) -> None:
    type_text(rig, "I sat down for lunch")  # the router's negative cases apply to typing too
    assert sent(rig) == []


def test_typing_interrupts_hexa_like_a_press_does(rig: Rig) -> None:
    before = rig.loop.barge_ins
    type_text(rig, "tell me a joke")
    assert rig.loop.barge_ins == before + 1 and rig.playback.skip_tail_clears >= 1


def test_every_marker_in_the_script_is_still_what_the_chat_tests_expect() -> None:
    assert (WALK, CHAT, STOP) == (1, 3, 4) and "stop" in str(SCRIPT[STOP])


# --- the app: what the page may do depends on how the robot was started ---------------------
def make_app(**options: object) -> HexaApp:
    defaults: dict[str, object] = dict(listen="ptt", chat="fake", no_speak=True, no_mic=False,
                                       web=True, web_port=0, web_pin="777777")
    defaults.update(options)
    return HexaApp(AppOptions(**defaults),  # type: ignore[arg-type]
                   stt=FakeStt({}), source=QueueSource(), chat_backend=FakeChat(("hi",)))


@pytest.mark.parametrize(("options", "expected"), [
    ({}, None),
    ({"no_mic": True}, "--no-mic"),
    ({"listen": "always"}, "--listen always"),
])
def test_the_app_tells_the_page_when_hold_to_talk_cannot_work(
    options: dict, expected: str | None
) -> None:
    app = make_app(**options)
    app.start()
    try:
        assert app.web is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", "777777")
        hello = client.recv(3) or {}
        ptt = hello["ptt"]
        if expected is None:
            assert ptt["available"] and ptt["reason"] is None
        else:
            assert not ptt["available"] and expected in ptt["reason"]
            client.press()
            assert "error" == (client.recv(2) or {}).get("type")
        assert hello["say"]["available"]  # typing always works
        client.close()
    finally:
        app.shutdown()


def test_the_app_publishes_heard_route_said_and_listening_events_to_the_page() -> None:
    stt = FakeStt({}, flush_events=[("final", "tell me a joke")])
    app = HexaApp(AppOptions(listen="ptt", chat="fake", no_speak=True, web=True, web_port=0,
                             web_pin="777777"),
                  stt=stt, source=QueueSource(), chat_backend=FakeChat(("Hello there.",)))
    app.start()
    try:
        assert app.web is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", "777777")
        client.recv(3)
        client.press()
        wait_until(lambda: app.ptt is not None and app.ptt.active, what="listening")
        client.release()
        got: list[dict] = []
        wait_until(lambda: any(m.get("type") == "said" for m in collect(client, got)),
                   timeout=10, what="the reply")
        types = [m.get("type", "") for m in got]
        assert types.index("listening") < types.index("heard") < types.index("route") < (
            types.index("said"))
        client.say("walk forward")
        typed: list[dict] = []
        wait_until(lambda: any(m.get("type") == "heard" and m.get("text") == "walk forward"
                               for m in collect(client, typed)), what="typed text heard")
        client.close()
    finally:
        app.shutdown()


# --- real Vosk, real body process, a WAV through a FileSource --------------------------------
class GatedSource:
    """A ``FileSource`` that stays silent until the test opens the gate (the audio is "fed" only
    after the press; in ptt mode blocks that arrive while idle are dropped, like a real mic)."""

    def __init__(self, inner: FileSource, gate: threading.Event) -> None:
        self.inner, self.gate = inner, gate

    def start(self) -> None:
        self.inner.start()

    def stop(self) -> None:
        self.inner.stop()

    def read(self, timeout: float) -> Block | None:
        if not self.gate.is_set():
            time.sleep(min(timeout, 0.05))
            return None
        return self.inner.read(timeout)


def test_press_audio_release_and_the_robot_sits(
    shared_vosk: object, speech_wavs: dict[str, Path]
) -> None:
    gate = threading.Event()
    clip = FileSource(speech_wavs["sit down"], realtime=True, pad_silence_s=1.0)
    source = GatedSource(clip, gate)
    app = HexaApp(AppOptions(listen="ptt", chat="off", no_speak=True, web=True, web_port=0,
                             web_pin="777777"), source=source)
    app.start()
    try:
        assert app.web is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", "777777")
        assert client.recv(3)["ptt"]["available"]  # type: ignore[index]
        client.press()
        heard: list[dict] = []
        wait_until(lambda: any(m.get("type") == "listening" and m["on"]
                               for m in collect(client, heard)), what="LISTENING")
        gate.set()  # the audio flows now

        def spoken() -> bool:
            collect(client, heard)
            end = clip.speech_end_at
            return end is not None and time.monotonic() > end + 0.2

        wait_until(spoken, timeout=20, what="the sentence to be spoken")
        client.release()
        wait_until(lambda: any(m.get("type") == "status" and m.get("state") == "sitting"
                               for m in collect(client, heard)),
                   timeout=30, what="the robot sitting")
        types = [m["type"] for m in heard]
        assert "heard" in types and "route" in types
        assert next(m for m in heard if m["type"] == "route")["action"] == "sit"
        client.close()
    finally:
        app.shutdown()
