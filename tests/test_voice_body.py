"""Step 8 end to end: Piper-rendered speech -> real Vosk -> router -> bridge -> a headless body.

Skipped without the models. Run this file on its own (it spawns the body process):
``nice -n 19 pytest tests/test_voice_body.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

import config
from body.process import BodyProbe, BodyProcess
from brain.brain_loop import BrainLoop
from brain.status_hub import StatusHub, Subscription
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import Status, make_bridge
from tests.voice_fixtures import samples_of
from voice.audio import FileSource
from voice.stt import VoskStt


class Setup:
    def __init__(self, stt: VoskStt) -> None:
        self.bridge = make_bridge()
        self.probe = BodyProbe()
        self.body = BodyProcess(self.bridge, True, self.probe)
        self.hub = StatusHub(self.bridge)
        self.seen: Subscription = self.hub.subscribe("test")
        self.brain = BrainLoop(self.bridge, hub=self.hub)
        self.stt = stt
        self.statuses: list[Status] = []
        self.events: list[VoiceEvent] = []
        self.loop: VoiceLoop | None = None

    def start(self) -> Setup:
        self.hub.start()
        self.body.start()
        assert self.body.wait_ready(), "the body did not start"
        return self

    def listen(self, source: FileSource) -> VoiceLoop:
        self.loop = VoiceLoop(source, self.stt, self.brain, threading.Event(), None,
                              self.events.append)
        self.loop.start()
        return self.loop

    def until(self, match: object, timeout: float) -> Status:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.seen.get(timeout=0.05)
            if status is not None:
                self.statuses.append(status)
                if match(status):  # type: ignore[operator]
                    return status
        raise AssertionError(f"timed out; statuses so far: {self.statuses[-6:]}")

    def close(self) -> None:
        if self.loop is not None:
            self.loop.shutdown()
        self.brain.close()
        self.hub.stop()
        self.body.shutdown()


@pytest.fixture
def setup(vosk_stt: VoskStt) -> Iterator[Setup]:
    made = Setup(vosk_stt).start()
    yield made
    made.close()


def test_saying_sit_down_makes_the_body_sit(setup: Setup, speech_wavs: dict[str, Path]) -> None:
    setup.listen(FileSource(speech_wavs["sit down"]))
    setup.until(lambda s: s.status == "accepted", 10.0)
    done = setup.until(lambda s: s.status == "done", config.SIT_STAND_TRANSITION_S + 6.0)
    assert done.detail["action"] == "sit"


def test_saying_walk_forward_keeps_walking_until_stop_is_said(
    setup: Setup, speech_wavs: dict[str, Path]
) -> None:
    walk, stop = samples_of(speech_wavs["walk forward"]), samples_of(speech_wavs["stop"])
    gap = np.zeros(int(16000 * 4.5), dtype=np.int16)  # about 4 s of walking before "stop"
    source = FileSource(np.concatenate([walk, gap, stop]), realtime=True, pad_silence_s=2.0)
    setup.listen(source)
    accepted = setup.until(lambda s: s.status == "accepted", 10.0)
    x0 = setup.probe.get("base_x")
    done = setup.until(lambda s: s.status == "done", 15.0)
    assert done.detail["action"] == "stop"  # the next "done" is the stop, not a watchdog
    assert accepted.ref_seq is not None
    print(f"walked {(setup.probe.get('base_x') - x0) * 100:.1f} cm before the spoken stop")
    assert setup.probe.get("base_x") - x0 > 0.03  # it really walked, the heartbeats held it


def test_a_chat_sentence_does_not_move_the_body(setup: Setup, speech_wavs: dict[str, Path]) -> None:
    loop = setup.listen(FileSource(speech_wavs["I sat down for lunch"]))
    assert loop.wait_idle(30.0)
    time.sleep(0.5)
    assert [s.status for s in setup.seen.get_all()] == []  # not a single status: nothing sent
