"""Step 8: self-hearing protection and the voice loop's logic, with a fake recognizer.

No microphone, no speaker, no Vosk model: blocks are marked with a number and ``FakeStt`` turns
a marker into scripted results. The real recognizer is exercised in ``test_voice_loop.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from brain.brain_loop import BrainLoop
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import make_local_bridge
from tests.fakes import (
    FakeClock,
    FakeEngine,
    FakeSink,
    FakeStt,
    marker_block,
    run_fake_time,
    wait_until,
)
from voice.audio import Block, EndOfAudio, QueueSource
from voice.playback import Playback
from voice.stt import SelfHearingGate
from voice.tts import AudioClip, write_wav

WALK = 1  # block markers
SIT = 2
CHAT = 3
PARTIAL_STOP = 4
FINAL_STOP = 5
SCRIPT = {
    WALK: [("final", "walk forward")],
    SIT: [("final", "sit down")],
    CHAT: [("final", "I sat down for lunch")],
    PARTIAL_STOP: [("partial", "stop")],
    FINAL_STOP: [("final", "stop")],
}


class Rig:
    def __init__(
        self,
        stt: FakeStt | None = None,
        playback: Any = None,
        source: Any = None,
    ) -> None:
        self.bridge = make_local_bridge()
        self.speaking = threading.Event()
        self.source = source if source is not None else QueueSource()
        self.stt = stt if stt is not None else FakeStt(SCRIPT)
        self.brain = BrainLoop(self.bridge)
        self.events: list[VoiceEvent] = []
        self.loop = VoiceLoop(
            self.source, self.stt, self.brain, self.speaking, playback, self.events.append
        )
        self.loop.start()

    def push(self, marker: int) -> None:
        self.source.push(marker_block(marker))

    def settle(self, blocks: int) -> None:
        wait_until(lambda: self.loop.blocks_seen >= blocks, what=f"{blocks} blocks to be read")
        wait_until(lambda: self.loop.events_handled >= self.loop.events_published,
                   what="the worker to catch up")

    def actions(self) -> list[str]:
        return [command.action for command in self.bridge.drain()]

    def close(self) -> None:
        self.loop.shutdown()
        self.brain.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig()
    yield made
    made.close()


# --- the gate on its own ---------------------------------------------------------------------
def test_gate_accepts_everything_while_hexa_is_silent() -> None:
    gate = SelfHearingGate(threading.Event())
    for _ in range(5):
        decision = gate.check()
        assert decision.accept and not decision.reset


def test_gate_blocks_while_speaking_and_the_edge_block_and_resets_once_when_it_reopens() -> None:
    speaking = threading.Event()
    gate = SelfHearingGate(speaking)
    assert gate.check().accept
    speaking.set()
    assert not gate.check().accept and not gate.check().accept  # hexa is talking
    speaking.clear()  # tail over
    assert not gate.check().accept  # the block that spanned the change: still hexa's voice
    reopened = gate.check()
    assert reopened.accept and reopened.reset  # first clean block, recognizer reset first
    again = gate.check()
    assert again.accept and not again.reset


def test_gate_survives_rapid_speak_clear_cycles() -> None:
    speaking = threading.Event()
    gate = SelfHearingGate(speaking)
    accepted = []
    for cycle in range(40):
        speaking.set() if cycle % 2 == 0 else speaking.clear()  # flips on every block
        accepted.append(gate.check().accept)
    assert not any(accepted)  # never a clean block in the middle of a chatter
    speaking.clear()
    gate.check()
    assert gate.check().accept


# --- the voice loop: self-hearing ----------------------------------------------------------------
def test_audio_while_speaking_never_reaches_the_recognizer_or_the_robot(rig: Rig) -> None:
    rig.speaking.set()
    for _ in range(3):
        rig.push(WALK)  # a "walk forward" that hexa itself said
    rig.settle(3)
    assert rig.stt.fed == [] and rig.loop.blocks_discarded == 3
    assert rig.actions() == []  # the robot did not move


def test_the_same_audio_after_the_tail_is_accepted_and_moves_the_robot(rig: Rig) -> None:
    rig.speaking.set()
    rig.push(WALK)
    rig.push(WALK)
    rig.settle(2)
    assert rig.actions() == []
    rig.speaking.clear()
    rig.push(0)  # the edge block (silence) is dropped too
    rig.push(WALK)
    rig.settle(4)
    assert rig.actions() == ["walk"]
    assert rig.stt.resets == 1  # reset when the gate reopened, before the first clean block


def test_a_stale_partial_does_not_leak_through_the_gap() -> None:
    stt = FakeStt({WALK: [("partial", "walk")], SIT: [("final", "sit down")]})
    rig = Rig(stt)
    try:
        rig.push(WALK)  # a half-heard utterance
        rig.settle(1)
        rig.speaking.set()
        rig.push(0)
        rig.settle(2)  # read while hexa speaks
        rig.speaking.clear()
        rig.push(0)
        rig.push(0)
        rig.settle(4)
        assert stt.resets == 1 and rig.actions() == []  # the partial never became a command
    finally:
        rig.close()


def test_self_hearing_through_the_real_playback_with_a_fake_clock(tmp_path: Path) -> None:
    clock = FakeClock()
    write_wav(tmp_path / "okay.wav", AudioClip(np.full(1000, 7, dtype=np.int16), 1000))  # 1 s
    playback = Playback(FakeEngine(clock), FakeSink(clock), clock=clock, tail_s=0.4,
                        phrases_dir=tmp_path)
    playback.start()
    rig = Rig(playback=playback)
    rig.loop.shutdown()  # use our own speaking Event: the real one from the playback
    rig.loop = VoiceLoop(rig.source, rig.stt, rig.brain, playback.speaking, playback,
                         rig.events.append)
    rig.loop.start()
    try:
        playback.say_phrase("okay")
        wait_until(playback.speaking.is_set, what="speech to start")
        rig.push(WALK)
        rig.settle(1)
        assert rig.actions() == [] and rig.loop.blocks_discarded == 1  # during the sound
        run_fake_time(clock, lambda: playback.pending == 0)  # the sound ends ...
        rig.push(WALK)
        rig.settle(2)
        assert rig.actions() == [] and playback.speaking.is_set()  # ... inside the tail
        clock.advance(0.5)
        wait_until(lambda: not playback.speaking.is_set(), what="the tail to end")
        rig.push(0)
        rig.push(WALK)
        rig.settle(4)
        assert rig.actions() == ["walk"]
    finally:
        rig.close()
        playback.shutdown()


# --- the voice loop: routing -------------------------------------------------------------------
def test_final_results_are_routed_to_the_bridge(rig: Rig) -> None:
    rig.push(SIT)
    rig.push(WALK)
    rig.settle(2)
    assert rig.actions() == ["sit", "walk"]
    routes = [e for e in rig.events if e.kind == "route"]
    assert [r.route.action for r in routes if r.route] == ["sit", "walk"]


def test_a_chat_sentence_sends_nothing(rig: Rig) -> None:
    rig.push(CHAT)
    rig.settle(1)
    assert rig.actions() == []
    route = next(e for e in rig.events if e.kind == "route")
    assert route.route is not None and route.route.kind == "chat"


def test_a_stop_word_in_a_partial_stops_at_once_and_the_final_does_not_repeat_it(rig: Rig) -> None:
    rig.push(PARTIAL_STOP)
    rig.settle(1)
    assert rig.actions() == ["stop"]  # before any final result
    assert any(e.kind == "route" and e.early_stop for e in rig.events)
    rig.push(PARTIAL_STOP)  # the next partial of the same utterance
    rig.push(FINAL_STOP)
    rig.settle(3)
    assert rig.actions() == []  # no second stop
    rig.push(FINAL_STOP)  # the next utterance is a fresh stop
    rig.settle(4)
    assert rig.actions() == ["stop"]


def test_a_command_in_a_partial_result_does_nothing() -> None:
    rig = Rig(FakeStt({WALK: [("partial", "walk forward")]}))
    try:
        rig.push(WALK)
        rig.settle(1)
        assert rig.actions() == []  # only FINAL results route commands
    finally:
        rig.close()


def test_a_stop_after_a_gap_is_not_swallowed_by_an_earlier_early_stop() -> None:
    """Safety: an early stop must not make the next utterance's stop get ignored."""
    stt = FakeStt({PARTIAL_STOP: [("partial", "stop")]})
    rig = Rig(stt)
    try:
        rig.push(PARTIAL_STOP)
        rig.settle(1)
        assert rig.actions() == ["stop"]
        rig.speaking.set()  # hexa says "okay": the half-heard utterance is discarded
        rig.push(0)
        rig.settle(2)
        rig.speaking.clear()
        rig.push(0)
        rig.push(0)  # the gate reopens and resets
        rig.push(PARTIAL_STOP)
        rig.settle(5)
        assert rig.actions() == ["stop"]
    finally:
        rig.close()


