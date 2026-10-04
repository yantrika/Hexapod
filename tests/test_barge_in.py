"""Step 10: push-to-talk in the voice loop and barge-in. No microphone, no real LLM."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

import config
from brain.brain_loop import BrainLoop
from brain.chat import ChatResponder, FakeChat
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import make_local_bridge
from tests.fakes import (
    FakeClock,
    FakeEngine,
    FakeSink,
    FakeStt,
    StubPlayback,
    marker_block,
    run_fake_time,
    wait_until,
)
from voice.audio import QueueSource
from voice.playback import Playback
from voice.ptt import PushToTalk

WALK, SILENCE = 1, 7
SCRIPT = {WALK: [("final", "walk forward")]}
SLOW_REPLY = " ".join(f"Sentence number {n}." for n in range(60))


class Rig:
    def __init__(self, chat_backend: FakeChat | None = None, ptt: bool = True,
                 playback: object | None = None, speaking: threading.Event | None = None,
                 flush_events: list[tuple[str, str]] | None = None) -> None:
        self.clock = FakeClock()
        self.bridge = make_local_bridge()
        self.brain = BrainLoop(self.bridge)
        self.playback = playback if playback is not None else StubPlayback()
        self.backend = chat_backend
        self.chat = (ChatResponder(chat_backend, self.playback, filler_delay_s=0.0)  # type: ignore[arg-type]
                     if chat_backend else None)
        self.ptt = PushToTalk(self.clock.now, tail_s=0.5) if ptt else None
        self.stt = FakeStt(SCRIPT, flush_events=flush_events)
        self.source = QueueSource()
        self.events: list[VoiceEvent] = []
        self.loop = VoiceLoop(self.source, self.stt, self.brain, speaking or threading.Event(),
                              self.playback, self.events.append,  # type: ignore[arg-type]
                              chat=self.chat, ptt=self.ptt)
        self.loop.start()
        self.pushed = 0

    def push(self, marker: int, count: int = 1) -> None:
        for _ in range(count):
            self.source.push(marker_block(marker))
            self.pushed += 1
        wait_until(lambda: self.loop.blocks_seen >= self.pushed, what="blocks to be read")

    def close(self) -> None:
        self.loop.shutdown()
        if self.chat:
            self.chat.shutdown()
        self.brain.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig()
    yield made
    made.close()


# --- the recognizers only get audio while PTT listens -----------------------------------------
def test_zero_blocks_reach_the_recognizer_while_ptt_is_off(rig: Rig) -> None:
    rig.push(WALK, 6)  # even speech: nobody pressed the button
    assert rig.stt.calls == 0 and rig.stt.fed == []
    assert rig.loop.blocks_idle == 6 and rig.loop.blocks_seen == 6


def test_all_blocks_reach_the_recognizer_while_ptt_is_on(rig: Rig) -> None:
    assert rig.ptt is not None
    rig.ptt.press()
    rig.push(SILENCE, 5)
    assert rig.stt.fed == [SILENCE] * 5 and rig.loop.blocks_idle == 0


def test_the_tail_is_still_fed_then_the_utterance_is_flushed_and_nothing_more(rig: Rig) -> None:
    assert rig.ptt is not None
    rig.ptt.press()
    rig.push(SILENCE, 2)
    rig.ptt.release()
    rig.clock.advance(0.3)
    rig.push(SILENCE)  # inside the tail
    assert len(rig.stt.fed) == 3 and rig.stt.flushes == 0
    rig.clock.advance(0.3)
    rig.push(SILENCE)  # the tail is over: flush, and this block is dropped
    assert rig.stt.flushes == 1 and len(rig.stt.fed) == 3
    rig.push(SILENCE, 3)
    assert len(rig.stt.fed) == 3 and rig.loop.blocks_idle == 4


def test_the_recognizer_is_reset_when_listening_starts(rig: Rig) -> None:
    assert rig.ptt is not None
    rig.push(SILENCE)
    resets = rig.stt.resets
    rig.ptt.press()
    rig.push(SILENCE)
    assert rig.stt.resets > resets  # a half-heard partial from before cannot leak in


def test_the_final_from_the_release_flush_becomes_a_command() -> None:
    made = Rig(flush_events=[("final", "walk forward")])
    try:
        assert made.ptt is not None
        made.ptt.press()
        made.push(SILENCE, 2)
        made.ptt.release()
        made.clock.advance(1.0)
        made.push(SILENCE)
        wait_until(lambda: any(e.kind == "route" for e in made.events), what="the route")
        assert [c.action for c in made.bridge.drain()] == ["walk"]
    finally:
        made.close()


# --- barge-in --------------------------------------------------------------------------------
def test_pressing_while_chat_speaks_cancels_it_clears_the_speech_and_listens() -> None:
    backend = FakeChat((SLOW_REPLY,), tokens_per_s=40.0)
    made = Rig(backend)
    try:
        assert made.chat is not None and made.ptt is not None
        made.chat.submit("tell me a story")
        wait_until(lambda: len(made.playback.sentences) >= 2, what="the reply to be speaking")  # type: ignore[attr-defined]
        started = time.monotonic()
        made.ptt.press()
        took = time.monotonic() - started
        assert made.playback.clears and made.playback.skip_tail_clears >= 1  # type: ignore[attr-defined]
        assert took < max(config.TTS_CLEAR_MAX_S, 0.5)
        assert not made.chat.active  # the stream was cancelled
        assert made.chat.history == []  # the cancelled reply is not remembered
        made.push(SILENCE, 2)  # listening started at once
        assert made.stt.fed == [SILENCE, SILENCE]
        assert made.loop.barge_ins == 1
        spoken = len(made.playback.said)  # type: ignore[attr-defined]
        time.sleep(0.2)
        # nothing more of the old reply
        assert len(made.playback.said) == spoken  # type: ignore[attr-defined]
    finally:
        made.close()


def test_a_finished_reply_stays_in_history_when_the_user_presses_later() -> None:
    made = Rig(FakeChat(("Short answer.",)))
    try:
        assert made.chat is not None and made.ptt is not None
        chat = made.chat
        chat.submit("hello")
        wait_until(lambda: not chat.active, what="the reply")
        history = made.chat.history
        made.ptt.press()
        assert made.chat.history == history
    finally:
        made.close()


def test_pressing_while_hexa_speaks_opens_the_microphone_at_once() -> None:
    """Real Playback (fake engine and sink): after the press the speaking flag is already clear,
    so the first block is fed, not discarded by the self-hearing gate or its tail."""
    clock = FakeClock()
    speaking = threading.Event()
    playback = Playback(FakeEngine(clock), FakeSink(clock), speaking=speaking, clock=clock,
                        tail_s=config.SPEAK_TAIL_S)
    playback.start()
    made = Rig(playback=playback, speaking=speaking)
    try:
        assert made.ptt is not None
        playback.say("A long sentence that keeps going.")
        run_fake_time(clock, lambda: speaking.is_set(), limit_s=5.0)
        made.ptt.press()
        assert not speaking.is_set()  # no tail after a barge-in
        made.push(SILENCE, 2)
        assert made.stt.fed == [SILENCE, SILENCE] and made.loop.blocks_discarded == 0
    finally:
        made.close()
        playback.shutdown()


def test_without_barge_in_the_tail_would_gate_the_first_blocks() -> None:
    """The contrast: a plain clear() keeps the speaking tail, so the gate would discard."""
    clock = FakeClock()
    speaking = threading.Event()
    playback = Playback(FakeEngine(clock), FakeSink(clock), speaking=speaking, clock=clock,
                        tail_s=config.SPEAK_TAIL_S)
    playback.start()
    try:
        playback.say("A long sentence that keeps going.")
        run_fake_time(clock, lambda: speaking.is_set(), limit_s=5.0)
        playback.clear()
        wait_until(lambda: playback.pending == 0, what="the clear")
        time.sleep(0.05)
        assert speaking.is_set()
        clock.advance(config.SPEAK_TAIL_S + 0.1)
        wait_until(lambda: not speaking.is_set(), what="the tail to end")
    finally:
        playback.shutdown()


def test_a_stop_word_while_listening_still_stops_at_once() -> None:
    made = Rig()
    try:
        assert made.ptt is not None
        made.stt.script[WALK] = [("partial", "stop")]
        made.ptt.press()
        made.push(WALK)
        wait_until(lambda: any(e.early_stop for e in made.events), what="the early stop")
        assert [c.action for c in made.bridge.drain()][0] == "stop"
    finally:
        made.close()


# --- "always" mode: the Step 8 behaviour is unchanged --------------------------------------------
def test_always_mode_feeds_every_block_and_still_gates_hexas_voice() -> None:
    speaking = threading.Event()
    made = Rig(ptt=False, speaking=speaking)
    try:
        made.push(SILENCE, 3)
        assert made.stt.fed == [SILENCE] * 3 and made.loop.blocks_idle == 0
        speaking.set()
        made.push(SILENCE, 2)
        assert len(made.stt.fed) == 3 and made.loop.blocks_discarded == 2  # deaf while speaking
    finally:
        made.close()


def test_no_barge_in_hook_without_ptt() -> None:
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=40.0), ptt=False)
    try:
        assert made.ptt is None and made.loop.barge_ins == 0
    finally:
        made.close()


# --- shutdown --------------------------------------------------------------------------------
def test_clean_shutdown_leaves_no_threads() -> None:
    before = {t.name for t in threading.enumerate()}
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=40.0))
    assert made.chat is not None and made.ptt is not None
    made.chat.submit("story")
    wait_until(lambda: len(made.playback.sentences) >= 1, what="speech")  # type: ignore[attr-defined]
    made.ptt.press()
    made.close()
    wait_until(lambda: {t.name for t in threading.enumerate()} <= before, timeout=3.0,
               what="all threads to end")
