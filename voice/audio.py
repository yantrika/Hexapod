"""Audio input: the ``AudioSource`` interface, the microphone and a WAV file source.

Everything downstream (the recognizer, the self-hearing gate) reads blocks of 16 kHz mono int16
through ``AudioSource`` and never knows where they come from: the local microphone now, a
network stream (the Step 12 phone page) later, a WAV file in tests. Only this module touches the
audio input (sounddevice is imported here, lazily); ``voice/playback.py`` owns the output.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

import config
from voice.tts import read_wav

logger = logging.getLogger(__name__)

Block = NDArray[np.int16]


class EndOfAudio(Exception):
    """A finite source (a file) has delivered its last block."""


class AudioSource(Protocol):
    """A stream of 16 kHz mono int16 blocks."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self, timeout: float) -> Block | None:
        """The next block, waiting up to *timeout* s; None if none arrived; ``EndOfAudio`` at
        the end of a finite source."""
        ...


def list_input_devices() -> list[tuple[int, str, int, float, bool]]:
    """``(index, name, input channels, default rate, is default)`` of every input device."""
    import sounddevice

    default_input = sounddevice.default.device[0]
    return [
        (index, str(device["name"]), int(device["max_input_channels"]),
         float(device["default_samplerate"]), index == default_input)
        for index, device in enumerate(sounddevice.query_devices())
        if device["max_input_channels"] > 0
    ]


class MicSource:
    """The microphone. The audio callback only copies a block into a bounded queue (dropping the
    OLDEST block when the reader falls behind, never blocking); all processing happens elsewhere."""

    def __init__(
        self,
        device: int | None = config.MIC_DEVICE,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        blocksize: int = config.AUDIO_BLOCKSIZE,
        queue_blocks: int = config.MIC_QUEUE_BLOCKS,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.dropped = 0  # blocks lost because the reader was too slow
        self._queue: queue.Queue[Block] = queue.Queue(maxsize=queue_blocks)
        self._stream: Any = None

    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice  # only here: nothing else touches the audio input

        self._stream = sounddevice.InputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", blocksize=self.blocksize,
            device=self.device, callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.warning("microphone status: %s", status)
        self.offer(indata[:, 0].copy())  # copy only: no processing in the audio callback

    def offer(self, block: Block) -> None:
        """Queue *block*; when full, drop the oldest. Never blocks (the callback calls this)."""
        while True:
            try:
                self._queue.put_nowait(block)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass

    def read(self, timeout: float) -> Block | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                logger.exception("could not close the microphone stream")


def resample(samples: Block, rate_from: int, rate_to: int) -> Block:
    """Linear-interpolation resampling (enough for tests; real input is already 16 kHz)."""
    if rate_from == rate_to or len(samples) == 0:
        return samples
    count = int(len(samples) * rate_to / rate_from)
    positions = np.linspace(0, len(samples) - 1, count)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16)


class FileSource:
    """A WAV file (or samples) as a source, followed by *pad_silence_s* of silence so a recognizer
    can finish the last utterance. ``realtime`` paces blocks like a microphone (block *i* arrives
    when it would have been captured); otherwise blocks come as fast as they are read."""

    def __init__(
        self,
        audio: Path | str | Block,
        blocksize: int = config.AUDIO_BLOCKSIZE,
        realtime: bool = False,
        pad_silence_s: float = 2.0,
        lead_silence_s: float = 0.0,
        sample_rate: int = config.AUDIO_SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(audio, (str, Path)):
            clip = read_wav(Path(audio))
            samples = resample(clip.samples, clip.sample_rate, sample_rate)
        else:
            samples = audio
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.realtime = realtime
        self._clock = clock
        lead = np.zeros(int(lead_silence_s * sample_rate), dtype=np.int16)
        tail = np.zeros(int(pad_silence_s * sample_rate), dtype=np.int16)
        self._samples = np.concatenate([lead, samples, tail])
        self._speech_end = len(lead) + len(samples)  # sample index where the speech stops
        self._index = 0
        self.started_at: float | None = None

    @property
    def speech_end_at(self) -> float | None:
        """Clock time at which the speech ends (``realtime`` only, after the first read)."""
        if self.started_at is None:
            return None
        return self.started_at + self._speech_end / self.sample_rate

    def start(self) -> None:
        self._index = 0
        self.started_at = None

    def stop(self) -> None:
        pass

    def read(self, timeout: float) -> Block | None:
        if self.started_at is None:
            self.started_at = self._clock()
        if self._index >= len(self._samples):
            raise EndOfAudio
        end = min(self._index + self.blocksize, len(self._samples))
        if self.realtime:  # a block exists once all of it would have been captured
            ready_at = self.started_at + end / self.sample_rate
            wait = ready_at - self._clock()
            if wait > timeout:
                time.sleep(timeout)
                return None
            if wait > 0:
                time.sleep(wait)
        block = self._samples[self._index:end]
        self._index = end
        return block

    @property
    def duration_s(self) -> float:
        return len(self._samples) / self.sample_rate


class QueueSource:
    """Blocks pushed by a test or by a network handler later; ``close()`` ends the stream."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Block | None] = queue.Queue()
        self._closed = threading.Event()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def push(self, block: Block) -> None:
        self._queue.put(block)

    def close(self) -> None:
        self._queue.put(None)

    def read(self, timeout: float) -> Block | None:
        try:
            block = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if block is None:
            raise EndOfAudio
        return block
