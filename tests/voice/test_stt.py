"""VoskStt runs ONE model with TWO recognizers (both fed, reset and flushed together; fake Vosk
classes) and the self-hearing gate with a fake recognizer (no microphone, no speaker, no Vosk)."""

from __future__ import annotations

import json
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
from voice.stt import SelfHearingGate, VoskStt, command_grammar
from voice.tts import AudioClip, write_wav


class FakeKaldi:
    """Stands in for ``vosk.KaldiRecognizer``; all instances are collected in ``made``."""

    made: list[FakeKaldi] = []

    def __init__(self, model: object, rate: int, grammar: str | None = None) -> None:
        self.grammar = json.loads(grammar) if grammar else None
        self.words_on = False
        self.resets = 0
        self.final_calls = 0
        self.accepted: list[bytes] = []
        self.script: dict[int, tuple[bool, dict[str, Any]]] = {}  # marker -> (final?, result)
        self.partial = ""
        self._pending: dict[str, Any] = {}
        FakeKaldi.made.append(self)

    def SetWords(self, on: bool) -> None:  # noqa: N802 - Vosk's API
        self.words_on = on

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802
        self.accepted.append(data)
        marker = int(np.frombuffer(data, dtype=np.int16)[0])
        final, result = self.script.get(marker, (False, {}))
        if final:
            self._pending = result
        return final

    def Result(self) -> str:  # noqa: N802
        return json.dumps(self._pending)

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps({"partial": self.partial})

    def FinalResult(self) -> str:  # noqa: N802
        self.final_calls += 1
        return json.dumps(self._pending or {"text": ""})

    def Reset(self) -> None:  # noqa: N802
        self.resets += 1


class FakeModel:
    def __init__(self, path: str) -> None:
        self.path = path


@pytest.fixture
def fake_vosk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    import vosk

    FakeKaldi.made = []
    monkeypatch.setattr(vosk, "Model", FakeModel)
    monkeypatch.setattr(vosk, "KaldiRecognizer", FakeKaldi)
    monkeypatch.setattr(vosk, "SetLogLevel", lambda level: None)
    yield tmp_path


def words(*pairs: tuple[str, float]) -> dict[str, Any]:
    return {"text": " ".join(w for w, _ in pairs),
            "result": [{"word": w, "conf": c, "start": 0, "end": 1} for w, c in pairs]}


def test_two_recognizers_share_one_model_and_the_grammar_comes_from_config(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    assert free.grammar is None and grammar.grammar == command_grammar()
    assert free.words_on and grammar.words_on  # confidences on both
    assert stt._model is not None and len(FakeKaldi.made) == 2


def test_the_grammar_recognizer_can_be_turned_off(fake_vosk: Path) -> None:
    VoskStt(fake_vosk, grammar=False)
    assert len(FakeKaldi.made) == 1
    assert config.STT_USE_GRAMMAR is True  # on by default


def test_both_recognizers_are_fed_the_same_audio(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    stt.feed(marker_block(1, 50))
    free, grammar = FakeKaldi.made
    assert free.accepted == grammar.accepted and len(free.accepted) == 1


def test_a_final_carries_both_hypotheses_with_confidences(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    free.script[2] = (True, words(("said", 0.4)))
    grammar._pending = words(("[unk]", 0.8), ("sit", 0.8))  # what FinalResult will flush
    (event,) = stt.feed(marker_block(2))
    assert event.kind == "final" and event.text == "said"
    assert event.free is not None and event.free.mean_conf == pytest.approx(0.4)
    assert event.grammar is not None and event.grammar.text == "[unk] sit"
    assert event.grammar.mean_conf == pytest.approx(0.8)  # [unk] is not counted
    assert grammar.final_calls == 1  # flushed at the free recognizer's endpoint


def test_a_grammar_endpoint_that_came_first_is_kept_and_merged(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    grammar.script[3] = (True, words(("sit", 0.9)))  # the grammar ends its segment first
    assert stt.feed(marker_block(3)) == []  # the free recognizer has no result yet
    free.script[4] = (True, words(("sit", 1.0), ("down", 1.0)))
    grammar._pending = words(("down", 0.7))
    (event,) = stt.feed(marker_block(4))
    assert event.grammar is not None and event.grammar.text == "sit down"
    assert [w for w, _ in event.grammar.words] == ["sit", "down"]


def test_partials_come_from_the_free_recognizer_and_only_when_they_change(
    fake_vosk: Path,
) -> None:
    stt = VoskStt(fake_vosk)
    free, _ = FakeKaldi.made
    free.partial = "wal"
    assert [e.text for e in stt.feed(marker_block(0))] == ["wal"]
    assert stt.feed(marker_block(0)) == []  # unchanged
    free.partial = "walk"
    assert [e.text for e in stt.feed(marker_block(0))] == ["walk"]


def test_reset_clears_both_recognizers_and_the_pending_segments(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    grammar.script[3] = (True, words(("sit", 0.9)))
    stt.feed(marker_block(3))
    stt.reset()
    assert (free.resets, grammar.resets) == (1, 1)
    free.script[4] = (True, words(("x", 1.0)))
    grammar._pending = {}
    (event,) = stt.feed(marker_block(4))
    assert event.grammar is not None and event.grammar.text == ""  # the old segment is gone


def test_flush_returns_a_final_from_both(fake_vosk: Path) -> None:
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    free._pending = words(("walk", 1.0))
    grammar._pending = words(("walk", 1.0))
    (event,) = stt.flush()
    assert event.text == "walk" and event.grammar is not None and event.grammar.text == "walk"


def test_the_voice_loop_resets_both_recognizers_after_hexa_speaks(fake_vosk: Path) -> None:
    """Self-hearing: audio during speech is never fed, and when it ends BOTH are reset."""
    stt = VoskStt(fake_vosk)
    free, grammar = FakeKaldi.made
    bridge = make_local_bridge()
    brain = BrainLoop(bridge)
    speaking = threading.Event()
    source = QueueSource()
    loop = VoiceLoop(source, stt, brain, speaking)
    loop.start()
    try:
        source.push(marker_block(0))
        wait_until(lambda: loop.blocks_seen == 1)
        speaking.set()
        for _ in range(2):
            source.push(marker_block(1))
        wait_until(lambda: loop.blocks_seen == 3)
        assert len(free.accepted) == 1 and len(grammar.accepted) == 1  # nothing fed while speaking
        speaking.clear()
        for _ in range(3):
            source.push(marker_block(0))
        wait_until(lambda: loop.blocks_seen == 6)
        assert (free.resets, grammar.resets) == (1, 1)  # both reset when the gate reopened
        assert len(free.accepted) == len(grammar.accepted) == 3  # edge block was dropped
    finally:
        loop.shutdown()
        brain.close()


# --- stt gate ------------------------------------------------------------

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
