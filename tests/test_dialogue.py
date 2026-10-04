"""Step 9: status -> pre-rendered phrase (brain/dialogue.py), with a fake clock."""

from __future__ import annotations

import logging
import time

import pytest

import config
from brain.dialogue import Dialogue
from brain.status_hub import StatusHub
from bridge import Command, Status, make_local_bridge, new_command
from tests.fakes import FakeClock, StubPlayback, wait_until
from voice.tts import TtsError


def status(kind: str, ref: int | None = None, **detail: object) -> Status:
    return Status(status=kind, ref_seq=ref, detail=dict(detail), seq=1, timestamp=time.time())


class Rig:
    def __init__(self, **args: object) -> None:
        self.clock = FakeClock()
        self.playback = StubPlayback()
        self.dialogue = Dialogue(None, self.playback, self.clock.now, **args)  # type: ignore[arg-type]

    def sent(self, action: str) -> Command:
        command = new_command(action)
        self.dialogue.note_sent(command)
        return command


def test_accepted_is_okay_or_sure_alternating() -> None:
    rig = Rig(throttle_s=0.0)
    said = []
    for action in ("walk", "sit", "wave", "stand"):
        command = rig.sent(action)
        said.append(rig.dialogue.handle(status("accepted", command.seq)))
    assert said == ["okay", "sure", "okay", "sure"]


def test_accepted_stop_says_stopped() -> None:
    rig = Rig()
    command = rig.sent("stop")
    assert rig.dialogue.handle(status("accepted", command.seq)) == "stopped"


def test_an_accepted_for_a_command_we_did_not_send_is_silent() -> None:
    assert Rig().dialogue.handle(status("accepted", 999)) is None


@pytest.mark.parametrize(
    ("state", "phrase"),
    [("sitting", "already_sitting"), ("standing", "already_standing"),
     ("moving", "cant_do_that"), ("holding", "cant_do_that")],
)
def test_rejected_already_in_state_names_the_posture(state: str, phrase: str) -> None:
    rig = Rig()
    command = rig.sent("sit")
    assert rig.dialogue.handle(
        status("rejected", command.seq, reason="already_in_state", state=state)) == phrase


@pytest.mark.parametrize(
    ("reason", "phrase"),
    [("invalid_state", "cant_do_that"), ("invalid_params", "cant_do_that"),
     ("fallen", "fell_over"), ("something_new", "cant_do_that"),
     ("stale", None), ("superseded", None)],
)
def test_rejected_reasons(reason: str, phrase: str | None) -> None:
    rig = Rig()
    command = rig.sent("walk")
    assert rig.dialogue.handle(status("rejected", command.seq, reason=reason)) == phrase


def test_busy_fallen_and_error() -> None:
    rig = Rig(throttle_s=0.0)
    command = rig.sent("sit")
    assert rig.dialogue.handle(status("busy", command.seq, state="standing_up")) == "one_moment"
    assert rig.dialogue.handle(status("fallen", None, tilt_deg=60.0)) == "fell_over"
    assert rig.dialogue.handle(status("error", command.seq, message="x")) == "something_wrong"


def test_done_is_silent_by_default_and_speaks_when_enabled() -> None:
    quiet = Rig()
    command = quiet.sent("walk")
    assert config.DIALOGUE_SPEAK_DONE is False
    assert quiet.dialogue.handle(status("done", command.seq, action="walk")) is None
    loud = Rig(speak_done=True)
    command = loud.sent("walk")
    assert loud.dialogue.handle(status("done", command.seq, action="walk")) == "done"


def test_no_synthesis_only_prerendered_phrases() -> None:
    rig = Rig(throttle_s=0.0)
    for kind, extra in (("busy", {}), ("fallen", {}), ("error", {}),
                        ("rejected", {"reason": "invalid_state"})):
        rig.dialogue.handle(status(kind, None, **extra))
    assert rig.playback.phrases and all(p in config.TTS_PHRASES for p in rig.playback.phrases)
    assert rig.playback.said == []  # say() (synthesis) is never used


def test_the_same_phrase_is_throttled_then_allowed_again() -> None:
    rig = Rig(throttle_s=2.0)
    reject = status("rejected", None, reason="invalid_state")
    assert rig.dialogue.handle(reject) == "cant_do_that"
    rig.clock.advance(1.0)
    assert rig.dialogue.handle(reject) is None  # a moment ago
    assert rig.dialogue.handle(status("busy", None)) == "one_moment"  # another phrase is fine
    rig.clock.advance(1.5)
    assert rig.dialogue.handle(reject) == "cant_do_that"
    assert rig.playback.phrases == ["cant_do_that", "one_moment", "cant_do_that"]


def test_the_throttle_default_comes_from_config() -> None:
    assert Dialogue(None, StubPlayback()).throttle_s == config.DIALOGUE_THROTTLE_S


def test_every_phrase_the_dialogue_can_say_is_in_the_prerender_list() -> None:
    rig = Rig(speak_done=True, throttle_s=0.0)
    command = rig.sent("walk")
    cases = [status("accepted", command.seq), status("done", command.seq, action="walk"),
             status("busy", None), status("fallen", None), status("error", None)]
    cases += [status("rejected", None, reason=r, state=s) for r in
              ("already_in_state", "invalid_state", "fallen", "invalid_params")
              for s in ("sitting", "standing", "moving")]
    stop = rig.sent("stop")
    cases.append(status("accepted", stop.seq))
    for case in cases:
        phrase = rig.dialogue.phrase_for(case)
        assert phrase is None or phrase in config.TTS_PHRASES, phrase
    for needed in ("cant_think", "tell_me_after_stop", "okay", "sure", "stopped", "one_moment"):
        assert needed in config.TTS_PHRASES


def test_a_speech_error_is_logged_and_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def say_phrase(self, name: str) -> int:
            raise TtsError("phrase not rendered")

    dialogue = Dialogue(None, Broken(), FakeClock().now)
    command = new_command("sit")
    dialogue.note_sent(command)
    with caplog.at_level(logging.ERROR):
        assert dialogue.handle(status("accepted", command.seq)) is None
    assert "could not say" in caplog.text


def test_the_dialogue_thread_speaks_from_a_hub_subscription() -> None:
    bridge = make_local_bridge()
    hub = StatusHub(bridge)
    playback = StubPlayback()
    dialogue = Dialogue(hub.subscribe("dialogue"), playback, FakeClock().now)
    hub.start()
    dialogue.start()
    try:
        command = new_command("sit")
        dialogue.note_sent(command)
        bridge.report(status("rejected", command.seq, reason="already_in_state", state="sitting"))
        wait_until(lambda: bool(playback.phrases), what="the phrase")
        assert playback.phrases == ["already_sitting"]
    finally:
        dialogue.shutdown()
        hub.stop()


def test_the_sent_memory_is_bounded() -> None:
    rig = Rig()
    for _ in range(200):
        rig.sent("walk")
    assert len(rig.dialogue._sent) == 64
