"""Phone audio (ROADMAP 12c): the protocol, the limits, the network source and the whole path.

Layers, light to heavy: the validation tables (pure), ``WebControl`` on a fake clock, the
``NetworkSource``, the server over a real loopback socket with a local bridge, then the whole app
with a fake recognizer and a REAL body process, and last a Piper-rendered "sit down" through the
real Vosk (skipped without the models). No phone and no microphone are involved: frames come from
``scripts/web_check.py``'s client.
"""

from __future__ import annotations

import argparse
import builtins
import json
import logging
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
import main
from brain.app import AppOptions, HexaApp
from brain.chat import FakeChat
from brain.status_hub import StatusHub
from bridge import make_local_bridge
from scripts.web_check import Refused as HandshakeRefused
from scripts.web_check import WebClient, load_wav_16k_mono
from tests.fakes import FakeClock, FakeStt, collect, wait_until
from tests.web.test_session import Recorder
from voice.audio import NetworkSource
from voice.tts import AudioClip, read_wav, write_wav
from web.protocol import ProtocolError, Request, check_audio_frame, parse_message
from web.server import WebServer
from web.session import NO_VOICE, Refused, VoiceControls, WebControl

PIN = "246810"
GOOD_FORMAT = {"rate": 16000, "channels": 1, "format": "s16le"}


