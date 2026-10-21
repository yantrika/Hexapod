"""Step 12c-2: Arya's voice on the phone. The sink, the message format, the server's outbox rules
(order, a slow phone, clear, only the controller) and the whole app with ``--phone-voice``.

The server tests use a real loopback socket; the app test uses a fake TTS engine, so no model is
needed and nothing here makes a sound.
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Any

import numpy as np
import pytest

import config
from brain.app import AppOptions, HexaApp, StartupError
from brain.chat import FakeChat
from brain.startup import CheckOptions, run_checks
from brain.status_hub import StatusHub
from bridge import make_local_bridge
from scripts.web_check import WebClient
from tests.fakes import FakeStt, wait_until
from voice.audio import QueueSource
from voice.playback import PhoneSink
from voice.tts import AudioClip
from web.protocol import VOICE_HEADER, ProtocolError, encode_voice_clip, parse_message
from web.server import WebServer
from web.session import VoiceControls

PIN = "246810"
RATE = 22050


def clip(seconds: float = 0.1, value: int = 7) -> AudioClip:
    return AudioClip(np.full(int(seconds * RATE), value, dtype=np.int16), RATE)


def decode(payload: bytes) -> tuple[int, np.ndarray]:
    kind, rate = VOICE_HEADER.unpack_from(payload)
    assert kind == b"A"
    return rate, np.frombuffer(payload, dtype="<i2", offset=VOICE_HEADER.size)


# --- the format ------------------------------------------------------------------------------
def test_the_voice_action_needs_a_real_boolean() -> None:
    assert parse_message('{"action": "voice", "on": true}').on is True
    assert parse_message('{"action": "voice", "on": false}').on is False
    for bad in ('{"action": "voice"}', '{"action": "voice", "on": 1}',
                '{"action": "voice", "on": "yes"}', '{"action": "voice", "on": true, "x": 1}'):
        with pytest.raises(ProtocolError):
            parse_message(bad)


def test_a_clip_message_has_an_aligned_header_and_the_samples() -> None:
    payload = encode_voice_clip(np.array([1, -2, 3], dtype="<i2").tobytes(), RATE)
    assert VOICE_HEADER.size == 8 and payload[:1] == b"A"
    assert struct.unpack_from("<I", payload, 4)[0] == RATE
    assert decode(payload)[1].tolist() == [1, -2, 3]


# --- the sink --------------------------------------------------------------------------------
def test_the_sink_hands_over_the_clip_and_waits_its_real_duration() -> None:
    got: list[AudioClip] = []
    sink = PhoneSink(on_clip=got.append)
    started = time.monotonic()
    sink.start(clip(0.25))
    sink.wait()
    assert 0.2 <= time.monotonic() - started <= 0.8
    assert len(got) == 1


def test_abort_ends_the_wait_at_once_and_clears_the_phone() -> None:
    cleared: list[bool] = []
    sink = PhoneSink(on_clear=lambda: cleared.append(True))
    sink.start(clip(5.0))
    threading.Timer(0.05, sink.abort).start()
    started = time.monotonic()
    sink.wait()
    assert time.monotonic() - started < 1.0 and cleared == [True]


def test_a_broken_phone_link_never_stops_the_speech() -> None:
    def boom(*_args: Any) -> None:
        raise RuntimeError("link down")

    sink = PhoneSink(on_clip=boom, on_clear=boom)
    sink.start(clip(0.05))
    sink.wait()
    sink.abort()


# --- the server ------------------------------------------------------------------------------
def serve(voice_out: bool = True) -> tuple[WebServer, str]:
    voice = VoiceControls(lambda on: None, lambda text: None, None, None)
    bridge = make_local_bridge()
    server = WebServer(bridge, StatusHub(bridge), PIN, port=0, voice=voice, voice_out=voice_out)
    server.start()
    return server, f"ws://127.0.0.1:{server.port}"


def test_hello_says_whether_the_voice_can_go_to_the_phone() -> None:
    for enabled in (True, False):
        server, url = serve(enabled)
        try:
            client = WebClient(url, PIN)
            assert (client.recv(3) or {})["voice"] == {"available": enabled}
            client.close()
        finally:
            server.stop()


def test_voice_arrives_in_order_only_after_the_page_asks_for_it() -> None:
    server, url = serve()
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        server.send_audio(clip(value=1))  # nobody asked yet: dropped
        client.voice(True)
        time.sleep(0.2)
        server.send_audio(clip(value=2))
        server.send_audio(clip(value=3))
        client.messages(0.4)
        assert [int(decode(c)[1][0]) for c in client.clips] == [2, 3]
        assert decode(client.clips[0])[0] == RATE
        client.voice(False)
        time.sleep(0.2)
        server.send_audio(clip(value=4))
        client.messages(0.4)
        assert len(client.clips) == 2
        client.close()
    finally:
        server.stop()


def test_clear_audio_reaches_the_page_and_keeps_the_order_with_later_speech() -> None:
    server, url = serve()
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        client.voice(True)
        time.sleep(0.2)
        server.send_audio(clip(value=1))
        server.clear_audio()
        server.send_audio(clip(value=2))
        seen: list[str] = []
        end = time.monotonic() + 0.8
        while time.monotonic() < end:
            before = len(client.clips)
            message = client.recv(0.1)
            if message is not None and message.get("type") == "audio_clear":
                seen.append("clear")
            seen += ["clip"] * (len(client.clips) - before)
        # the first sentence is either sent (then cleared) or dropped unsent; the second follows
        assert seen in (["clear", "clip"], ["clip", "clear", "clip"])
        client.close()
    finally:
        server.stop()


def test_a_slow_phone_loses_sentences_and_the_sender_never_waits() -> None:
    server, url = serve()
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        client.voice(True)
        time.sleep(0.2)
        big = AudioClip(np.zeros(RATE * 20, dtype=np.int16), RATE)  # 880 KB each
        started = time.monotonic()
        for _ in range(40):
            server.send_audio(big)  # the phone reads nothing at all
        assert time.monotonic() - started < 3.0
        time.sleep(0.5)
        held = server._client  # noqa: SLF001 - what the server holds back for this phone
        assert held is not None and held.audio_queued <= config.WEB_VOICE_MAX_CLIPS
        client.close()
    finally:
        server.stop()


def test_asking_for_the_voice_without_phone_voice_is_refused() -> None:
    server, url = serve(voice_out=False)
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        client.voice(True)
        errors = [m for m in client.messages(0.4) if m.get("type") == "error"]
        assert errors and "phone-voice" in errors[0]["reason"]
        server.send_audio(clip())
        assert client.messages(0.3) == [] and client.clips == []
        client.close()
    finally:
        server.stop()


# --- the app ---------------------------------------------------------------------------------
class FakePiper:
    """Stands in for ``PiperEngine``: a short tone per sentence (the app checks, starts, uses and
    closes it)."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def check_installed(self) -> None:
        pass

    def start(self) -> None:
        pass

    def synthesize(self, text: str) -> AudioClip:
        self.texts.append(text)
        return clip(0.3, value=len(self.texts))

    def close(self) -> None:
        pass


