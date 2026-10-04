"""Step 8b: VoskStt runs ONE model with TWO recognizers; both are fed, reset and flushed together.

Uses fake Vosk classes (no model needed); the real model runs in ``test_voice_loop.py``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from brain.brain_loop import BrainLoop
from brain.voice_loop import VoiceLoop
from bridge import make_local_bridge
from tests.fakes import marker_block, wait_until
from voice.audio import QueueSource
from voice.stt import VoskStt, command_grammar


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