def frame(ms: int = 50, value: int = 1) -> bytes:
    return np.full(config.WEB_AUDIO_RATE * ms // 1000, value, dtype="<i2").tobytes()


# --- the protocol ----------------------------------------------------------------------------
def press_message(**fields: Any) -> str:
    return json.dumps({"action": "ptt_press", **fields})


def test_a_press_announcing_the_one_supported_format_is_phone_audio() -> None:
    assert parse_message(press_message(audio=GOOD_FORMAT)) == Request("ptt_press", audio=True)
    assert parse_message(press_message()) == Request("ptt_press")  # the robot's microphone


@pytest.mark.parametrize("audio", [
    {**GOOD_FORMAT, "rate": 8000}, {**GOOD_FORMAT, "rate": 44100}, {**GOOD_FORMAT, "rate": "16000"},
    {**GOOD_FORMAT, "rate": 16000.0}, {**GOOD_FORMAT, "rate": True},
    {**GOOD_FORMAT, "channels": 2}, {**GOOD_FORMAT, "channels": 0},
    {**GOOD_FORMAT, "format": "s16be"}, {**GOOD_FORMAT, "format": "f32le"},
    {**GOOD_FORMAT, "format": "S16LE"}, {**GOOD_FORMAT, "extra": 1},
    {"rate": 16000, "channels": 1}, {}, None, "s16le", [16000, 1, "s16le"], 7, True,
])
def test_any_other_audio_format_is_rejected_with_a_clear_error(audio: Any) -> None:
    with pytest.raises(ProtocolError, match="unsupported audio format"):
        parse_message(press_message(audio=audio))


def test_unknown_fields_on_a_press_are_still_rejected() -> None:
    with pytest.raises(ProtocolError, match="unknown field"):
        parse_message(press_message(audio=GOOD_FORMAT, pcm="x"))


@pytest.mark.parametrize(("size", "error"), [
    (0, "empty"), (1, "whole 16-bit samples"), (641, "whole 16-bit samples"),
    (config.WEB_AUDIO_MAX_FRAME_BYTES + 2, "longer than 100 ms"), (10_000, "longer than 100 ms"),
])
def test_a_malformed_frame_is_refused(size: int, error: str) -> None:
    with pytest.raises(ProtocolError, match=error):
        check_audio_frame(bytes(size))


@pytest.mark.parametrize("size", [2, 640, 1600, config.WEB_AUDIO_MAX_FRAME_BYTES])
def test_a_frame_up_to_100_ms_is_accepted(size: int) -> None:
    assert check_audio_frame(bytes(size)) == pytest.approx(size / 32000)


# --- WebControl: who may feed audio, and when ------------------------------------------------
def make_phone(**options: Any) -> tuple[WebControl, FakeClock, list[bytes], list[bool], object]:
    clock = FakeClock()
    fed: list[bytes] = []
    listening: list[bool] = []
    voice = VoiceControls(listening.append, lambda text: None, phone_audio=True,
                          feed_audio=fed.append)
    control = WebControl(Recorder(), clock.now, voice=voice, **options)
    client = object()
    control.claim(client)
    return control, clock, fed, listening, client


PHONE_PRESS = Request("ptt_press", audio=True)


def test_audio_is_fed_only_while_a_press_is_held() -> None:
    control, _, fed, listening, client = make_phone()
    assert control.handle_audio(client, frame()) == "outside"  # before any press
    control.handle(client, PHONE_PRESS)
    assert control.handle_audio(client, frame()) == "fed"
    control.handle(client, Request("ptt_release"))
    assert control.handle_audio(client, frame()) == "outside"  # in flight when the finger lifted
    assert len(fed) == 1 and listening == [True, False]
    stats = control.audio_stats
    assert (stats.fed_frames, stats.fed_bytes, stats.dropped_outside) == (1, 1600, 2)


def test_audio_from_anyone_but_the_controller_is_dropped() -> None:
    control, _, fed, _, client = make_phone()
    control.handle(client, PHONE_PRESS)
    assert control.handle_audio(object(), frame()) == "ignored"
    assert fed == [] and control.audio_stats.dropped_outside == 1
    assert not control.handle(object(), PHONE_PRESS)  # a stranger cannot press either


def test_a_press_must_match_the_mode_the_robot_runs_in() -> None:
    control, _, _, listening, client = make_phone()
    with pytest.raises(Refused, match="announce the audio format"):
        control.handle(client, Request("ptt_press"))
    robot = WebControl(Recorder(), FakeClock().now, voice=VoiceControls(
        lambda on: None, lambda text: None))
    robot.claim(client)
    with pytest.raises(Refused, match="--audio-source phone"):
        robot.handle(client, PHONE_PRESS)
    assert listening == [] and not control.listening and not robot.listening


def test_a_malformed_frame_is_counted_and_never_fed() -> None:
    control, _, fed, _, client = make_phone()
    control.handle(client, PHONE_PRESS)
    for bad in (b"", b"\x00", bytes(config.WEB_AUDIO_MAX_FRAME_BYTES + 2)):
        with pytest.raises(ProtocolError):
            control.handle_audio(client, bad)
    assert fed == [] and control.audio_stats.dropped_invalid == 3


def test_the_data_rate_limit_allows_a_burst_then_real_time_plus_half_again() -> None:
    control, clock, fed, _, client = make_phone()
    control.handle(client, PHONE_PRESS)
    verdicts = [control.handle_audio(client, frame(50)) for _ in range(30)]  # 1.5 s in no time
    assert verdicts.count("fed") == 20 and verdicts.count("rate") == 10  # the 1 s burst
    clock.sleep(1.0)  # 1.5 s of credit is earned, capped at the 1 s burst
    verdicts = [control.handle_audio(client, frame(50)) for _ in range(30)]
    assert verdicts.count("fed") == 20 and verdicts.count("rate") == 10
    assert len(fed) == 40 and control.audio_stats.dropped_rate == 20


def test_a_real_time_stream_is_never_limited() -> None:
    control, clock, fed, _, client = make_phone()
    control.handle(client, PHONE_PRESS)
    for _ in range(400):  # 20 s of 50 ms frames, one every 50 ms
        assert control.handle_audio(client, frame(50)) == "fed"
        clock.sleep(0.05)
    assert len(fed) == 400


def test_a_flood_of_tiny_frames_is_charged_as_20_ms_each() -> None:
    control, _, fed, _, client = make_phone()
    control.handle(client, PHONE_PRESS)
    verdicts = [control.handle_audio(client, frame(1)) for _ in range(200)]
    assert verdicts.count("fed") == 50  # 1 s of burst / 20 ms, not 1000 frames
    assert len(fed) == 50


def test_the_ten_second_limit_ends_audio_too() -> None:
    control, clock, fed, listening, client = make_phone(ptt_max_s=10.0)
    control.handle(client, PHONE_PRESS)
    clock.sleep(10.1)
    assert control.expire_listening()
    assert control.handle_audio(client, frame()) == "outside" and fed == []
    assert listening == [True, False]


def test_stop_and_disconnect_end_audio_and_send_stop() -> None:
    control, _, fed, listening, client = make_phone()
    sender: Recorder = control._send  # type: ignore[assignment]
    control.handle(client, PHONE_PRESS)
    control.handle(client, Request("stop"))
    assert control.handle_audio(client, frame()) == "outside"
    control.handle(client, PHONE_PRESS)
    assert control.handle_audio(client, frame()) == "fed"
    control.disconnect(client)
    assert control.handle_audio(client, frame()) == "ignored"
    assert sender.actions == ["stop", "stop"] and listening == [True, False, True, False]
    assert len(fed) == 1


# --- the network source ----------------------------------------------------------------------
def test_slow_consumer_drops_the_oldest_blocks_and_the_producer_never_waits() -> None:
    source = NetworkSource(queue_blocks=4)
    started = time.perf_counter()
    for value in range(1, 11):  # nobody reads: 10 frames into a queue of 4
        source.offer_pcm(frame(20, value))
    assert time.perf_counter() - started < 0.1
    assert source.dropped == 6
    kept = [int(source.read(0.1)[0]) for _ in range(4)]  # type: ignore[index]
    assert kept == [7, 8, 9, 10] and source.read(0.01) is None


def test_pcm_becomes_int16_blocks_and_clear_forgets_the_old_press() -> None:
    source = NetworkSource()
    source.offer_pcm((np.arange(5, dtype="<i2") - 2).tobytes())
    block = source.read(0.1)
    assert block is not None and block.dtype == np.int16 and list(block) == [-2, -1, 0, 1, 2]
    source.offer_pcm(frame())
    source.clear()
    assert source.read(0.01) is None


def test_audio_is_not_saved_unless_debug_saving_is_on(tmp_path: Path) -> None:
    quiet = NetworkSource(debug_dir=None)
    quiet.offer_pcm(frame())
    assert quiet.end_utterance() is None
    debug = NetworkSource(debug_dir=tmp_path / "out")
    debug.offer_pcm(frame(50, 7))
    debug.offer_pcm(frame(50, 9))
    saved = debug.end_utterance()
    assert saved is not None and saved.parent == tmp_path / "out"
    clip = read_wav(saved)
    assert clip.sample_rate == 16000 and list(clip.samples[[0, -1]]) == [7, 9]
    assert len(clip.samples) == 1600 and debug.end_utterance() is None
    assert [path.name for path in tmp_path.iterdir()] == ["out"]  # nothing else was written


def test_debug_saving_writes_only_into_the_dot_tmp_folder() -> None:
    app = HexaApp(AppOptions(audio_source="phone", debug_save_audio=True, web=True))
    try:
        source = app._make_source()
        assert isinstance(source, NetworkSource)
        assert source.debug_dir == config.PROJECT_ROOT / ".tmp"
    finally:
        app.shutdown()
    plain = HexaApp(AppOptions(audio_source="phone"))
    try:
        quiet = plain._make_source()
        assert isinstance(quiet, NetworkSource) and quiet.debug_dir is None
    finally:
        plain.shutdown()


# --- main.py options -------------------------------------------------------------------------
def options(*argv: str) -> argparse.Namespace:
    return main.build_parser().parse_args(argv)


@pytest.mark.parametrize(("argv", "problem"), [
    (["--audio-source", "phone"], "needs --web or --lan"),
    (["--audio-source", "phone", "--web", "--listen", "always"], "push-to-talk"),
    (["--audio-source", "phone", "--web", "--audio-file", "x.wav"], "--audio-file"),
    (["--web", "--debug-save-audio"], "needs --audio-source phone"),
])
def test_impossible_phone_audio_options_are_refused(argv: list[str], problem: str) -> None:
    assert any(problem in line for line in main.phone_audio_problems(options(*argv)))


def test_the_default_is_the_robot_microphone_and_phone_with_lan_is_fine() -> None:
    assert options().audio_source == "robot" and not options().debug_save_audio
    assert main.phone_audio_problems(options()) == []
    assert main.phone_audio_problems(options("--lan", "--audio-source", "phone")) == []
    assert main.phone_audio_problems(
        options("--web", "--audio-source", "phone", "--debug-save-audio")) == []


# --- the server over a real socket -----------------------------------------------------------
class Rig:
    def __init__(self, phone: bool) -> None:
        self.bridge = make_local_bridge()
        self.source = NetworkSource()
        self.listening: list[bool] = []
        voice = VoiceControls(self.listening.append, lambda text: None, phone_audio=phone,
                              feed_audio=self.source.offer_pcm if phone else None)
        self.server = WebServer(self.bridge, StatusHub(self.bridge), PIN, port=0, voice=voice)
        self.server.start()
        self.commands: list[str] = []

    def connect(self) -> WebClient:
        return WebClient(f"ws://127.0.0.1:{self.server.port}", PIN)

    def actions(self) -> list[str]:
        self.commands += [command.action for command in self.bridge.drain()]
        return self.commands

    @property
    def stats(self) -> Any:
        return self.server._control.audio_stats


@pytest.fixture
def phone_rig() -> Iterator[Rig]:
    rig = Rig(phone=True)
    yield rig
    rig.server.stop()


def test_hello_tells_the_app_the_audio_format_and_limits(phone_rig: Rig) -> None:
    hello = phone_rig.connect().recv(2) or {}
    assert hello["audio"] == {"available": True, "rate": 16000, "channels": 1, "format": "s16le",
                              "min_frame_ms": 20, "max_frame_ms": 100}
    assert hello["ptt"]["available"] and hello["ptt"]["max_s"] == config.WEB_PTT_MAX_S


def test_frames_reach_the_source_only_after_a_press(phone_rig: Rig) -> None:
    client = phone_rig.connect()
    client.recv(2)
    client.send_audio(frame(50, 3))  # no press yet: dropped and counted, never fed
    wait_until(lambda: phone_rig.stats.dropped_outside == 1, what="the early frame")
    client.press_audio()
    client.send_audio(frame(50, 4))
    client.send_audio(frame(50, 5))
    wait_until(lambda: phone_rig.stats.fed_frames == 2, what="the frames")
    client.release()
    client.send_audio(frame(50, 6))  # in flight at the release
    wait_until(lambda: phone_rig.stats.dropped_outside == 2, what="the late frame")
    values = []
    while (block := phone_rig.source.read(0.05)) is not None:
        values.append(int(block[0]))
    assert values == [4, 5] and phone_rig.listening == [True, False]
    client.close()


def test_a_bad_frame_gets_one_clear_error_and_the_connection_survives(phone_rig: Rig) -> None:
    client = phone_rig.connect()
    client.recv(2)
    client.press_audio()
    client.send_audio(bytes(config.WEB_AUDIO_MAX_FRAME_BYTES + 2))
    client.send_audio(b"\x01")  # a second error within a second is not repeated
    errors: list[dict] = []
    wait_until(lambda: any(m.get("type") == "error" for m in collect(client, errors)),
               what="the error")
    assert "longer than 100 ms" in errors[0]["reason"]
    wait_until(lambda: phone_rig.stats.dropped_invalid == 2, what="both frames")
    assert sum(m.get("type") == "error" for m in collect(client, errors, 0.5)) == 1
    client.send_audio(frame())
    wait_until(lambda: phone_rig.stats.fed_frames == 1, what="a good frame after")
    client.close()


def test_a_second_phone_cannot_join_and_so_cannot_feed_audio(phone_rig: Rig) -> None:
    first = phone_rig.connect()
    first.recv(2)
    with pytest.raises(HandshakeRefused) as error:
        phone_rig.connect()
    assert error.value.status == 409 and phone_rig.stats.fed_frames == 0
    first.close()


def test_a_dropped_connection_mid_utterance_releases_listening_and_stops(
    phone_rig: Rig,
) -> None:
    client = phone_rig.connect()
    client.recv(2)
    client.press_audio()
    client.send_audio(frame())
    wait_until(lambda: phone_rig.stats.fed_frames == 1, what="the frame")
    client.drop()  # Wi-Fi lost: no ptt_release, no stop
    wait_until(lambda: phone_rig.listening == [True, False], what="the release")
    wait_until(lambda: "stop" in phone_rig.actions(), what="the stop")
    assert phone_rig.bridge.stop_event.is_set()


def test_the_robot_microphone_mode_refuses_phone_audio() -> None:
    rig = Rig(phone=False)
    try:
        client = rig.connect()
        hello = client.recv(2) or {}
        assert not hello["audio"]["available"]
        assert "--audio-source phone" in hello["audio"]["reason"]
        client.press_audio()
        error = client.recv(2) or {}
        assert error.get("type") == "error" and "--audio-source phone" in error["reason"]
        client.send_audio(frame())
        wait_until(lambda: rig.stats.dropped_outside == 1, what="the frame")
        assert rig.listening == [] and rig.stats.fed_frames == 0
        client.close()
    finally:
        rig.server.stop()


def test_no_voice_at_all_drops_frames_quietly() -> None:
    bridge = make_local_bridge()
    server = WebServer(bridge, StatusHub(bridge), PIN, port=0, voice=NO_VOICE)
    server.start()
    try:
        client = WebClient(f"ws://127.0.0.1:{server.port}", PIN)
        client.recv(2)
        client.send_audio(frame())
        wait_until(lambda: server._control.audio_stats.dropped_outside == 1, what="the frame")
        client.close()
    finally:
        server.stop()


# --- the whole app: fake recognizer, real body process ---------------------------------------
WALK_MARK, STOP_MARK, CHAT_MARK, PARTIAL_MARK = 1, 2, 3, 4
SCRIPT = {WALK_MARK: [("final", "walk forward")], STOP_MARK: [("final", "stop")],
          CHAT_MARK: [("final", "tell me a joke")], PARTIAL_MARK: [("partial", "hello there")]}
REPLY = "Why did the robot cross the road? To reach the other side."


def make_phone_app(stt: Any = None, **options: object) -> HexaApp:
    defaults: dict[str, Any] = dict(listen="ptt", chat="fake", no_speak=True, web=True, web_port=0,
                                    web_pin="777777", audio_source="phone")
    defaults.update(options)
    return HexaApp(AppOptions(**defaults), stt=stt or FakeStt(SCRIPT),
                   chat_backend=FakeChat((REPLY,)))


def connect(app: HexaApp) -> WebClient:
    assert app.web is not None
    client = WebClient(f"ws://127.0.0.1:{app.web.port}", "777777")
    hello = client.recv(3) or {}
    assert hello["audio"]["available"] and hello["ptt"]["available"]
    return client


def say_marker(client: WebClient, marker: int, frames: int = 1) -> None:
    for _ in range(frames):
        client.send_audio(frame(50, marker))
        time.sleep(0.05)


def start_walking(app: HexaApp, client: WebClient) -> list[dict]:
    """Say "walk forward" in one press and wait until the robot walks (its own press: the
    server ends a press after 10 s, so a slow machine never loses the later one)."""
    client.press_audio()
    say_marker(client, WALK_MARK)
    client.release()
    seen: list[dict] = []

    def walking() -> bool:
        collect(client, seen)
        return app.brain is not None and not app.brain.body_idle()

    wait_until(walking, timeout=20, what="walking")
    return seen


def test_a_chat_sentence_in_the_stream_gets_a_reply_and_events_in_order() -> None:
    app = make_phone_app()
    app.start()
    try:
        assert isinstance(app.source, NetworkSource)  # the robot's own microphone is not opened
        client = connect(app)
        client.press_audio()
        say_marker(client, CHAT_MARK)
        client.release()
        got: list[dict] = []
        wait_until(lambda: any(m.get("type") == "said" for m in collect(client, got)),
                   timeout=15, what="Arya's reply")
        types = [m["type"] for m in got]
        assert types.index("listening") < types.index("heard") < types.index("route") < (
            types.index("said"))
        assert next(m for m in got if m["type"] == "heard")["text"] == "tell me a joke"
        assert next(m for m in got if m["type"] == "route")["route"] == "chat"
        assert next(m for m in got if m["type"] == "said")["text"].startswith("Why did")
        client.close()
    finally:
        app.shutdown()


def test_partial_text_reaches_the_phone_but_not_more_than_once_per_interval() -> None:
    app = make_phone_app()
    app.start()
    try:
        client = connect(app)
        client.press_audio()
        say_marker(client, PARTIAL_MARK, frames=4)
        got: list[dict] = []
        wait_until(lambda: any(m.get("type") == "partial" for m in collect(client, got)),
                   what="a partial")
        assert [m["text"] for m in got if m["type"] == "partial"][0] == "hello there"
        assert sum(m["type"] == "partial" for m in got) <= 1  # four partials, one interval
        client.release()
        client.close()
    finally:
        app.shutdown()


def test_stop_in_the_stream_stops_a_walking_robot() -> None:
    app = make_phone_app()
    app.start()
    try:
        client = connect(app)
        seen = start_walking(app, client)
        client.press_audio()  # a voice stop works while listening
        say_marker(client, STOP_MARK)
        wait_until(lambda: any(m.get("type") == "route" and m.get("route") == "stop"
                               for m in collect(client, seen)), timeout=15, what="the stop")
        wait_until(lambda: app.brain is not None and app.brain.body_idle(), what="idle")
        routes = [m for m in seen if m["type"] == "route"]
        assert [m["route"] for m in routes] == ["command", "stop"]
        client.close()
    finally:
        app.shutdown()


def test_a_dropped_phone_mid_utterance_releases_and_the_robot_stops() -> None:
    app = make_phone_app()
    app.start()
    try:
        client = connect(app)
        start_walking(app, client)
        client.press_audio()
        say_marker(client, 9)  # audio is flowing when the phone vanishes
        assert app.ptt is not None and app.ptt.active
        client.drop()
        wait_until(lambda: app.ptt is not None and not app.ptt.active, what="the release")
        wait_until(lambda: app.brain is not None and app.brain.body_idle(), timeout=15,
                   what="the robot stopped")
    finally:
        app.shutdown()


def test_phone_audio_is_never_written_to_disk_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scratch = config.PROJECT_ROOT / ".tmp"
    before = sorted(path.name for path in scratch.iterdir()) if scratch.is_dir() else None
    writes: list[str] = []
    real_open = builtins.open

    def watching_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in str(mode) for flag in "wax+"):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    caplog.set_level(logging.DEBUG)
    pcm = frame(50, 12345)
    app = make_phone_app()
    app.start()
    try:
        client = connect(app)
        builtins.open = watching_open
        try:
            client.press_audio()
            for _ in range(10):
                client.send_audio(pcm)
                time.sleep(0.02)
            say_marker(client, CHAT_MARK)
            client.release()
            got: list[dict] = []
            wait_until(lambda: any(m.get("type") == "said" for m in collect(client, got)),
                       timeout=15, what="the reply")
        finally:
            builtins.open = real_open
        client.close()
    finally:
        app.shutdown()
    assert writes == []
    after = sorted(path.name for path in scratch.iterdir()) if scratch.is_dir() else None
    assert before == after
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert repr(pcm[:16]) not in logged