class StubPlayback:
    def __init__(self) -> None:
        self.phrases: list[str] = []

    def say_phrase(self, name: str) -> int:
        self.phrases.append(name)
        return 1


def test_the_voice_loop_itself_says_nothing_about_a_command() -> None:
    """Speech after a command comes from the body's STATUS (brain/dialogue.py), never from the
    assumption that the command worked."""
    playback = StubPlayback()
    rig = Rig(playback=playback)
    try:
        rig.push(SIT)
        rig.push(CHAT)
        rig.push(WALK)
        rig.settle(3)
        assert rig.actions() == ["sit", "walk"]
        assert playback.phrases == []
    finally:
        rig.close()


# --- errors and shutdown -----------------------------------------------------------------------
def test_a_recognizer_error_is_logged_and_the_loop_keeps_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig(FakeStt(SCRIPT, fail_on_calls=(2,)))
    try:
        with caplog.at_level(logging.ERROR):
            rig.push(SIT)
            rig.push(WALK)  # this block makes the recognizer raise
            rig.push(SIT)
            rig.settle(3)
        assert rig.loop.errors == 1
        assert "speech recognition error" in caplog.text
        assert rig.actions() == ["sit", "sit"]  # the loop recovered and carried on
        assert rig.stt.resets == 1
    finally:
        rig.close()