def phone_app(**options: Any) -> HexaApp:
    defaults: dict[str, Any] = dict(listen="ptt", chat="fake", no_mic=True, web=True, web_port=0,
                                    web_pin=PIN, backend="dryrun", phone_voice=True,
                                    persona=False)
    defaults.update(options)
    return HexaApp(AppOptions(**defaults), stt=FakeStt({}), source=QueueSource(),
                   chat_backend=FakeChat(("hi",)))


def test_phone_voice_needs_the_page_and_speech() -> None:
    for options in ({"web": False}, {"no_speak": True}):
        with pytest.raises(StartupError, match="phone-voice"):
            phone_app(**options).start()


def test_phone_voice_needs_no_sound_device() -> None:
    options = CheckOptions(no_mic=True, chat="fake", phone_voice=True)
    assert not any("output" in problem for problem in run_checks(options))


def test_arya_speaks_to_the_phone_and_the_robot_is_deaf_meanwhile(monkeypatch: Any) -> None:
    piper = FakePiper()
    monkeypatch.setattr("brain.app.PiperEngine", lambda: piper)
    app = phone_app()
    app.start()
    try:
        assert app.web is not None and app.phone_sink is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        assert (client.recv(3) or {})["voice"]["available"]
        client.voice(True)
        time.sleep(0.2)
        app.speech.say("One. Two.")
        wait_until(app.speaking.is_set, what="speaking")  # the phone counts as the speaker
        wait_until(lambda: client.messages(0.1) is not None and len(client.clips) == 2,
                   timeout=10, what="two sentences on the phone")
        assert piper.texts == ["One.", "Two."]
        app.speech.say("Three.")
        wait_until(lambda: client.messages(0.1) is not None and len(client.clips) == 3,
                   timeout=10, what="Three. on the phone")  # it is playing now
        app.speech.clear(skip_tail=True)  # barge-in
        cleared = [m for m in client.messages(0.6) if m.get("type") == "audio_clear"]
        assert cleared and not app.speaking.is_set()
        client.close()
    finally:
        app.shutdown()