# --- real Vosk and Piper: a rendered "sit down" streamed as the phone's microphone -----------
def test_a_streamed_sit_down_makes_the_robot_sit(
    shared_vosk: Any, speech_wavs: dict[str, Path], tmp_path: Path
) -> None:
    stereo = tmp_path / "sit-44k-stereo.wav"  # also proves the resample-to-16k-mono helper
    clip = read_wav(speech_wavs["sit down"])
    wide = np.repeat(np.interp(np.linspace(0, len(clip.samples) - 1, len(clip.samples) * 2),
                               np.arange(len(clip.samples)), clip.samples), 2).astype(np.int16)
    with wave.open(str(stereo), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(clip.sample_rate * 2)
        wav.writeframes(wide.tobytes())
    samples = load_wav_16k_mono(stereo)
    assert abs(len(samples) - len(clip.samples)) <= 2

    app = HexaApp(AppOptions(listen="ptt", chat="off", no_speak=True, web=True, web_port=0,
                             web_pin="777777", audio_source="phone"), stt=shared_vosk)
    app.start()
    try:
        client = connect(app)
        client.press_audio()
        heard = client.stream_samples(samples)
        client.release()
        wait_until(lambda: any(m.get("type") == "status" and m.get("state") == "sitting"
                               for m in collect(client, heard)), timeout=30, what="sitting")
        route = next(m for m in heard if m["type"] == "route")
        assert route["action"] == "sit" and route["route"] == "command"
        client.close()
    finally:
        app.shutdown()


def test_the_wav_helper_keeps_the_audio_it_is_given(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    tone = (np.sin(np.arange(16000) / 20) * 10000).astype(np.int16)
    write_wav(path, AudioClip(tone, 16000))
    assert np.array_equal(load_wav_16k_mono(path), tone)