class FlakySource:
    """Fails on the first read and on start, then delivers one block."""

    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.reads = 0
        self.stopped = False

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("no microphone")

    def stop(self) -> None:
        self.stopped = True

    def read(self, timeout: float) -> Block | None:
        self.reads += 1
        if self.reads == 1:
            raise OSError("device hiccup")
        if self.reads == 2:
            return marker_block(SIT)
        time.sleep(min(timeout, 0.01))
        return None


def test_a_source_error_is_logged_and_the_loop_keeps_running(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "STT_ERROR_BACKOFF_S", 0.01)
    source = FlakySource()
    with caplog.at_level(logging.ERROR):
        rig = Rig(source=source)
        try:
            wait_until(lambda: rig.loop.blocks_seen >= 1, what="a block after the error")
            rig.settle(1)
            assert rig.loop.errors == 1 and "audio source error" in caplog.text
            assert rig.actions() == ["sit"]
        finally:
            rig.close()
    assert source.stopped


def test_a_source_that_cannot_start_is_reported_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        rig = Rig(source=FlakySource(fail_start=True))
        try:
            assert rig.loop.stt_finished.wait(2.0)
            assert rig.loop.errors == 1 and "could not start the audio source" in caplog.text
        finally:
            rig.close()


def test_a_finite_source_finishes_and_the_loop_goes_idle() -> None:
    source = QueueSource()
    rig = Rig(source=source)
    try:
        rig.push(SIT)
        source.close()
        assert rig.loop.wait_idle(3.0)
        assert rig.actions() == ["sit"]
    finally:
        rig.close()


def voice_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name in ("voice-stt", "voice-worker",
                                                          "status-hub")]


def test_shutdown_leaves_no_threads(rig: Rig) -> None:
    rig.push(SIT)
    rig.settle(1)
    assert len(voice_threads()) == 3  # stt, worker, hub
    started = time.perf_counter()
    rig.close()
    assert time.perf_counter() - started < 2.0
    assert voice_threads() == []
    rig.close()  # twice is fine


def test_end_of_audio_is_an_exception_type() -> None:
    assert issubclass(EndOfAudio, Exception)
