"""Step 12c: the phone's microphone. Frames in, rules on the server, TLS, and one real sentence.

The first tests use ``WebControl`` with a fake clock and a recording ``push_audio``; the next ones
a real loopback socket (plain and TLS) and the app with a fake recognizer; the last one runs the
whole app with the real Vosk model, a headless dry-run body and a Piper-rendered "sit down" sent
as binary frames (skipped without the models; heavy, run it alone).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from brain.app import AppOptions, HexaApp, StartupError
from brain.chat import FakeChat
from brain.status_hub import StatusHub
from bridge import make_local_bridge
from scripts.make_cert import alt_names
from scripts.make_cert import main as make_cert
from scripts.web_check import Refused, WebClient
from tests.fakes import FakeClock, FakeStt, collect, wait_until
from voice.audio import QueueSource, resample
from voice.tts import read_wav
from web.protocol import ProtocolError, Request, parse_audio_frame
from web.server import WebServer, make_ssl_context
from web.session import Refused as SessionRefused
from web.session import VoiceControls, WebControl

PIN = "135790"
PRESS, RELEASE = Request("ptt_press"), Request("ptt_release")
SECOND = config.AUDIO_SAMPLE_RATE * 2  # bytes of audio in one second


def frame(seconds: float = 0.1) -> bytes:
    return bytes(int(seconds * SECOND))


# --- the frame format --------------------------------------------------------------------------
def test_a_good_frame_is_accepted() -> None:
    assert parse_audio_frame(frame()) == frame()
    assert parse_audio_frame(bytes(config.WEB_AUDIO_MAX_FRAME_BYTES))


@pytest.mark.parametrize(("data", "why"), [
    (b"", "empty"),
    (bytes(config.WEB_AUDIO_MAX_FRAME_BYTES + 2), "too large"),
    (bytes(101), "16-bit"),
], ids=["empty", "oversize", "odd-length"])
def test_bad_frames_are_refused(data: bytes, why: str) -> None:
    with pytest.raises(ProtocolError, match=why):
        parse_audio_frame(data)


# --- the rules on the server -------------------------------------------------------------------
class Phone:
    def __init__(self, audio_unavailable: str | None = None) -> None:
        self.pushed: list[bytes] = []
        self.listening: list[bool] = []
        self.clock = FakeClock()
        voice = VoiceControls(self.listening.append, lambda text: None, None, None, None,
                              self.pushed.append, audio_unavailable)
        self.control = WebControl(lambda action, params: 1, self.clock.now, voice=voice)
        self.client = object()
        self.control.claim(self.client)


def test_frames_without_a_press_are_dropped() -> None:
    phone = Phone()
    assert not phone.control.audio_frame(phone.client, frame())
    assert phone.pushed == []


def test_frames_during_a_press_reach_the_recognizer_and_stop_after_the_release() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    assert phone.control.audio_frame(phone.client, frame())
    phone.control.handle(phone.client, RELEASE)
    assert not phone.control.audio_frame(phone.client, frame())  # one in flight: dropped
    assert phone.pushed == [frame()]


def test_only_the_controller_may_send_audio() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    assert not phone.control.audio_frame(object(), frame())
    assert phone.pushed == []


def test_audio_faster_than_real_time_is_refused() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    accepted = 0
    with pytest.raises(SessionRefused, match="too fast"):
        for _ in range(100):  # 10 s of audio all at once
            phone.control.audio_frame(phone.client, frame())
            accepted += 1
    limit = config.WEB_AUDIO_BURST_S / 0.1
    assert limit - 1 <= accepted <= limit + 1
    phone.clock.sleep(1.0)  # time passes: audio flows again
    assert phone.control.audio_frame(phone.client, frame())


def test_real_time_audio_is_never_refused_for_the_whole_press() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    for _ in range(int(config.WEB_PTT_MAX_S * 10) - 1):
        phone.clock.sleep(0.1)
        assert phone.control.audio_frame(phone.client, frame())


def test_the_limit_forces_a_release_and_later_frames_are_dropped() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    phone.clock.sleep(config.WEB_PTT_MAX_S + 0.1)
    assert phone.control.expire_listening()
    assert phone.listening == [True, False]
    assert not phone.control.audio_frame(phone.client, frame())


@pytest.mark.parametrize("end", ["disconnect", "stop"])
def test_a_disconnect_and_a_stop_end_the_audio(end: str) -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    if end == "stop":
        phone.control.handle(phone.client, Request("stop"))
    else:
        phone.control.disconnect(phone.client)
    assert phone.listening == [True, False]
    assert not phone.control.audio_frame(phone.client, frame())


def test_a_second_press_starts_a_fresh_allowance() -> None:
    phone = Phone()
    phone.control.handle(phone.client, PRESS)
    for _ in range(10):
        phone.control.audio_frame(phone.client, frame())
    phone.control.handle(phone.client, RELEASE)
    phone.control.handle(phone.client, PRESS)
    assert phone.control.audio_frame(phone.client, frame())


def test_without_a_phone_microphone_audio_is_dropped() -> None:
    phone = Phone(audio_unavailable="the robot uses its own microphone")
    phone.control.handle(phone.client, PRESS)
    assert not phone.control.audio_frame(phone.client, frame())


# --- a real socket: plain and TLS --------------------------------------------------------------
def serve(tmp_path: Path, tls: bool) -> tuple[WebServer, list[bytes], str]:
    pushed: list[bytes] = []
    voice = VoiceControls(lambda on: None, lambda text: None, None, None, None, pushed.append,
                          None)
    context = None
    if tls:
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        assert make_cert(["--cert", str(cert), "--key", str(key)]) == 0
        context = make_ssl_context(cert, key)
    bridge = make_local_bridge()
    server = WebServer(bridge, StatusHub(bridge), PIN, port=0, voice=voice, ssl_context=context)
    server.start()
    return server, pushed, f"{'wss' if tls else 'ws'}://127.0.0.1:{server.port}"


@pytest.mark.parametrize("tls", [False, True], ids=["plain", "tls"])
def test_frames_travel_over_the_socket(tmp_path: Path, tls: bool) -> None:
    if tls and shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    server, pushed, url = serve(tmp_path, tls)
    try:
        client = WebClient(url, PIN)
        hello = client.recv(3) or {}
        assert hello["audio"] == {"available": True, "reason": None, "rate": 16000,
                                  "max_frame_bytes": config.WEB_AUDIO_MAX_FRAME_BYTES}
        client.send_audio(frame())  # no press yet: dropped
        client.press()
        time.sleep(0.2)
        client.send_audio(frame())
        wait_until(lambda: len(pushed) == 1, what="the frame")
        client.close()
    finally:
        server.stop()


def test_a_wrong_pin_is_refused_over_tls_and_a_second_controller_too(tmp_path: Path) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    server, _pushed, url = serve(tmp_path, True)
    try:
        with pytest.raises(Refused) as wrong:
            WebClient(url, "000000")
        assert wrong.value.status == 401
        first = WebClient(url, PIN)
        with pytest.raises(Refused) as second:
            WebClient(url, PIN)
        assert second.value.status == 409
        first.close()
    finally:
        server.stop()


def test_a_bad_frame_gets_an_error_and_nothing_reaches_the_recognizer(tmp_path: Path) -> None:
    server, pushed, url = serve(tmp_path, False)
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        client.press()
        client.send_audio(bytes(7))  # not whole 16-bit samples
        errors = [m for m in client.messages(0.5) if m.get("type") == "error"]
        assert errors and "16-bit" in errors[0]["reason"]
        assert pushed == []
        client.close()
    finally:
        server.stop()


def test_an_oversize_frame_closes_the_connection(tmp_path: Path) -> None:
    server, pushed, url = serve(tmp_path, False)
    try:
        client = WebClient(url, PIN)
        client.recv(3)
        client.press()
        client.send_audio(bytes(config.WEB_AUDIO_MAX_FRAME_BYTES * 2))
        time.sleep(0.3)
        assert pushed == []

        def slot_is_free() -> bool:  # the server closed that connection, so the slot frees up
            try:
                WebClient(url, PIN).close()
            except Refused:
                return False
            return True

        wait_until(slot_is_free, timeout=5, what="the oversize frame to end the connection")
    finally:
        server.stop()


def test_the_certificate_script_names_the_hosts_and_refuses_to_overwrite(tmp_path: Path) -> None:
    assert alt_names(["a", "a", "b"], ["1.2.3.4", "1.2.3.4"]) == "DNS:a,DNS:b,IP:1.2.3.4"
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    args = ["--cert", str(cert), "--key", str(key)]
    assert make_cert(args) == 0 and cert.is_file() and key.is_file()
    before = cert.read_bytes()
    assert make_cert(args) == 1 and cert.read_bytes() == before  # not replaced
    assert make_cert([*args, "--force"]) == 0 and cert.read_bytes() != before
    subprocess.run(["openssl", "x509", "-in", str(cert), "-noout"], check=True)


def test_a_missing_certificate_is_one_clear_error(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="make_cert"):
        make_ssl_context(tmp_path / "no.pem", tmp_path / "no.key")


# --- the app ---------------------------------------------------------------------------------
def app_for(**options: Any) -> HexaApp:
    defaults: dict[str, Any] = dict(listen="ptt", chat="fake", no_speak=True, no_mic=True,
                                    web=True, web_port=0, web_pin=PIN, backend="dryrun")
    defaults.update(options)
    return HexaApp(AppOptions(**defaults), stt=FakeStt({}), source=QueueSource(),
                   chat_backend=FakeChat(("hi",)))


def test_with_no_mic_the_phone_is_the_microphone_and_frames_reach_the_source() -> None:
    app = app_for()
    app.start()
    try:
        assert app.web is not None and isinstance(app.source, QueueSource)
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        hello = client.recv(3) or {}
        assert hello["ptt"]["available"] and hello["audio"]["available"]
        client.press()
        wait_until(lambda: app.ptt is not None and app.ptt.active, what="listening")
        client.send_audio(np.arange(1600, dtype="<i2").tobytes())
        wait_until(lambda: app.voice is not None and app.voice.blocks_seen >= 1, what="a block")
        client.close()
    finally:
        app.shutdown()


@pytest.mark.parametrize(("options", "expected"), [
    ({"no_mic": False}, "own microphone"),
    ({"listen": "always"}, "--listen always"),
])
def test_without_no_mic_or_in_always_mode_there_is_no_phone_microphone(
    options: dict, expected: str
) -> None:
    app = app_for(**options)
    app.start()
    try:
        assert app.web is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        hello = client.recv(3) or {}
        assert not hello["audio"]["available"] and expected in hello["audio"]["reason"]
        client.close()
    finally:
        app.shutdown()


def test_starting_https_without_a_certificate_is_a_startup_error(tmp_path: Path) -> None:
    app = app_for(https=True, tls_cert=tmp_path / "no.pem", tls_key=tmp_path / "no.key")
    with pytest.raises(StartupError, match="make_cert"):
        app.start()


# --- real Vosk, a dry-run body process, a Piper-rendered sentence as binary frames --------------
def test_hold_to_talk_sit_down_from_the_phone_microphone(
    shared_vosk: object, speech_wavs: dict[str, Path]
) -> None:
    clip = read_wav(speech_wavs["sit down"])
    samples = resample(clip.samples, clip.sample_rate, config.AUDIO_SAMPLE_RATE)
    app = HexaApp(AppOptions(listen="ptt", chat="off", no_speak=True, no_mic=True, web=True,
                             web_port=0, web_pin=PIN, backend="dryrun"),
                  stt=shared_vosk)  # type: ignore[arg-type]
    app.start()
    try:
        assert app.web is not None
        client = WebClient(f"ws://127.0.0.1:{app.web.port}", PIN)
        assert (client.recv(3) or {})["audio"]["available"]
        client.press()
        heard: list[dict] = []
        wait_until(lambda: any(m.get("type") == "listening" and m["on"]
                               for m in collect(client, heard)), what="LISTENING")
        client.stream_audio(samples)
        client.release()
        wait_until(lambda: any(m.get("type") == "status" and m.get("state") == "sitting"
                               for m in collect(client, heard)),
                   timeout=30, what="the robot sitting")
        assert next(m for m in heard if m["type"] == "route")["action"] == "sit"
        client.close()
    finally:
        app.shutdown()
