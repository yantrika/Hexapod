"""Step 13 (P1): the voice loop with persona hooks: persona routes never reach the body or the
LLM, a yes answers "say yes to confirm", commands and stops are untouched in every mode."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from brain.arya import Arya
from brain.brain_loop import BrainLoop
from brain.chat import ChatResponder, FakeChat
from brain.context import ContextBuilder
from brain.memory import Memory
from brain.persona import Persona
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import make_local_bridge
from tests.fakes import FakeStt, StubPlayback, wait_until
from voice.audio import QueueSource


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.bridge = make_local_bridge()
        self.brain = BrainLoop(self.bridge)
        self.playback = StubPlayback()
        self.memory = Memory(tmp_path / "arya.db")
        self.arya = Arya(Persona.from_file(), self.memory, self.playback)
        self.backend = FakeChat(("I am fine, thanks.",))
        self.chat = ChatResponder(
            self.backend, self.playback,
            context_builder=ContextBuilder(self.arya.context_parts).build,
            sentence_check=self.arya.sentence_check)
        self.events: list[VoiceEvent] = []
        self.loop = VoiceLoop(QueueSource(), FakeStt({}), self.brain, threading.Event(),
                              self.playback, self.events.append,  # type: ignore[arg-type]
                              chat=self.chat, persona=self.arya)
        self.loop.start()

    def type(self, text: str) -> None:
        self.loop.submit_text(text)
        wait_until(lambda: self.loop.events_handled >= self.loop.events_published,
                   what="the worker")
        wait_until(lambda: not self.chat.active, what="the chat reply")

    def sent(self) -> list[str]:
        return [command.action for command in self.bridge.drain()]

    def close(self) -> None:
        self.loop.shutdown()
        self.chat.shutdown()
        self.brain.close()
        self.memory.close()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path)
    yield made
    made.close()


def test_a_persona_route_is_handled_without_the_body_or_the_llm(rig: Rig) -> None:
    rig.type("Arya, roast mode")
    rig.type("my name is anna")
    assert rig.arya.mode == "roast" and rig.arya.speaker == "Anna"
    assert rig.sent() == [] and rig.backend.calls == 0
    kinds = [e.route.kind for e in rig.events if e.kind == "route" and e.route]
    assert kinds == ["persona", "persona"]
    assert rig.playback.phrases == ["mode_roast"] and rig.playback.sentences == [
        "Nice to meet you, Anna."]


def test_chat_goes_through_the_persona_prompt(rig: Rig) -> None:
    rig.type("funny mode")
    rig.type("tell me about yourself")
    system = rig.backend.last_messages[0]["content"]
    assert "Mode: funny" in system and rig.backend.calls == 1


@pytest.mark.parametrize("mode", ["calm", "funny", "roast"])
def test_commands_and_stop_are_identical_in_every_mode(rig: Rig, mode: str) -> None:
    rig.type(f"{mode} mode")
    rig.type("walk forward")
    rig.type("stop")
    rig.type("sit down")
    actions = rig.sent()
    assert actions == ["walk", "stop", "sit"]
    assert rig.bridge.stop_event.is_set()


def test_forget_me_by_voice_needs_a_yes_and_never_reaches_the_llm(rig: Rig) -> None:
    rig.type("my name is anna")
    rig.type("forget me")
    assert rig.playback.phrases[-1] == "say_yes_to_confirm"
    assert rig.memory.get_person("anna") is not None
    rig.type("yes")
    assert rig.memory.get_person("anna") is None
    assert rig.playback.phrases[-1] == "forget_done"
    assert rig.backend.calls == 0 and rig.sent() == []


def test_a_stop_during_the_question_still_stops_and_cancels_the_question(rig: Rig) -> None:
    rig.type("my name is anna")
    rig.type("walk forward")
    rig.type("forget me")
    rig.type("stop")
    assert rig.sent() == ["walk", "stop"]  # the stop went out first, as always
    rig.type("yes")
    assert rig.memory.get_person("anna") is not None


def test_a_yes_does_not_answer_a_walk_that_follows(rig: Rig) -> None:
    rig.type("my name is anna")
    rig.type("forget me")
    rig.type("walk forward")  # anything else cancels the question AND is carried out
    assert rig.sent() == ["walk"]
    rig.type("yes")
    assert rig.memory.get_person("anna") is not None


def test_a_typed_yes_without_a_question_is_just_chat(rig: Rig) -> None:
    rig.type("yes")
    assert rig.backend.calls == 1


def test_the_greeting_makes_arya_happy_and_motion_makes_her_busy(rig: Rig) -> None:
    rig.type("hello Arya")
    assert rig.arya.state().mood == "happy"


def test_the_loop_works_without_a_persona(tmp_path: Path) -> None:
    bridge = make_local_bridge()
    brain = BrainLoop(bridge)
    loop = VoiceLoop(QueueSource(), FakeStt({}), brain, threading.Event(), None)
    loop.start()
    loop.submit_text("roast mode")  # a persona route with nobody to act on it: nothing happens
    wait_until(lambda: loop.events_handled >= loop.events_published, what="the worker")
    time.sleep(0.05)
    assert bridge.drain() == []
    loop.shutdown()
    brain.close()


# -- "stop roasting": a real stop only when something is moving ---------------------------------
def test_stop_roasting_while_walking_sends_a_real_stop_then_goes_calm(rig: Rig) -> None:
    rig.type("roast mode")
    rig.type("walk forward")
    rig.type("stop roasting")
    assert rig.sent() == ["walk", "stop"]
    assert rig.bridge.stop_event.is_set()  # the stop_event fast path, like any stop word
    assert rig.arya.mode == "calm" and rig.playback.phrases[-1] == "mode_calm"
    assert rig.backend.calls == 0


def test_stop_roasting_during_a_transition_also_stops(rig: Rig) -> None:
    rig.type("roast mode")
    rig.type("sit down")  # a sit transition is a motion command that is active
    rig.type("stop the roast")
    assert rig.sent() == ["sit", "stop"] and rig.arya.mode == "calm"


def test_stop_roasting_while_idle_only_switches_the_mode(rig: Rig) -> None:
    rig.type("roast mode")
    rig.type("stop roasting")
    assert rig.sent() == []
    assert not rig.bridge.stop_event.is_set()
    assert rig.arya.mode == "calm"
    assert [e.route.kind for e in rig.events if e.kind == "route" and e.route] == [
        "persona", "persona"]


def test_stop_roasting_inside_a_longer_sentence_is_still_just_a_stop(rig: Rig) -> None:
    rig.type("roast mode")
    rig.type("walk forward")
    rig.type("stop roasting and stop walking")
    assert rig.sent() == ["walk", "stop"] and rig.arya.mode == "roast"  # mode untouched
