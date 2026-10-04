"""Step 10: the instant filler. A slow reply gets ONE pre-rendered filler; a fast one gets none."""

from __future__ import annotations

import threading
import time

import config
from brain.brain_loop import BrainLoop
from brain.chat import ChatResponder, FakeChat
from brain.voice_loop import VoiceLoop
from bridge import make_local_bridge
from tests.fakes import FakeStt, StubPlayback, marker_block, wait_until
from voice.audio import QueueSource

DELAY = 0.15


def responder(backend: FakeChat, playback: StubPlayback, **kwargs: object) -> ChatResponder:
    return ChatResponder(backend, playback, filler_delay_s=DELAY, **kwargs)  # type: ignore[arg-type]


def test_a_slow_first_sentence_gets_exactly_one_filler() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("Here is my answer. And more.",), first_token_s=0.5), playback)
    chat.submit("hello")
    wait_until(lambda: not chat.active, what="the reply")
    time.sleep(0.4)  # a second timer firing would show up here
    assert playback.phrases == [config.FILLER_PHRASES[0]]
    assert chat.fillers_played == 1
    assert playback.sentences  # the real answer still follows the filler
    chat.shutdown()


def test_a_fast_reply_gets_no_filler() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("Here is my answer. And more.",)), playback)
    chat.submit("hello")
    wait_until(lambda: not chat.active, what="the reply")
    time.sleep(DELAY + 0.2)
    assert playback.phrases == [] and chat.fillers_played == 0
    chat.shutdown()


def test_a_long_reply_gets_one_filler_not_one_per_sentence() -> None:
    playback = StubPlayback()
    reply = " ".join(f"Sentence number {n}." for n in range(6))
    chat = responder(FakeChat((reply,), first_token_s=0.4, tokens_per_s=20.0), playback)
    chat.submit("hello")
    wait_until(lambda: not chat.active, timeout=8.0, what="the reply")
    assert len(playback.phrases) == 1
    chat.shutdown()


def test_the_filler_is_cancelled_by_barge_in() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("A late answer.",), first_token_s=1.0), playback)
    chat.submit("hello")
    time.sleep(DELAY / 3)
    chat.cancel()  # the user pressed the button before the filler was due
    time.sleep(DELAY * 2)
    assert playback.phrases == [] and chat.fillers_played == 0
    chat.shutdown()


def test_a_filler_already_playing_is_cleared_by_barge_in() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("A late answer.",), first_token_s=1.0), playback)
    chat.submit("hello")
    wait_until(lambda: bool(playback.phrases), what="the filler")
    chat.cancel()
    assert playback.clears  # the filler and the reply are silenced together
    time.sleep(0.2)
    assert playback.sentences == []
    chat.shutdown()


def test_the_fillers_rotate() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("Answer one.", "Answer two."), first_token_s=0.4), playback)
    for _ in range(2):
        chat.submit("hello")
        wait_until(lambda: not chat.active, what="the reply")
    assert playback.phrases == list(config.FILLER_PHRASES[:2])
    chat.shutdown()


def test_a_failed_reply_does_not_get_a_filler_after_the_apology() -> None:
    playback = StubPlayback()
    chat = responder(FakeChat(("x",), fail_after=0), playback)
    chat.submit("hello")
    wait_until(lambda: not chat.active, what="the failure")
    time.sleep(DELAY + 0.2)
    assert playback.phrases == ["cant_think"]
    chat.shutdown()


def test_no_filler_while_the_body_is_moving() -> None:
    """Chat is not called while the body moves, so there is nothing to fill."""
    bridge = make_local_bridge()
    brain = BrainLoop(bridge)
    playback = StubPlayback()
    backend = FakeChat(("Hi there.",), first_token_s=0.5)
    chat = responder(backend, playback)
    source = QueueSource()
    script = {1: [("final", "walk forward")], 2: [("final", "tell me a joke")]}
    loop = VoiceLoop(source, FakeStt(script), brain, threading.Event(),
                     playback, chat=chat)  # type: ignore[arg-type]
    loop.start()
    try:
        for marker in (1, 2):
            source.push(marker_block(marker))
        wait_until(lambda: loop.events_handled >= 2, what="both utterances")
        time.sleep(DELAY + 0.3)
        assert backend.calls == 0  # never asked the LLM ...
        assert config.FILLER_PHRASES[0] not in playback.phrases  # ... so no filler
        assert playback.phrases == ["tell_me_after_stop"]
    finally:
        loop.shutdown()
        chat.shutdown()
        brain.close()
