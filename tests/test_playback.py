"""Step 7 checks: the clearable playback thread, with a fake engine, fake sink and fake clock.

No sound device, no Piper: durations come from the fake clock (``tests/fakes.py``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

import config
from tests.fakes import FakeClock, FakeEngine, FakeSink, run_fake_time, wait_until
from voice.playback import Playback, Utterance
from voice.tts import AudioClip, TtsError, write_wav

TAIL = 0.4


class Rig:
    def __init__(self, rtf: float = 0.0, **engine_args: object) -> None:
        self.clock = FakeClock()
        self.engine = FakeEngine(self.clock, rtf=rtf, **engine_args)  # type: ignore[arg-type]
        self.sink = FakeSink(self.clock)
        self.started: list[Utterance] = []
        self.playback = Playback(
            self.engine, self.sink, clock=self.clock, tail_s=TAIL, on_start=self.started.append
        )

    def played(self) -> list[str]:
        return [self.engine.text_of(int(play["code"])) for play in self.sink.plays]

    def finish(self, limit_s: float = 120.0) -> None:
        """Run fake time until everything queued has played and the tail is over."""
        run_fake_time(self.clock, lambda: self.playback.pending == 0, limit_s=limit_s)
        run_fake_time(self.clock, lambda: not self.playback.speaking.is_set(), limit_s=limit_s)


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig()
    made.playback.start()
    yield made
    made.playback.shutdown()


def test_items_play_in_order_one_at_a_time(rig: Rig) -> None:
    rig.playback.say("First one. Second one. Third one. Fourth one.")
    rig.finish()
    assert rig.played() == ["First one.", "Second one.", "Third one.", "Fourth one."]
    plays = rig.sink.plays
    for before, after in zip(plays, plays[1:], strict=False):
        assert after["start"] >= before["end"] - 1e-9  # never two at once
    assert rig.playback.errors == 0


def test_two_say_calls_keep_their_order(rig: Rig) -> None:
    rig.playback.say("Alpha.")
    rig.playback.say("Beta.")
    rig.finish()
    assert rig.played() == ["Alpha.", "Beta."]
    assert [u.group for u in rig.started] == [1, 2]


# --- clear ----------------------------------------------------------------------------------
def test_clear_stops_the_current_utterance_and_flushes_the_queue(rig: Rig) -> None:
    rig.playback.say("Aaa aaa. Bbb bbb. Ccc ccc.")
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    rig.clock.advance(0.5)  # 0.5 s into a 2 s sentence
    time.sleep(0.01)
    started = time.perf_counter()
    rig.playback.clear()
    elapsed = time.perf_counter() - started
    assert elapsed < config.TTS_CLEAR_MAX_S
    wait_until(lambda: rig.sink.plays[0]["end"] >= 0, what="the aborted play to return")
    first = rig.sink.plays[0]
    assert first["aborted"] == 1.0
    assert first["end"] - first["start"] < 1.0  # stopped early, not after the full 2 s
    assert rig.playback.wait_idle(2.0)  # the queue is flushed
    rig.clock.advance(5.0)
    time.sleep(0.05)
    assert len(rig.sink.plays) == 1  # B and C never played
    assert rig.played() == ["Aaa aaa."]


def test_the_thread_keeps_working_after_a_clear(rig: Rig) -> None:
    rig.playback.say("Aaa aaa. Bbb bbb.")
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    rig.playback.clear()
    rig.playback.say("After it.")
    rig.finish()
    assert rig.played()[-1] == "After it."
    assert "Bbb bbb." not in rig.played()


def test_clear_when_idle_is_a_no_op(rig: Rig) -> None:
    rig.playback.clear()
    rig.playback.clear()
    assert rig.sink.abort_calls == 0
    assert not rig.playback.speaking.is_set()
    assert rig.playback.pending == 0
    rig.playback.say("Still works.")
    rig.finish()
    assert rig.played() == ["Still works."]


def test_clear_discards_a_sentence_that_was_still_being_synthesized() -> None:
    rig = Rig(rtf=1.0)
    rig.playback.start()
    try:
        rig.playback.say("Slow one.")
        wait_until(lambda: bool(rig.engine.started), what="synthesis to start")
        rig.clock.advance(0.5)  # half way through rendering
        rig.playback.clear()
        rig.clock.advance(5.0)  # the render finishes, but it was cleared
        assert rig.playback.wait_idle(2.0)
        time.sleep(0.05)
        assert rig.sink.plays == []
    finally:
        rig.playback.shutdown()


def test_clear_is_safe_from_many_threads(rig: Rig) -> None:
    rig.playback.say("One. Two. Three. Four. Five. Six.")
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    threads = [threading.Thread(target=rig.playback.clear) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)
    assert not any(thread.is_alive() for thread in threads)
    assert rig.playback.wait_idle(2.0)


# --- speaking gate ---------------------------------------------------------------------------
def test_speaking_is_set_during_playback_and_the_tail_then_cleared(rig: Rig) -> None:
    speaking = rig.playback.speaking
    assert not speaking.is_set()
    rig.playback.say("Hello there you.")  # 2 s of audio
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    assert speaking.is_set()
    run_fake_time(rig.clock, lambda: rig.playback.pending == 0)  # playback ended just now
    assert speaking.is_set()  # ... and the tail has started
    rig.clock.advance(TAIL * 0.5)
    time.sleep(0.05)
    assert speaking.is_set()  # inside the tail
    rig.clock.advance(TAIL)
    wait_until(lambda: not speaking.is_set(), what="the tail to end")


def test_speaking_stays_set_between_gapless_sentences(rig: Rig) -> None:
    rig.playback.say("One. Two. Three.")
    samples: list[bool] = []

    def done() -> bool:
        samples.append(rig.playback.speaking.is_set())
        return len(rig.sink.plays) == 3 and rig.sink.plays[-1]["end"] >= 0

    run_fake_time(rig.clock, done)
    assert all(samples[samples.index(True):])  # once on, on until the end


def test_the_tail_follows_a_clear_too(rig: Rig) -> None:
    rig.playback.say("A long one.")
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    rig.playback.clear()
    wait_until(lambda: rig.sink.plays[0]["end"] >= 0)
    assert rig.playback.speaking.is_set()  # the room still rings for the tail
    rig.clock.advance(TAIL + 0.1)
    wait_until(lambda: not rig.playback.speaking.is_set())


def test_a_zero_tail_clears_speaking_at_the_end() -> None:
    rig = Rig()
    rig.playback.tail_s = 0.0
    rig.playback.start()
    try:
        rig.playback.say("Short.")
        run_fake_time(rig.clock, lambda: rig.playback.pending == 0)
        wait_until(lambda: not rig.playback.speaking.is_set())
    finally:
        rig.playback.shutdown()


def test_a_shared_speaking_event_can_be_injected() -> None:
    clock = FakeClock()
    shared = threading.Event()
    playback = Playback(FakeEngine(clock), FakeSink(clock), speaking=shared, clock=clock)
    assert playback.speaking is shared


# --- errors -------------------------------------------------------------------------------------
def test_a_synthesis_error_skips_that_utterance_and_the_thread_lives() -> None:
    rig = Rig(fail_on=("Bad one.",))
    rig.playback.start()
    try:
        rig.playback.say("Good one. Bad one. Fine one.")
        rig.finish()
        assert rig.played() == ["Good one.", "Fine one."]
        assert rig.playback.errors == 1
        rig.playback.say("Later one.")
        rig.finish()
        assert rig.played()[-1] == "Later one."
    finally:
        rig.playback.shutdown()


def test_an_audio_error_skips_that_utterance_and_the_thread_lives() -> None:
    rig = Rig()
    rig.sink.fail_on_start = 2
    rig.playback.start()
    try:
        rig.playback.say("First one. Second one. Third one.")
        rig.finish()
        assert rig.played() == ["First one.", "Third one."]
        assert rig.playback.errors == 1
    finally:
        rig.playback.shutdown()


# --- prefetch -----------------------------------------------------------------------------------
def test_the_next_sentence_is_synthesized_while_the_current_one_plays() -> None:
    rig = Rig(rtf=0.5)
    rig.playback.start()
    try:
        rig.playback.say("Sentence one. Sentence two. Sentence three.")
        rig.finish()
        one_end = rig.sink.plays[0]["end"]
        two_synth_start = rig.engine.started[1][1]
        assert two_synth_start < one_end  # prefetch: rendering 2 began before 1 finished playing
    finally:
        rig.playback.shutdown()


def gaps_for(rtf: float, sentences: int = 5) -> tuple[float, list[float]]:
    """Time to first sound and the silent gaps between the following sentences (fake seconds)."""
    rig = Rig(rtf=rtf)
    rig.playback.start()
    try:
        rig.playback.say(" ".join(f"Sentence number {n}." for n in range(sentences)))
        rig.finish()
        plays = rig.sink.plays
        first_sound = plays[0]["start"] - 1000.0  # FakeClock starts at 1000
        gaps = [
            after["start"] - before["end"] for before, after in zip(plays, plays[1:], strict=False)
        ]
        return first_sound, gaps
    finally:
        rig.playback.shutdown()


@pytest.mark.parametrize(("rtf", "gaps_expected"), [(0.8, False), (1.3, True)])
def test_prefetch_gaps_depend_on_the_real_time_factor(rtf: float, gaps_expected: bool) -> None:
    first, gaps = gaps_for(rtf)
    print(f"\nrtf {rtf}: first sound after {first:.2f} s of fake time; gaps "
          + ", ".join(f"{gap:.2f}" for gap in gaps))
    assert first == pytest.approx(rtf * 2.0, abs=0.1)  # a 2 s sentence needs rtf*2 s to render
    if gaps_expected:
        # each sentence takes 2.6 s to render and 2 s to play: 0.6 s of silence per sentence
        assert all(gap == pytest.approx(0.6, abs=0.1) for gap in gaps)
    else:
        assert all(gap < 0.1 for gap in gaps)  # at most the polling slack


def test_phrases_are_played_without_synthesis(tmp_path: Path) -> None:
    clock = FakeClock()
    engine, sink = FakeEngine(clock, rtf=5.0), FakeSink(clock)
    write_wav(tmp_path / "okay.wav", AudioClip(np.full(1500, 7, dtype=np.int16), 1000))
    playback = Playback(engine, sink, clock=clock, tail_s=TAIL, phrases_dir=tmp_path)
    playback.start()
    try:
        playback.say_phrase("okay")
        run_fake_time(clock, lambda: playback.pending == 0)
        assert engine.started == []  # no synthesis happened, even with rtf 5
        assert sink.plays[0]["start"] == pytest.approx(1000.0, abs=0.1)  # instantly
        assert sink.plays[0]["code"] == 7.0
    finally:
        playback.shutdown()


def test_a_missing_or_unknown_phrase_is_a_clear_error(tmp_path: Path) -> None:
    clock = FakeClock()
    playback = Playback(FakeEngine(clock), FakeSink(clock), clock=clock, phrases_dir=tmp_path)
    with pytest.raises(TtsError, match="prerender_phrases"):
        playback.say_phrase("okay")
    with pytest.raises(TtsError, match="unknown phrase"):
        playback.say_phrase("no_such_phrase")


def test_a_full_queue_drops_new_sentences_without_blocking() -> None:
    clock = FakeClock()
    playback = Playback(FakeEngine(clock, rtf=100.0), FakeSink(clock), clock=clock, queue_size=2)
    # not started: nothing consumes, so the third sentence finds the queue full
    playback.say("One. Two. Three. Four.")
    assert playback.pending == 2


def test_blank_text_queues_nothing(rig: Rig) -> None:
    assert rig.playback.say("  \n ... ") == 0
    assert rig.playback.pending == 0


# --- shutdown -------------------------------------------------------------------------------------
def tts_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("tts-")]


def test_shutdown_leaves_no_threads_and_closes_the_sink_and_engine() -> None:
    rig = Rig()
    rig.playback.start()
    assert len(tts_threads()) == 2
    rig.playback.say("Talking when it ends. And more. And more.")
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    started = time.perf_counter()
    rig.playback.shutdown()
    assert time.perf_counter() - started < 2.0
    assert tts_threads() == []
    assert rig.sink.closed and rig.engine.closed
    assert not rig.playback.speaking.is_set()


def test_shutdown_while_synthesis_is_in_flight_does_not_hang() -> None:
    rig = Rig(rtf=50.0)  # synthesis would take forever
    rig.playback.start()
    rig.playback.say("Never finishes.")
    wait_until(lambda: bool(rig.engine.started))
    started = time.perf_counter()
    rig.playback.shutdown()  # closing the engine ends the stuck synthesis
    assert time.perf_counter() - started < 2.0
    assert tts_threads() == []


def test_shutdown_twice_and_say_after_shutdown_are_harmless() -> None:
    rig = Rig()
    rig.playback.start()
    rig.playback.shutdown()
    rig.playback.shutdown()
    assert rig.playback.say("Too late.") == 0
    assert rig.playback.pending == 0
    assert tts_threads() == []


def test_the_sink_is_opened_by_the_playback_thread(rig: Rig) -> None:
    wait_until(lambda: rig.sink.opened, what="sink.open()")


def test_synthesis_runs_only_a_bounded_distance_ahead(rig: Rig) -> None:
    rig.playback.say(" ".join(f"Sentence number {n}." for n in range(8)))
    run_fake_time(rig.clock, lambda: bool(rig.sink.plays))
    time.sleep(0.1)  # let the worker run as far ahead as it is allowed to
    # one playing, TTS_PREFETCH_SIZE waiting for the speaker, one being rendered / handed over
    assert len(rig.engine.started) <= 2 + config.TTS_PREFETCH_SIZE
