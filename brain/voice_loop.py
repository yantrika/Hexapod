"""The voice loop: microphone to body, with self-hearing protection.

    AudioSource -> [STT thread: gate -> recognizer] -> utterance queue -> [worker thread: router,
    bridge, motion keeper heartbeats, spoken acknowledgement]

- Only FINAL results are routed. Exception: a stop word in a PARTIAL result sends ``stop`` at
  once (a false stop is safe and it saves the end-of-speech wait). For a final, ``decide()``
  (``brain/stt_decision.py``) chooses between the free-text result and the command-grammar
  result; every final is appended to the transcript log with both texts and confidences.
  Commands go through the bridge and the motion keeper exactly like
  ``scripts/brain_cli.py``; the voice side never touches the controller or any joint angle.
- Audio captured while hexa speaks is discarded (``SelfHearingGate``) and never fed to Vosk.
- The voice loop itself says nothing about commands: speech after a command comes from the body's
  STATUS (``brain/dialogue.py``). A chat sentence goes to the LLM (``brain/chat.py``) only while
  the body is idle (no walk, turn or transition); otherwise hexa says "tell me after I stop" and
  the LLM is not called. Any motion command or stop cancels a chat reply in progress, so the
  microphone is free for "stop". The LLM is never in the command path: the router decides.
- Listening mode (``config.LISTEN_MODE``): with a ``PushToTalk`` the recognizers are fed ONLY
  between press and release (plus a short tail); other blocks are read and dropped, so Vosk spends
  no CPU, and the end of the utterance flushes the recognizer. A press is barge-in: hexa is
  silenced at once (``playback.clear(skip_tail=True)``) and the chat reply is cancelled. Without
  one ("always") every block is fed, as in Step 8. In ptt mode a voice "stop" works only while
  listening; the control window STOP button and Space are the always-available stop.
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
from brain.chat import ChatResponder
from brain.router import RouteResult, route
from brain.stt_decision import PATH_EARLY_STOP, decide
from brain.transcript_log import TranscriptLog, hypothesis_record
from voice.audio import AudioSource, EndOfAudio
from voice.playback import Playback
from voice.ptt import PushToTalk
from voice.stt import Hypothesis, SelfHearingGate, SttEngine, SttEvent
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
    path: str = ""  # which rule decided a final / route (see brain/stt_decision.py)
    reason: str = ""
    free: Hypothesis | None = None  # a "final": both recognizers' results
    grammar: Hypothesis | None = None


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
        transcripts: TranscriptLog | None = None,
        chat: ChatResponder | None = None,
        ptt: PushToTalk | None = None,
    ) -> None:
        self.source = source
        self.stt = stt
        self.brain = brain
        self.playback = playback
        self.on_event = on_event
        self.transcripts = transcripts
        self.chat = chat
        self.ptt = ptt
        if ptt is not None:
            ptt.add_press_listener(self.barge_in)
        self._last_busy_notice = float("-inf")
        self._clock = clock
        self._gate = SelfHearingGate(speaking)
        self._events: queue.Queue[SttEvent] = queue.Queue(maxsize=config.UTTERANCE_QUEUE_MAXSIZE)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stop_sent_early = False
        self.blocks_seen = 0  # every block read, fed or discarded
        self.blocks_discarded = 0
        self.blocks_idle = 0  # dropped because push-to-talk was not listening
        self.barge_ins = 0
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

    def barge_in(self) -> None:
        """The user wants the floor: cancel the chat reply, silence hexa, open the microphone.
        Any thread (it runs on the thread that pressed). A cancelled reply is not remembered."""
        self.barge_ins += 1
        if self.chat is not None:
            self.chat.cancel()  # first, so no sentence of the old reply is queued after the clear
        if self.playback is not None:
            self.playback.clear(skip_tail=True)

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
                if block is not None:
                    self.blocks_seen += 1
                if self.ptt is not None and not self._ptt_gate(block):
                    continue
                if block is None:
                    continue
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

    def _ptt_gate(self, block: object) -> bool:
        """Push-to-talk bookkeeping for one read (a block or a timeout). True: process the block."""
        assert self.ptt is not None
        poll = self.ptt.poll()
        try:
            if poll.finished:  # released and the tail is over: end the utterance now
                self._flush()
                self.stt.reset()
            if poll.started:  # a fresh utterance: nothing heard before it may leak in
                self._gate.forget()
                self.stt.reset()
                self._publish(SttEvent("reset", "", self._clock()))
        except Exception:  # noqa: BLE001
            logger.exception("push-to-talk could not reset the recognizer")
            self.errors += 1
        if not poll.feed:
            if block is not None:
                self.blocks_idle += 1  # nobody is listening: never decoded, no Vosk CPU
            return False
        return block is not None

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
                if self.chat is not None and not self.brain.body_idle():
                    self.chat.cancel()  # motion from any source silences chat: keep the mic free
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
                self._act(result, PATH_EARLY_STOP, "stop word in a partial result", True)
                self._stop_sent_early = True
            return
        self._emit(VoiceEvent("final", event.text, event.timestamp, free=event.free,
                              grammar=event.grammar))
        already_stopped, self._stop_sent_early = self._stop_sent_early, False
        if not event.text and not (event.grammar and event.grammar.text):
            return
        decision = decide(event.text, event.grammar)
        result = route(decision.text)
        duplicate = result.kind == "stop" and already_stopped
        self._log_final(event, decision.path, decision.reason, result, duplicate)
        if duplicate:
            return  # the partial already stopped the robot; do not send it twice
        self._act(result, decision.path, decision.reason, False, event.timestamp)

    def _log_final(self, event: SttEvent, path: str, reason: str, result: RouteResult,
                   duplicate: bool) -> None:
        if self.transcripts is None:
            return
        self.transcripts.append({
            "kind": "final",
            "free": hypothesis_record(event.free) or {"text": event.text},
            "grammar": hypothesis_record(event.grammar),
            "path": path,
            "reason": reason,
            "route": {"kind": result.kind, "action": result.action, "phrase": result.phrase,
                      "score": round(result.score, 1)},
            "duplicate_of_early_stop": duplicate,
        })

    def _act(self, result: RouteResult, path: str, reason: str, early_stop: bool,
             started_at: float | None = None) -> None:
        if result.kind != "chat" and self.chat is not None:
            self.chat.cancel()  # a command or a stop silences any chat reply first
        self.brain.handle_route(result)  # a stop goes out first, before anything else
        self._emit(
            VoiceEvent("route", result.text, self._clock(), result, early_stop, path, reason)
        )
        if early_stop and self.transcripts is not None:
            self.transcripts.append({"kind": "early-stop", "free": {"text": result.text},
                                     "path": path, "reason": reason})
        if result.kind == "chat":
            self._chat(result.text, started_at)

    def _chat(self, text: str, started_at: float | None) -> None:
        """Chat goes to the LLM only while the body is idle (see the module docstring)."""
        if self.chat is None:
            return
        if not self.brain.body_idle():
            now = self._clock()
            if self.playback is not None and now - self._last_busy_notice >= (
                config.DIALOGUE_THROTTLE_S
            ):
                self._last_busy_notice = now
                try:
                    self.playback.say_phrase("tell_me_after_stop")
                except TtsError as error:
                    logger.error("could not say it: %s", error)
            return
        self.chat.submit(text, started_at)
