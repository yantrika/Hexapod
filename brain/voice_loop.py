"""The voice loop: microphone to body, with self-hearing protection.

    AudioSource -> [STT thread: gate -> recognizer] -> utterance queue -> [worker thread: router,
    bridge, motion keeper heartbeats, spoken acknowledgement]

- Only FINAL results are routed. Exception: a stop word in a PARTIAL result sends ``stop`` at
  once (a false stop is safe and it saves the end-of-speech wait). Commands go through the
  bridge and the motion keeper exactly like ``scripts/brain_cli.py``; the voice side never
  touches the controller or any joint angle.
- Audio captured while hexa speaks is discarded (``SelfHearingGate``) and never fed to Vosk.
- After a routed command hexa says the pre-rendered "okay" (a placeholder until Step 9, which
  speaks from body statuses). Statuses are only printed by the front end.
- A source or recognizer error is logged and the loop keeps running.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import config
from brain.brain_loop import BrainLoop
from brain.router import RouteResult, route
from voice.audio import AudioSource, EndOfAudio
from voice.playback import Playback
from voice.stt import SelfHearingGate, SttEngine, SttEvent
from voice.tts import TtsError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceEvent:
    """Something a front end may want to print or measure."""

    kind: str  # "partial" | "final" | "route" | "error"
    text: str
    timestamp: float
    route: RouteResult | None = None
    early_stop: bool = False  # a "route" that came from a partial result


class VoiceLoop:
    def __init__(
        self,
        source: AudioSource,
        stt: SttEngine,
        brain: BrainLoop,
        speaking: threading.Event,
        playback: Playback | None = None,
        on_event: Callable[[VoiceEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.stt = stt
        self.brain = brain
        self.playback = playback
        self.on_event = on_event
        self._clock = clock
        self._gate = SelfHearingGate(speaking)
        self._events: queue.Queue[SttEvent] = queue.Queue(maxsize=config.UTTERANCE_QUEUE_MAXSIZE)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stop_sent_early = False
        self.blocks_seen = 0  # every block read, fed or discarded
        self.blocks_discarded = 0
        self.errors = 0
        self.events_published = 0  # STT results queued / handled by the worker (for tests)
        self.events_handled = 0
        self.stt_finished = threading.Event()  # a finite source ended and its results are queued
        self.idle = threading.Event()  # ... and the worker has handled all of them

    # -- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._stt_loop, name="voice-stt", daemon=True),
            threading.Thread(target=self._worker_loop, name="voice-worker", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def shutdown(self, timeout: float = 3.0) -> None:
        self._stop.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                logger.warning("%s did not end within %.1f s", thread.name, timeout)
        self._threads = []

    def wait_idle(self, timeout: float) -> bool:
        """For file sources: True once the whole file was recognised and its commands sent."""
        return self.idle.wait(timeout)

    # -- STT thread ----------------------------------------------------------------------------
    def _stt_loop(self) -> None:
        try:
            self.source.start()
        except Exception:  # noqa: BLE001
            logger.exception("could not start the audio source")
            self.errors += 1
            self.stt_finished.set()
            return
        try:
            while not self._stop.is_set():
                try:
                    block = self.source.read(config.STT_READ_TIMEOUT_S)
                except EndOfAudio:
                    self._flush()
                    break
                except Exception:  # noqa: BLE001 - a flaky device must not kill the loop
                    logger.exception("audio source error; carrying on")
                    self.errors += 1
                    self._stop.wait(config.STT_ERROR_BACKOFF_S)
                    continue
                if block is None:
                    continue
                self.blocks_seen += 1
                decision = self._gate.check()
                if not decision.accept:
                    self.blocks_discarded += 1  # hexa's own voice: never decoded
                    continue
                try:
                    if decision.reset:
                        self.stt.reset()  # nothing heard before the gap may leak through
                        self._publish(SttEvent("reset", "", self._clock()))
                    for event in self.stt.feed(block):
                        self._publish(event)
                except Exception:  # noqa: BLE001 - one bad block must not end recognition
                    logger.exception("speech recognition error; resetting the recognizer")
                    self.errors += 1
                    try:
                        self.stt.reset()
                    except Exception:  # noqa: BLE001
                        logger.exception("could not reset the recognizer")
                    self._stop.wait(config.STT_ERROR_BACKOFF_S)
        finally:
            self.source.stop()
            self.stt_finished.set()

    def _flush(self) -> None:
        flush = getattr(self.stt, "flush", None)
        if flush is not None:
            for event in flush():
                self._publish(event)

    def _publish(self, event: SttEvent) -> None:
        self.events_published += 1
        while True:
            try:
                self._events.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._events.get_nowait()  # the oldest result is the least useful
                except queue.Empty:
                    pass

    # -- worker thread -------------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event: SttEvent | None = self._events.get(timeout=config.VOICE_PUMP_S)
            except queue.Empty:
                event = None
            if event is not None:
                try:
                    self._handle(event)
                except Exception:  # noqa: BLE001
                    logger.exception("could not handle %r", event)
                    self.errors += 1
                self.events_handled += 1
            try:
                self.brain.pump()  # statuses to the keeper, due heartbeats to the body
            except Exception:  # noqa: BLE001
                logger.exception("brain pump failed")
                self.errors += 1
            if self.stt_finished.is_set() and self._events.empty():
                self.idle.set()

    def _emit(self, event: VoiceEvent) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001
                logger.exception("on_event callback failed")

    def _handle(self, event: SttEvent) -> None:
        if event.kind == "reset":  # a new utterance starts: an earlier early stop is history
            self._stop_sent_early = False
            return
        if event.kind == "partial":
            self._emit(VoiceEvent("partial", event.text, event.timestamp))
            if self._stop_sent_early:
                return
            result = route(event.text)
            if result.kind == "stop":  # only a stop word acts on a partial
                self._act(result, early_stop=True)
                self._stop_sent_early = True
            return
        self._emit(VoiceEvent("final", event.text, event.timestamp))
        already_stopped, self._stop_sent_early = self._stop_sent_early, False
        if not event.text:
            return
        result = route(event.text)
        if result.kind == "stop" and already_stopped:
            return  # the partial already stopped the robot; do not send it twice
        self._act(result, early_stop=False)

    def _act(self, result: RouteResult, early_stop: bool) -> None:
        self.brain.handle_route(result)  # a stop goes out first, before anything else
        self._emit(VoiceEvent("route", result.text, self._clock(), result, early_stop))
        if result.kind != "chat" and self.playback is not None:
            try:
                self.playback.say_phrase(config.VOICE_ACK_PHRASE)
            except TtsError as error:
                logger.error("could not acknowledge: %s", error)
