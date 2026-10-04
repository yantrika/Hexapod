"""The whole robot as one object: body process, status hub, voice loop, dialogue, chat, speech.

``main.py`` builds a ``HexaApp`` from the command-line flags; tests build one with fakes. The
app owns the shutdown order (stop the robot, silence it, cancel chat, join the threads, end the
body process last) so a signal or an error never leaves an orphan behind.

No TTY is assumed. Push-to-talk is driven through ``set_listening()`` / ``toggle_listening()``,
which a terminal key thread (``main.py``) or, in Step 12, the phone page calls.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import config
from body.process import BodyProcess
from brain.brain_loop import BrainLoop
from brain.chat import ChatBackend, ChatResponder, FakeChat, OllamaChat
from brain.dialogue import Dialogue
from brain.status_hub import StatusHub
from brain.voice_loop import VoiceEvent, VoiceLoop
from bridge import make_bridge, new_command
from commandline import format_status
from voice.audio import AudioSource, FileSource, MicSource, QueueSource
from voice.playback import Playback, SoundDeviceSink
from voice.ptt import PushToTalk, make_ptt
from voice.stt import SttEngine, VoskStt
from voice.tts import PiperEngine

logger = logging.getLogger(__name__)


class StartupError(RuntimeError):
    """Something needed to start is missing or failed (the message says what to do)."""


@dataclass
class AppOptions:
    gui: bool = False
    listen: str = config.LISTEN_MODE  # "ptt" | "always"
    chat: str = "ollama"  # "ollama" | "fake" | "off"
    no_speak: bool = False
    no_mic: bool = False  # no microphone: audio comes from set_source/the phone page or a file
    audio_file: Path | None = None  # recognise this WAV in real time, then finish (demos, tests)
    model: str | None = None
    ollama_model: str | None = None
    mic_device: int | None = config.MIC_DEVICE


class LoggedPlayback:
    """What the dialogue, the chat and the voice loop speak through: every phrase is logged, and
    handed to the real ``Playback`` unless speech is off (``--no-speak``)."""

    def __init__(self, inner: Playback | None) -> None:
        self.inner = inner

    @property
    def pending(self) -> int:
        return self.inner.pending if self.inner is not None else 0

    def say(self, text: str) -> int:
        logger.info("hexa says: %s", text)
        return self.inner.say(text) if self.inner is not None else 0

    def say_phrase(self, name: str) -> int:
        logger.info("hexa says: %s", config.TTS_PHRASES.get(name, name))
        return self.inner.say_phrase(name) if self.inner is not None else 0

    def clear(self, skip_tail: bool = False) -> None:
        if self.inner is not None:
            self.inner.clear(skip_tail)


def make_chat_backend(kind: str, model: str | None) -> ChatBackend | None:
    if kind == "off":
        return None
    if kind == "fake":
        return FakeChat(first_token_s=0.3, tokens_per_s=8.0)
    backend = OllamaChat(model=model or config.OLLAMA_MODEL)
    threading.Thread(target=backend.warm_up, name="chat-warmup", daemon=True).start()
    return backend


class HexaApp:
    def __init__(
        self,
        options: AppOptions,
        *,
        stt: SttEngine | None = None,
        source: AudioSource | None = None,
        chat_backend: ChatBackend | None = None,
        playback: Playback | None = None,
    ) -> None:
        self.options = options
        self._stt = stt
        self._source = source
        self._chat_backend = chat_backend
        self._playback_inner = playback
        self._own_playback = playback is None
        self.speaking = threading.Event()
        self.bridge = make_bridge()
        self.body: BodyProcess | None = None
        self.hub: StatusHub | None = None
        self.brain: BrainLoop | None = None
        self.dialogue: Dialogue | None = None
        self.chat: ChatResponder | None = None
        self.voice: VoiceLoop | None = None
        self.ptt: PushToTalk | None = None
        self.source: AudioSource | None = None
        self.speech = LoggedPlayback(None)
        self._engine: PiperEngine | None = None
        self._log_stop = threading.Event()
        self._log_thread: threading.Thread | None = None
        self._started = False

    # -- start ---------------------------------------------------------------------------------
    def start(self) -> None:
        """Bring everything up; raises ``StartupError`` (after cleaning up) if it cannot."""
        try:
            self._start()
        except StartupError:
            self.shutdown()
            raise
        except Exception as error:
            self.shutdown()
            raise StartupError(f"could not start: {error}") from error
        self._started = True

    def _start(self) -> None:
        options = self.options
        if self._stt is None:
            from voice.stt import SttError

            try:
                self._stt = VoskStt(options.model)
            except SttError as error:
                raise StartupError(str(error)) from error
        if not options.no_speak and self._playback_inner is None:
            from voice.tts import TtsError

            engine = PiperEngine()
            try:
                engine.check_installed()
                engine.start()  # pay Piper's model load now, not at the first answer
            except TtsError as error:
                raise StartupError(f"{error} (or use --no-speak)") from error
            self._engine = engine
            self._playback_inner = Playback(engine, SoundDeviceSink(), speaking=self.speaking)
        if self._playback_inner is not None:
            if not self._own_playback:
                self.speaking = self._playback_inner.speaking
            self._playback_inner.start()
        self.speech = LoggedPlayback(self._playback_inner)

        self.body = BodyProcess(self.bridge, headless=not options.gui)
        self.body.start()
        logger.info("body process pid %s (%s)", self.body.pid, "gui" if options.gui else "headless")
        if not self.body.wait_ready():
            raise StartupError("the body process did not start (see the log above)")
        self.hub = StatusHub(self.bridge)
        self.hub.start()
        self.brain = BrainLoop(self.bridge, hub=self.hub)
        self.dialogue = Dialogue(self.hub.subscribe("dialogue"), self.speech)
        self.brain.add_sent_listener(self.dialogue.note_sent)
        self.dialogue.start()
        self._log_thread = threading.Thread(target=self._log_statuses, name="status-log",
                                            daemon=True)
        self._log_thread.start()

        backend = self._chat_backend or make_chat_backend(options.chat, options.ollama_model)
        if backend is not None:
            self.chat = ChatResponder(backend, self.speech)
            if self._playback_inner is not None:
                chat = self.chat
                self._playback_inner.on_start = lambda utterance: chat.note_audio_start(
                    utterance.group)

        listen = options.listen
        if options.audio_file is not None and listen == "ptt":
            logger.info("--audio-file: listening is 'always' (nobody can press a button)")
            listen = "always"
        self.ptt = make_ptt(listen)
        self.source = self._make_source()
        assert self._stt is not None
        self.voice = VoiceLoop(self.source, self._stt, self.brain, self.speaking,
                               self.speech, self._on_voice_event,  # type: ignore[arg-type]
                               chat=self.chat, ptt=self.ptt)
        self.voice.start()
        logger.info("hexa is ready (listen=%s, chat=%s, speech=%s)", listen, options.chat,
                    "off" if options.no_speak else "on")

    def _make_source(self) -> AudioSource:
        if self._source is not None:
            return self._source
        if self.options.audio_file is not None:
            return FileSource(self.options.audio_file, realtime=True, pad_silence_s=2.0)
        if self.options.no_mic:
            return QueueSource()  # the phone page pushes audio here (Step 12)
        return MicSource(device=self.options.mic_device)

    # -- logging -------------------------------------------------------------------------------
    def _log_statuses(self) -> None:
        assert self.hub is not None
        subscription = self.hub.subscribe("status-log")
        while not self._log_stop.is_set():
            status = subscription.get(timeout=0.1)
            if status is not None:
                logger.info("status %s", format_status(status))
        self.hub.unsubscribe(subscription)

    @staticmethod
    def _on_voice_event(event: VoiceEvent) -> None:
        if event.kind == "final":
            logger.info("heard %r", event.text)
        elif event.kind == "route" and event.route is not None:
            route = event.route
            logger.info("route %s action=%s text=%r%s", route.kind, route.action, route.text,
                        " (early stop)" if event.early_stop else "")

    # -- running -------------------------------------------------------------------------------
    def set_listening(self, on: bool) -> None:
        """Push-to-talk from any thread (a key, the phone page). No-op in 'always' mode."""
        if self.ptt is None:
            return
        if on:
            self.ptt.press()
        else:
            self.ptt.release()

    def toggle_listening(self) -> bool:
        return self.ptt.toggle() if self.ptt is not None else True

    def send_stop(self) -> None:
        self.bridge.send(new_command("stop"))

    @property
    def finished(self) -> bool:
        """True once a file source has been fully recognised and everything it caused is over."""
        voice, brain, chat = self.voice, self.brain, self.chat
        if voice is None or brain is None or not voice.idle.is_set():
            return False
        return brain.body_idle() and not (chat is not None and chat.active) and (
            self.speech.pending == 0)

    def wait(self, stop: threading.Event, poll_s: float = 0.2) -> None:
        """Block until *stop* is set, or (file source only) the audio is done and handled."""
        is_file = self.options.audio_file is not None or isinstance(self._source, FileSource)
        settled = 0
        while not stop.is_set():
            if is_file and self.finished:
                settled += 1
                if settled >= 3:  # stable for a moment: late statuses have been spoken
                    return
            else:
                settled = 0
            if self.body is not None and not self.body.alive:
                raise RuntimeError("the body process died")
            stop.wait(poll_s)

    def body_alive(self) -> bool:
        return self.body is not None and self.body.alive

    # -- shutdown ------------------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop the robot, silence it, cancel chat, join the threads, end the body. Idempotent."""
        steps = [
            ("stop the robot", self.send_stop if self.body is not None else None),
            ("voice loop", self.voice.shutdown if self.voice else None),
            ("chat", self.chat.shutdown if self.chat else None),
            ("playback clear", self.speech.clear),
            ("dialogue", self.dialogue.shutdown if self.dialogue else None),
            ("playback", self._playback_inner.shutdown
             if self._playback_inner and self._own_playback else None),
            ("engine", self._engine.close if self._engine else None),
            ("brain", self.brain.close if self.brain else None),
            ("status log", self._stop_log),
            ("status hub", self.hub.stop if self.hub else None),
            ("body process", self.body.shutdown if self.body else None),
            ("bridge", self.bridge.close),
        ]
        for name, step in steps:
            if step is None:
                continue
            try:
                step()
            except Exception:  # noqa: BLE001 - one failing step must not skip the rest
                logger.exception("shutdown: %s failed", name)
        self.voice = self.chat = self.dialogue = self.brain = self.hub = self.body = None
        self._playback_inner = None
        self._engine = None
        if self._started:
            logger.info("hexa stopped")
        self._started = False

    def _stop_log(self) -> None:
        self._log_stop.set()
        thread, self._log_thread = self._log_thread, None
        if thread is not None:
            thread.join(2.0)

