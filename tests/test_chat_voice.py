"""Step 9: chat in the voice loop. The safety rule: chat reaches the LLM only while the body is
idle; any motion or stop silences it. FakeChat, a fake recognizer and an in-process bridge."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

import config
from brain.brain_loop import BrainLoop
from brain.chat import ChatResponder, FakeChat
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import Status, make_local_bridge
from tests.fakes import FakeClock, FakeStt, StubPlayback, marker_block, wait_until
from voice.audio import QueueSource

WALK, SIT, CHAT, STOP, CHAT2 = 1, 2, 3, 4, 5
SCRIPT = {
    WALK: [("final", "walk forward")], SIT: [("final", "sit down")],
    CHAT: [("final", "tell me a joke")], STOP: [("final", "stop")],
    CHAT2: [("final", "what is your name")],
}
SLOW_REPLY = " ".join(f"Sentence number {n}." for n in range(60))


class Rig:
    def __init__(self, backend: FakeChat, clock: FakeClock | None = None) -> None:
        self.bridge = make_local_bridge()
        self.clock = clock
        self.brain = BrainLoop(self.bridge, clock=clock.now if clock else time.monotonic)
        self.playback = StubPlayback()
        self.backend = backend
        self.chat = ChatResponder(backend, self.playback)
        self.source = QueueSource()
        self.events: list[VoiceEvent] = []
        self.loop = VoiceLoop(self.source, FakeStt(SCRIPT), self.brain, threading.Event(),
                              self.playback, self.events.append,  # type: ignore[arg-type]
                              clock=clock.now if clock else time.monotonic, chat=self.chat)
        self.loop.start()
        self.blocks = 0

    def say(self, marker: int) -> None:
        self.source.push(marker_block(marker))
        self.blocks += 1
        wait_until(lambda: self.loop.blocks_seen >= self.blocks, what="the block to be read")
        wait_until(lambda: self.loop.events_handled >= self.loop.events_published,
                   what="the worker to catch up")

    def body_answers(self, kind: str, command_index: int = 0) -> None:
        """Play the body: answer the command that was sent."""
        commands = self.bridge.drain()
        self.sent_commands = commands
        self.bridge.report(Status(kind, commands[command_index].seq, {}, 1, time.time()))

    def pump_until(self, predicate: object, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.brain.pump()
            if predicate():  # type: ignore[operator]
                return
            time.sleep(0.01)
        raise AssertionError("timed out")

    def close(self) -> None:
        self.loop.shutdown()
        self.chat.shutdown()
        self.brain.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig(FakeChat(("Why did the robot cross the road? To walk the other side.",)))
    yield made
    made.close()


def test_chat_while_idle_calls_the_backend_and_speaks(rig: Rig) -> None:
    rig.say(CHAT)
    wait_until(lambda: rig.backend.calls == 1 and not rig.chat.active, what="the reply")
    assert rig.playback.sentences[0].startswith("Why did the robot")
    assert rig.backend.last_messages[-1] == {"role": "user", "content": "tell me a joke"}
    assert rig.playback.phrases == []


def test_chat_while_walking_does_not_call_the_llm_and_says_tell_me_after_i_stop(rig: Rig) -> None:
    rig.say(WALK)
    assert rig.brain.keeper.held_seq is not None and not rig.brain.body_idle()
    rig.say(CHAT)
    assert rig.backend.calls == 0  # the LLM was never called
    assert rig.playback.phrases == ["tell_me_after_stop"] and rig.playback.sentences == []


def test_the_busy_notice_is_not_repeated_every_time() -> None:
    clock = FakeClock()
    made = Rig(FakeChat(("Hello there.",)), clock=clock)
    try:
        made.say(WALK)
        made.say(CHAT)
        made.say(CHAT2)  # a moment later: throttled
        assert made.playback.phrases == ["tell_me_after_stop"]
        clock.advance(config.DIALOGUE_THROTTLE_S + 0.1)
        made.say(CHAT)
        assert made.playback.phrases == ["tell_me_after_stop", "tell_me_after_stop"]
        assert made.backend.calls == 0
    finally:
        made.close()


def test_chat_works_again_after_the_walk_ends(rig: Rig) -> None:
    rig.say(WALK)
    rig.body_answers("accepted")
    rig.pump_until(lambda: rig.brain.keeper.active)
    rig.say(STOP)  # the stop releases the keeper
    assert rig.brain.body_idle()
    rig.say(CHAT)
    wait_until(lambda: rig.backend.calls == 1, what="the LLM to be called")


def test_a_sit_transition_keeps_the_body_busy_until_it_is_done() -> None:
    clock = FakeClock()
    made = Rig(FakeChat(("Hello there.",)), clock=clock)
    try:
        made.say(SIT)
        assert not made.brain.body_idle()  # the transition is running
        made.say(CHAT)
        assert made.backend.calls == 0
        made.body_answers("done")  # the body says it finished
        made.pump_until(made.brain.body_idle)
        clock.advance(config.DIALOGUE_THROTTLE_S + 0.1)
        made.say(CHAT)
        wait_until(lambda: made.backend.calls == 1, what="chat after the sit")
    finally:
        made.close()


def test_a_transition_ends_by_itself_after_its_duration() -> None:
    clock = FakeClock()
    made = Rig(FakeChat(("Hi.",)), clock=clock)
    try:
        made.say(SIT)
        assert not made.brain.body_idle()
        clock.advance(config.SIT_STAND_TRANSITION_S + config.SETTLE_S + 0.1)
        assert made.brain.body_idle()
    finally:
        made.close()


def test_a_motion_command_cancels_chat_speech_in_progress() -> None:
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=100.0))
    try:
        made.say(CHAT)
        wait_until(lambda: len(made.playback.said) >= 2, 5.0, "chat speech")
        started = time.perf_counter()
        made.say(WALK)  # motion starts: the chat reply must stop
        assert time.perf_counter() - started < 2.0
        assert made.playback.clears and not made.chat.active
        count = len(made.playback.said)
        time.sleep(0.15)
        assert len(made.playback.said) == count  # nothing more is spoken
        assert made.backend.cancelled_calls >= 1 and made.chat.history == []
        assert [c.action for c in made.bridge.drain()] == ["walk"]
    finally:
        made.close()


def test_a_stop_cancels_chat_speech_too() -> None:
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=100.0))
    try:
        made.say(CHAT)
        wait_until(lambda: len(made.playback.said) >= 2, 5.0, "chat speech")
        made.say(STOP)
        assert made.playback.clears and not made.chat.active
        assert [c.action for c in made.bridge.drain()] == ["stop"]
    finally:
        made.close()


def test_motion_from_another_source_also_silences_chat() -> None:
    """The pump cancels chat as soon as the body is no longer idle, whoever started the motion."""
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=100.0))
    try:
        made.say(CHAT)
        wait_until(lambda: len(made.playback.said) >= 2, 5.0, "chat speech")
        made.brain.handle_text("walk forward")  # a walk started outside the voice loop
        wait_until(lambda: not made.chat.active, 3.0, "the chat to be cancelled")
        assert made.playback.clears
    finally:
        made.close()


def test_chat_text_is_not_a_command_and_commands_never_reach_the_llm(rig: Rig) -> None:
    """The LLM is not in the command path: only the router decides, and only chat goes to chat."""
    rig.say(WALK)
    rig.say(SIT)
    assert rig.backend.calls == 0  # commands never touch the LLM
    assert [c.action for c in rig.bridge.drain()] == ["walk", "sit"]


def test_no_threads_left_after_shutdown() -> None:
    made = Rig(FakeChat((SLOW_REPLY,), tokens_per_s=100.0))
    made.say(CHAT)
    wait_until(lambda: bool(made.playback.said), 5.0, "speech")
    made.close()
    left = [t.name for t in threading.enumerate()
            if t.name in ("chat-reply", "voice-stt", "voice-worker", "status-hub")]
    assert left == []
