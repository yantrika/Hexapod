"""Step 8 with the REAL Vosk model and Piper-rendered speech (no microphone, no speaker, no body).

Skipped when the models are missing. Commands are read from an in-process bridge.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

from brain.brain_loop import BrainLoop
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import Status, make_local_bridge
from tests.fakes import wait_until
from tests.voice_fixtures import samples_of
from voice.audio import FileSource, QueueSource
from voice.stt import VoskStt


class Rig:
    def __init__(self, stt: VoskStt, source: object) -> None:
        self.bridge = make_local_bridge()
        self.speaking = threading.Event()
        self.brain = BrainLoop(self.bridge)
        self.events: list[VoiceEvent] = []
        self.loop = VoiceLoop(source, stt, self.brain, self.speaking, None,  # type: ignore[arg-type]
                              self.events.append)

    def run(self, timeout: float = 30.0) -> None:
        self.loop.start()
        assert self.loop.wait_idle(timeout), f"voice loop did not finish: {self.events}"

    def close(self) -> None:
        self.loop.shutdown()
        self.brain.close()

    def finals(self) -> list[str]:
        return [e.text for e in self.events if e.kind == "final" and e.text]


@pytest.fixture
def make_rig(vosk_stt: VoskStt) -> Iterator[Callable[[object], Rig]]:
    made: list[Rig] = []

    def make(source: object) -> Rig:
        made.append(Rig(vosk_stt, source))
        return made[-1]

    yield make
    for rig in made:
        rig.close()


def test_sit_down_by_voice_sends_a_sit(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    rig = make_rig(FileSource(speech_wavs["sit down"]))
    rig.run()
    assert rig.finals() == ["sit down"]
    assert [c.action for c in rig.bridge.drain()] == ["sit"]


def test_walk_forward_by_voice_is_kept_alive_by_heartbeats(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    rig = make_rig(FileSource(speech_wavs["walk forward"]))
    rig.run()
    command = rig.bridge.drain()[0]
    assert (command.action, command.params["direction"]) == ("walk", "fwd")
    rig.bridge.report(Status("accepted", command.seq, {}, 1, time.time()))  # the body answers
    deadline = time.monotonic() + 3.0
    beats: list[str] = []
    while time.monotonic() < deadline and not beats:
        rig.brain.pump()
        beats = [c.action for c in rig.bridge.drain()]
        time.sleep(0.05)
    assert beats == ["heartbeat"] and rig.brain.keeper.active


def test_a_sentence_with_sat_down_in_it_is_chat_not_a_command(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    rig = make_rig(FileSource(speech_wavs["I sat down for lunch"]))
    rig.run()
    assert rig.finals() == ["i sat down for lunch"]
    assert rig.bridge.drain() == []
    route = next(e for e in rig.events if e.kind == "route")
    assert route.route is not None and route.route.kind == "chat"


def test_stop_in_a_partial_result_stops_before_the_final_result(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    source = FileSource(speech_wavs["stop"], realtime=True, lead_silence_s=0.3)
    rig = make_rig(source)
    rig.run()
    stops = [e for e in rig.events if e.kind == "route" and e.route and e.route.kind == "stop"]
    final = next(e for e in rig.events if e.kind == "final" and e.text)
    print(f"early stop at +{stops[0].timestamp - final.timestamp:+.2f} s relative to the final")
    assert len(stops) == 1 and stops[0].early_stop  # sent from the partial, once
    assert stops[0].timestamp < final.timestamp  # ... before the final result arrived
    assert [c.action for c in rig.bridge.drain()] == ["stop"]


def blocks_of(samples: np.ndarray, size: int = 4000) -> list[np.ndarray]:
    padded = np.concatenate([samples, np.zeros(-len(samples) % size, dtype=np.int16)])
    return [padded[i:i + size] for i in range(0, len(padded), size)]


def test_hexas_own_voice_is_ignored_and_the_same_audio_later_is_obeyed(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    source = QueueSource()
    rig = make_rig(source)
    rig.loop.start()
    walk = samples_of(speech_wavs["walk forward"])
    silence = np.zeros(16000 * 2, dtype=np.int16)
    count = 0
    rig.speaking.set()  # hexa is talking: "walk forward" comes out of its own speaker
    for block in blocks_of(np.concatenate([walk, silence])):
        source.push(block)
        count += 1
    wait_until(lambda: rig.loop.blocks_seen >= count, 30.0, "the blocks to be read")
    time.sleep(0.3)
    assert rig.bridge.drain() == [] and rig.finals() == []  # never decoded, never obeyed
    rig.speaking.clear()
    for block in blocks_of(np.concatenate([silence[:8000], walk, silence])):
        source.push(block)
        count += 1
    wait_until(lambda: rig.loop.blocks_seen >= count, 30.0, "the second pass to be read")
    wait_until(lambda: bool(rig.finals()), 30.0, "a recognised utterance")
    assert rig.finals() == ["walk forward"]
    wait_until(lambda: rig.loop.events_handled >= rig.loop.events_published)
    assert [c.action for c in rig.bridge.drain()] == ["walk"]


# --- Step 8b: the command grammar recognizer ------------------------------------------------
def finals(rig: Rig) -> list[VoiceEvent]:
    return [e for e in rig.events if e.kind == "final" and e.text]


def routes(rig: Rig) -> list[VoiceEvent]:
    return [e for e in rig.events if e.kind == "route" and not e.early_stop]


@pytest.mark.parametrize(
    ("phrase", "action", "path"),
    [
        ("sit down", "sit", "grammar-command"),
        ("turn left", "turn", "grammar-command"),
        ("wave", "wave", "grammar-command"),
    ],
)
def test_commands_are_routed_by_the_grammar_path(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path], phrase: str, action: str,
    path: str,
) -> None:
    rig = make_rig(FileSource(speech_wavs[phrase]))
    rig.run()
    (final,) = finals(rig)
    assert final.grammar is not None
    print(f"{phrase!r}: free {final.text!r}, grammar {final.grammar.text!r} "
          f"conf {final.grammar.mean_conf:.2f} -> {routes(rig)[0].path}")
    (routed,) = routes(rig)
    assert routed.path == path and routed.route is not None and routed.route.action == action
    assert [c.action for c in rig.bridge.drain()].count(action) >= 1


def test_stop_is_sent_once_and_the_grammar_agrees_on_the_final(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    """The US model's partial already says "stop", so the early stop goes first; the grammar
    result on the final says the same thing and is not sent a second time."""
    from brain.stt_decision import decide

    rig = make_rig(FileSource(speech_wavs["stop"], realtime=True, lead_silence_s=0.3))
    rig.run()
    (final,) = finals(rig)
    assert final.grammar is not None
    assert decide(final.text, final.grammar).path == "grammar-stop"
    assert [c.action for c in rig.bridge.drain()] == ["stop"]  # once, not twice
    assert [e.path for e in rig.events if e.kind == "route"] == ["partial-stop"]


@pytest.mark.parametrize(
    "sentence",
    ["I sat down for lunch", "what is the weather today", "I'll walk you through it",
     "turn up the music"],
)
def test_chat_sentences_never_become_commands_even_though_the_grammar_forces_a_match(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path], sentence: str
) -> None:
    rig = make_rig(FileSource(speech_wavs[sentence]))
    rig.run()
    (final,) = finals(rig)
    assert final.grammar is not None
    print(f"{sentence!r}: the grammar was forced to {final.grammar.text!r} "
          f"(conf {final.grammar.mean_conf:.2f}); free text {final.text!r}")
    (routed,) = routes(rig)
    assert routed.route is not None and routed.route.kind == "chat"
    assert routed.path == "free"
    assert rig.bridge.drain() == []  # nothing reached the body


def test_a_long_chat_sentence_goes_to_chat_with_the_free_text_intact(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path]
) -> None:
    sentence = "turn left and then walk forward for a while"
    rig = make_rig(FileSource(speech_wavs[sentence]))
    rig.run()
    (final,) = finals(rig)
    assert final.text == sentence  # the free-text recognizer's words, not the grammar's
    assert final.grammar is not None and "turn left" in final.grammar.text  # it did find commands
    (routed,) = routes(rig)
    assert routed.route is not None and routed.route.kind == "chat" and routed.path == "free"
    assert rig.bridge.drain() == []


def test_the_indian_model_stop_is_rescued_by_the_grammar(
    speech_wavs: dict[str, Path],
) -> None:
    """The Indian English model heard Piper's "stop" as "start"; the grammar still says stop."""
    try:
        stt = VoskStt("in")
    except Exception as error:  # noqa: BLE001
        pytest.skip(str(error))
    rig = Rig(stt, FileSource(speech_wavs["stop"]))
    try:
        rig.run()
        (final,) = finals(rig)
        heard = final.grammar.text if final.grammar else None
        print(f"Indian model: free {final.text!r}, grammar {heard!r}")
        (routed,) = routes(rig)
        assert routed.route is not None and routed.route.kind == "stop"
        assert [c.action for c in rig.bridge.drain()] == ["stop"]
    finally:
        rig.close()


def test_every_final_is_written_to_the_transcript_log(
    make_rig: Callable[[object], Rig], speech_wavs: dict[str, Path], tmp_path: Path
) -> None:
    import json

    from brain.transcript_log import TranscriptLog

    rig = make_rig(FileSource(speech_wavs["sit down"]))
    rig.loop.transcripts = TranscriptLog(tmp_path / "t.jsonl", model="us")
    rig.run()
    (line,) = (tmp_path / "t.jsonl").read_text().splitlines()
    record = json.loads(line)
    assert record["kind"] == "final" and record["path"] == "grammar-command"
    assert record["free"]["text"] == "sit down" and record["grammar"]["text"] == "sit down"
    assert record["free"]["words"][0][0] == "sit" and record["route"]["action"] == "sit"
