"""Step 8: audio sources (no microphone, no sound card)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

import config
from tests.fakes import marker_block
from voice.audio import EndOfAudio, FileSource, MicSource, QueueSource, resample
from voice.tts import AudioClip, write_wav


def test_mic_queue_overflow_drops_the_oldest_and_never_blocks() -> None:
    mic = MicSource(queue_blocks=3)
    started = time.perf_counter()
    for value in range(1, 11):  # a reader that never reads: 10 blocks into a queue of 3
        mic.offer(marker_block(value))
    assert time.perf_counter() - started < 0.1  # the audio callback is never held up
    assert mic.dropped == 7
    kept = [int(mic.read(0.1)[0]) for _ in range(3)]  # type: ignore[index]
    assert kept == [8, 9, 10]  # the newest blocks survive
    assert mic.read(0.01) is None


def test_mic_read_times_out_with_none() -> None:
    assert MicSource().read(0.01) is None


def test_mic_stop_before_start_is_harmless() -> None:
    MicSource().stop()


def test_the_mic_callback_only_copies() -> None:
    mic = MicSource(queue_blocks=2)
    indata = np.arange(20, dtype=np.int16).reshape(10, 2)
    mic._callback(indata, 10, None, None)
    indata[:] = 0  # the audio driver reuses its buffer: the queued block must be a copy
    block = mic.read(0.1)
    assert block is not None and block.tolist() == list(range(0, 20, 2))


def test_file_source_blocks_padding_and_end() -> None:
    samples = np.ones(2500, dtype=np.int16)
    source = FileSource(samples, blocksize=1000, pad_silence_s=0.25, lead_silence_s=0.0)
    source.start()
    blocks = []
    with pytest.raises(EndOfAudio):
        while True:
            block = source.read(0.1)
            assert block is not None
            blocks.append(block)
    joined = np.concatenate(blocks)
    assert len(joined) == 2500 + 4000  # 0.25 s of padding at 16 kHz
    assert joined[:2500].all() and not joined[2500:].any()
    assert all(len(b) == 1000 for b in blocks[:-1])


def test_file_source_reads_a_wav_and_resamples(tmp_path: Path) -> None:
    write_wav(tmp_path / "x.wav", AudioClip(np.full(8000, 5, dtype=np.int16), 8000))  # 1 s
    source = FileSource(tmp_path / "x.wav", pad_silence_s=0.0)
    assert source.duration_s == pytest.approx(1.0)  # 8 kHz became 16 kHz
    assert len(resample(np.arange(100, dtype=np.int16), 8000, 16000)) == 200


def test_file_source_realtime_paces_like_a_microphone() -> None:
    source = FileSource(np.ones(1600, dtype=np.int16), blocksize=800, realtime=True,
                        pad_silence_s=0.0)
    source.start()
    started = time.monotonic()
    first, second = source.read(1.0), source.read(1.0)
    elapsed = time.monotonic() - started
    assert first is not None and second is not None
    assert 0.09 <= elapsed < 0.4  # 100 ms of audio takes 100 ms to arrive
    assert source.speech_end_at == pytest.approx(source.started_at + 0.1, abs=1e-6)  # type: ignore[operator]


def test_file_source_realtime_returns_none_when_the_block_is_not_ready() -> None:
    source = FileSource(np.ones(16000, dtype=np.int16), blocksize=16000, realtime=True)
    source.start()
    assert source.read(0.02) is None  # a 1 s block is not there after 20 ms


def test_queue_source_push_and_close() -> None:
    source = QueueSource()
    source.push(marker_block(3))
    block = source.read(0.1)
    assert block is not None and int(block[0]) == 3
    assert source.read(0.01) is None
    source.close()
    with pytest.raises(EndOfAudio):
        source.read(0.1)


def test_audio_constants_are_16k_mono_blocks() -> None:
    assert config.AUDIO_SAMPLE_RATE == 16000 and config.MIC_QUEUE_BLOCKS >= 2
