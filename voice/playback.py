"""Speech playback: a clearable queue, a synthesis worker with prefetch, and the speaking gate.

Two threads, joined by ``queue.Queue``s:

    say() -> text queue -> synthesis worker -> prefetch queue -> playback thread -> AudioSink

- Ordered, one utterance at a time. While sentence N plays, the worker already renders N+1
  (``TTS_PREFETCH_SIZE`` clips wait in the prefetch queue). Pre-rendered phrases skip synthesis.
- ``clear()`` is safe from any thread. It bumps a generation counter, empties both queues and
  aborts the sound in progress; anything still being synthesized is discarded when it finishes.
  It takes microseconds of CPU plus the sink's ``abort`` (bound: ``config.TTS_CLEAR_MAX_S``).
- ``speaking`` (a ``threading.Event``) is set from the start of a clip until it ends plus
  ``SPEAK_TAIL_S``. Gapless sentences keep it set throughout. Step 8 uses it to ignore STT.
- A synthesis or audio error is logged and skipped; the threads keep running.

Only this module touches the audio output: sounddevice is imported here, lazily, and tests use
a fake ``AudioSink`` with a fake clock.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import config
from body.clock import Clock, MonotonicClock
from voice.tts import AudioClip, TtsEngine, load_phrase, split_sentences

logger = logging.getLogger(__name__)


class AudioSink(Protocol):
    """Where audio goes. ``start`` begins playing without blocking; ``wait`` blocks until the
    clip has been played or ``abort`` was called; ``abort`` is thread-safe and instant."""

    def open(self) -> None: ...

    def start(self, clip: AudioClip) -> None: ...

    def wait(self) -> None: ...

    def abort(self) -> None: ...

    def close(self) -> None: ...


class SoundDeviceSink:
    """The speaker, through one output stream that stays open and plays silence when idle.

    Starting a clip is a buffer swap (no stream-open delay), and ``abort`` drops the clip and
    flushes the device buffer with ``stream.abort()``.
    """

    def __init__(self, device: int | None = config.SPEAKER_DEVICE, latency: str = "low") -> None:
        self.device = device
        self.latency = latency
        self._stream: Any = None
        self._rate = 0
        self._lock = threading.Lock()
        self._data: Any = None
        self._pos = 0
        self._done = threading.Event()
        self._done.set()

    def open(self, sample_rate: int = config.AUDIO_SAMPLE_RATE) -> None:
        import sounddevice  # only here: nothing else touches the audio output

        self._close_stream()
        self._stream = sounddevice.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=self.device,
            latency=self.latency,
            callback=self._callback,
        )
        self._rate = sample_rate
        self._stream.start()

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        with self._lock:
            data = self._data
            if data is None:
                outdata.fill(0)
                return
            chunk = data[self._pos : self._pos + frames]
            count = len(chunk)
            outdata[:count, 0] = chunk
            outdata[count:] = 0
            self._pos += count
            if self._pos >= len(data):
                self._data = None
                self._done.set()

    def start(self, clip: AudioClip) -> None:
        if self._stream is None or clip.sample_rate != self._rate:
            self.open(clip.sample_rate)
        with self._lock:
            self._data = clip.samples
            self._pos = 0
            self._done.clear()

    def wait(self) -> None:
        self._done.wait()

    def abort(self) -> None:
        with self._lock:
            self._data = None
            self._done.set()
        stream = self._stream
        if stream is not None:  # drop what the device already buffered, then keep the stream
            try:
                stream.abort()
                stream.start()
            except Exception:  # noqa: BLE001 - the sound is already stopped; just report
                logger.exception("could not flush the audio stream")

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:  # noqa: BLE001
                logger.exception("could not close the audio stream")

    def close(self) -> None:
        with self._lock:
            self._data = None
            self._done.set()
        self._close_stream()


@dataclass(frozen=True)
class Utterance:
    """One sentence (or pre-rendered phrase) to speak; ``group`` ties it to its say() call."""

    text: str
    group: int
    generation: int
    clip: AudioClip | None = None  # set for pre-rendered phrases: no synthesis wait


class _Stop:
    """Queue sentinel that ends a thread."""


_STOP = _Stop()


class Playback:
    """Clearable speech playback (see the module docstring)."""

    def __init__(
        self,
        engine: TtsEngine,
        sink: AudioSink,
        speaking: threading.Event | None = None,
        clock: Clock | None = None,
        tail_s: float = config.SPEAK_TAIL_S,
        prefetch: int = config.TTS_PREFETCH_SIZE,
        queue_size: int = config.TTS_QUEUE_MAXSIZE,
        on_start: Callable[[Utterance], None] | None = None,
        phrases_dir: Path | None = None,
    ) -> None:
        self.engine = engine
        self.sink = sink
        self.speaking = speaking if speaking is not None else threading.Event()
        self.clock = clock or MonotonicClock()
        self.tail_s = tail_s
        self.on_start = on_start
        self.phrases_dir = phrases_dir
        self._text_q: queue.Queue[Utterance | _Stop] = queue.Queue(maxsize=queue_size)
        self._audio_q: queue.Queue[tuple[Utterance, AudioClip] | _Stop] = queue.Queue(
            maxsize=max(1, prefetch)
        )
        self._lock = threading.Lock()  # generation, playing flag, speaking gate
        self._generation = 0
        self._playing = False
        self._tail_deadline: float | None = None
        self._stopping = False
        self._group = 0
        self._pending = 0  # utterances accepted and not yet played, dropped or failed
        self._idle = threading.Condition()
        self._threads: list[threading.Thread] = []
        self.errors = 0  # utterances that failed (synthesis or audio)

    # -- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._synthesis_loop, name="tts-synth", daemon=True),
            threading.Thread(target=self._playback_loop, name="tts-playback", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def shutdown(self, timeout: float = config.TTS_SHUTDOWN_TIMEOUT_S) -> None:
        """Stop speaking, end both threads, close the sink and the engine (kills Piper)."""
        self._stopping = True
        self.clear()
        for target in (self._text_q, self._audio_q):
            self._put_stop(target)
        self.engine.close()  # also kills a Piper that is mid-sentence, so the worker returns now
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                logger.warning("%s did not end within %.1f s", thread.name, timeout)
        self._threads = []
        self.sink.close()
        self.speaking.clear()

    def _put_stop(self, target: queue.Queue[Any]) -> None:
        while True:
            try:
                target.put_nowait(_STOP)
                return
            except queue.Full:
                self._drain(target)

    # -- input -------------------------------------------------------------------------------
    def say(self, text: str) -> int:
        """Queue *text*, one utterance per sentence. Returns the group id (0: nothing queued)."""
        sentences = split_sentences(text)
        if not sentences or self._stopping:
            return 0
        group = self._next_group()
        for sentence in sentences:
            if not self._enqueue(Utterance(sentence, group, self._generation)):
                break
        return group

    def say_phrase(self, name: str) -> int:
        """Queue a pre-rendered phrase (``config.TTS_PHRASES``): no synthesis wait."""
        clip = load_phrase(name, self.phrases_dir)  # clear error if unknown or not rendered
        group = self._next_group()
        self._enqueue(Utterance(config.TTS_PHRASES[name], group, self._generation, clip))
        return group

    def _next_group(self) -> int:
        with self._lock:
            self._group += 1
            return self._group

    def _enqueue(self, utterance: Utterance) -> bool:
        if self._stopping:
            return False
        with self._idle:
            self._pending += 1
        try:
            self._text_q.put_nowait(utterance)
        except queue.Full:
            logger.warning("tts queue full; dropping %r", utterance.text)
            self._finished(1)
            return False
        return True

    # -- clear -------------------------------------------------------------------------------
    def clear(self) -> None:
        """Cancel the sound in progress and drop everything queued. Any thread; idle is a no-op."""
        with self._lock:
            self._generation += 1
            dropped = self._drain(self._text_q) + self._drain(self._audio_q)
            if self._playing:
                self.sink.abort()
        if dropped:
            self._finished(dropped)

    def _drain(self, target: queue.Queue[Any]) -> int:
        dropped, stops = 0, 0
        while True:
            try:
                item = target.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, _Stop):
                stops += 1
            else:
                dropped += 1
        for _ in range(stops):  # a shutdown sentinel is never swallowed
            target.put_nowait(_STOP)
        return dropped

    # -- idle tracking ---------------------------------------------------------------------
    def _finished(self, count: int) -> None:
        with self._idle:
            self._pending = max(0, self._pending - count)
            if self._pending == 0:
                self._idle.notify_all()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until everything queued has been played, dropped or failed."""
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    @property
    def pending(self) -> int:
        return self._pending

    # -- synthesis worker --------------------------------------------------------------------
    def _synthesis_loop(self) -> None:
        while True:
            item = self._text_q.get()
            if isinstance(item, _Stop):
                return
            if item.generation != self._generation:
                self._finished(1)
                continue
            clip = item.clip
            if clip is None:
                try:
                    clip = self.engine.synthesize(item.text)
                except Exception as error:  # noqa: BLE001 - one bad sentence must not end the thread
                    self.errors += 1
                    logger.error("speech synthesis failed for %r: %s", item.text, error)
                    self._finished(1)
                    continue
            self._hand_over(item, clip)

    def _hand_over(self, item: Utterance, clip: AudioClip) -> None:
        """Wait for room in the prefetch queue (the wait ends early if the item is cleared)."""
        while not self._stopping:
            if item.generation != self._generation:
                break
            try:
                self._audio_q.put((item, clip), timeout=0.05)
                return
            except queue.Full:
                continue
        self._finished(1)

    # -- playback thread ---------------------------------------------------------------------
    def _playback_loop(self) -> None:
        try:
            self.sink.open()
        except Exception as error:  # noqa: BLE001 - retried by the first start()
            logger.error("audio output not available yet: %s", error)
        while True:
            entry = self._next_entry()
            if isinstance(entry, _Stop):
                return
            item, clip = entry
            self._play(item, clip)

    def _next_entry(self) -> tuple[Utterance, AudioClip] | _Stop:
        """The next clip; while the speaking tail runs, also ends the tail when it is over."""
        while True:
            timeout: float | None = None
            with self._lock:
                if self._tail_deadline is not None:
                    if self.clock.now() >= self._tail_deadline:
                        self._tail_deadline = None
                        self.speaking.clear()
                    else:
                        timeout = config.TTS_POLL_S
            try:
                return self._audio_q.get(timeout=timeout)
            except queue.Empty:
                continue

    def _play(self, item: Utterance, clip: AudioClip) -> None:
        with self._lock:
            if item.generation != self._generation or self._stopping:
                self._finished(1)
                return
            self.speaking.set()
            self._tail_deadline = None
            try:
                self.sink.start(clip)
                self._playing = True
            except Exception as error:  # noqa: BLE001 - a bad audio device must not end the thread
                self.errors += 1
                logger.error("audio output failed for %r: %s", item.text, error)
                self._begin_tail()
                self._finished(1)
                return
        if self.on_start is not None:
            try:
                self.on_start(item)
            except Exception:  # noqa: BLE001
                logger.exception("on_start callback failed")
        try:
            self.sink.wait()
        except Exception as error:  # noqa: BLE001
            self.errors += 1
            logger.error("audio output failed for %r: %s", item.text, error)
        with self._lock:
            self._playing = False
            self._begin_tail()
        self._finished(1)

    def _begin_tail(self) -> None:
        """Called with the lock held when audio stops: speaking ends after the tail."""
        if self.tail_s <= 0:
            self.speaking.clear()
        else:
            self._tail_deadline = self.clock.now() + self.tail_s
