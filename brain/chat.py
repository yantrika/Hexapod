"""Chat: the LLM is ONLY for conversation and is never in the command path.

``ChatBackend`` is the interface (``stream(messages)`` yields text chunks, ``cancel()`` stops the
stream from any thread). ``OllamaChat`` talks to a local Ollama over HTTP; ``FakeChat`` is a
configurable stand-in for tests and for this slow dev laptop (the real model is too slow here).

``ChatResponder`` runs ONE reply at a time on its own thread: it streams the reply, hands each
sentence to the playback queue as soon as it is complete (``brain/sentences.py``), keeps a rolling
history, and can be cancelled at any moment (it closes the stream and calls ``playback.clear()``).
A cancelled reply is not added to the history. If the backend fails, the pre-rendered "I can't
think right now" is spoken and the loop carries on: chat never blocks the microphone or the body.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

import config
from brain.sentences import SentenceSplitter

logger = logging.getLogger(__name__)

Messages = list[dict[str, str]]


class ChatError(RuntimeError):
    """The chat backend could not answer (the message says why)."""


class ChatBackend(Protocol):
    """The chat interface. Nothing else in the project depends on a particular model."""

    def stream(self, messages: Messages) -> Iterator[str]:
        """Yield the reply as text chunks. Ends quietly if ``cancel()`` is called."""
        ...

    def cancel(self) -> None:
        """Stop the stream in progress. Safe to call from any thread, at any time."""
        ...


def _abort_response(response: requests.Response) -> None:
    """Close a streaming response even while another thread is blocked reading it."""
    try:  # closing a socket does not wake a blocked recv(); shutting it down does
        response.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


class OllamaChat:
    """Ollama's ``/api/chat`` over HTTP, streamed with ``requests`` (``stream=True``)."""

    def __init__(
        self,
        url: str = config.OLLAMA_URL,
        model: str = config.OLLAMA_MODEL,
        max_tokens: int = config.CHAT_MAX_TOKENS,
        temperature: float = config.CHAT_TEMPERATURE,
        keep_alive: str = config.OLLAMA_KEEP_ALIVE,
        timeout_s: float = config.OLLAMA_TIMEOUT_S,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._response: requests.Response | None = None
        self._cancelled = False

    @property
    def connection_open(self) -> bool:
        return self._response is not None

    def stream(self, messages: Messages) -> Iterator[str]:
        with self._lock:
            self._cancelled = False
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": self.max_tokens, "temperature": self.temperature},
        }
        try:
            response = requests.post(
                f"{self.url}/api/chat", json=body, stream=True,
                timeout=(self.timeout_s, self.timeout_s),
            )
        except requests.RequestException as error:
            raise ChatError(f"cannot reach Ollama at {self.url}: {error}") from error
        with self._lock:
            if self._cancelled:
                _abort_response(response)
                return
            self._response = response
        try:
            if response.status_code != 200:
                raise ChatError(f"Ollama answered {response.status_code}: {response.text[:200]}")
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError as error:
                    raise ChatError(f"Ollama sent a malformed line: {line[:80]!r}") from error
                if "error" in chunk:
                    raise ChatError(f"Ollama error: {chunk['error']}")
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break
        except (requests.RequestException, ValueError, OSError) as error:
            if self._cancelled:
                return  # we closed it ourselves
            raise ChatError(f"Ollama stream failed: {error}") from error
        finally:
            with self._lock:
                self._response = None
            _abort_response(response)

    def warm_up(self, timeout_s: float = 120.0) -> bool:
        """Load the model into memory without generating anything, so the first real reply does not
        pay the load (on the slow dev laptop a cold load can exceed ``OLLAMA_TIMEOUT_S``).
        Returns True if the model is loaded; never raises."""
        try:
            response = requests.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "keep_alive": self.keep_alive},
                timeout=timeout_s,
            )
            return response.status_code == 200
        except requests.RequestException as error:
            logger.warning("could not warm up %s: %s", self.model, error)
            return False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            response = self._response
        if response is not None:
            _abort_response(response)


class FakeChat:
    """A scripted backend: a reply streamed word by word with a configurable first-token delay and
    speed. Used by the tests and for developing on a machine too slow for the real model.

    ``tokens_per_s`` 0 means no delay between chunks. ``fail_after`` raises ``ChatError`` after
    that many chunks (0 = before the first). ``replies`` are used in turn, one per request.
    """

    DEFAULT_REPLIES = (
        "Hello! I am Hexa, a small six legged robot. I like to walk and wave.",
        "That is a good question. I think I should stand up and think about it.",
        "I am happy to chat. Ask me anything, or tell me to move.",
    )

    def __init__(
        self,
        replies: tuple[str, ...] = DEFAULT_REPLIES,
        first_token_s: float = 0.0,
        tokens_per_s: float = 0.0,
        fail_after: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.replies = replies
        self.first_token_s = first_token_s
        self.tokens_per_s = tokens_per_s
        self.fail_after = fail_after
        self._sleep = sleep
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0  # how many streams were started (a safety test counts these)
        self.yielded = 0  # chunks produced over all streams
        self.cancelled_calls = 0
        self.last_messages: Messages = []
        self.stream_open = False

    def stream(self, messages: Messages) -> Iterator[str]:
        with self._lock:
            self.calls += 1
            reply = self.replies[(self.calls - 1) % len(self.replies)]
            self.last_messages = [dict(m) for m in messages]
            self._cancel.clear()
            self.stream_open = True
        try:
            if self.fail_after == 0:
                raise ChatError("synthetic backend failure")
            if self._wait(self.first_token_s):
                return
            words = reply.split(" ")
            for index, word in enumerate(words):
                if self.fail_after is not None and index >= self.fail_after:
                    raise ChatError("synthetic backend failure")
                if index and self.tokens_per_s and self._wait(1.0 / self.tokens_per_s):
                    return
                if self._cancel.is_set():
                    self.cancelled_calls += 1
                    return
                self.yielded += 1
                yield word + (" " if index < len(words) - 1 else "")
        finally:
            self.stream_open = False

    def _wait(self, seconds: float) -> bool:
        """Sleep in small slices so cancel() is noticed; True if cancelled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._cancel.is_set():
                self.cancelled_calls += 1
                return True
            self._sleep(min(0.005, max(0.0, end - time.monotonic())))
        return False

    def cancel(self) -> None:
        self._cancel.set()


class ChatPlayback(Protocol):
    """What the responder needs from ``voice/playback.py``."""

    def say(self, text: str) -> int: ...

    def say_phrase(self, name: str) -> int: ...

    def clear(self) -> None: ...


@dataclass
class ChatTiming:
    """Monotonic times of one reply (None = did not happen). ``started_at`` is when the user's
    utterance was recognised."""

    started_at: float
    first_token: float | None = None
    first_sentence: float | None = None
    first_audio: float | None = None
    sentences: int = 0
    failed: bool = False
    cancelled: bool = False

    def seconds(self, field_name: str) -> float | None:
        value = getattr(self, field_name)
        return None if value is None else value - self.started_at


@dataclass
class _Request:
    text: str
    timing: ChatTiming
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    first_group: int = 0
    announced: bool = False


class ChatResponder:
    """One reply at a time: stream, split into sentences, speak, remember (see the module doc)."""

    def __init__(
        self,
        backend: ChatBackend,
        playback: ChatPlayback,
        history_turns: int = config.CHAT_HISTORY_TURNS,
        system_prompt: str = config.CHAT_SYSTEM_PROMPT,
        clock: Callable[[], float] = time.monotonic,
        on_timing: Callable[[ChatTiming], None] | None = None,
        fallback_phrase: str = "cant_think",
    ) -> None:
        self.backend = backend
        self.playback = playback
        self.system_prompt = system_prompt
        self.fallback_phrase = fallback_phrase
        self.on_timing = on_timing
        self._clock = clock
        self._history: deque[tuple[str, str]] = deque(maxlen=max(0, history_turns))
        self._state = threading.Lock()  # guards _current and _thread
        self._current: _Request | None = None
        self._thread: threading.Thread | None = None

    # -- public --------------------------------------------------------------------------------
    @property
    def history(self) -> list[tuple[str, str]]:
        return list(self._history)

    @property
    def active(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def submit(self, text: str, started_at: float | None = None) -> None:
        """Answer *text*. Any reply in progress is cancelled first."""
        self.cancel()
        timing = ChatTiming(started_at if started_at is not None else self._clock())
        request = _Request(text, timing)
        with self._state:
            self._current = request
            self._thread = threading.Thread(
                target=self._run, args=(request,), name="chat-reply", daemon=True
            )
            self._thread.start()

    def cancel(self) -> None:
        """Stop the reply in progress: close the stream and clear its speech. Any thread.

        The stream usually ends long before the audio does, so the speech is cleared even if the
        stream is finished, as long as something of the reply is still queued or playing."""
        with self._state:
            request, thread = self._current, self._thread
            self._current = None  # a reply is cancelled once
        if request is None:
            return
        streaming = thread is not None and thread.is_alive()
        with request.lock:  # no sentence can be queued after this returns
            request.cancelled = True
            if streaming:
                request.timing.cancelled = True
                self.backend.cancel()
            if streaming or getattr(self.playback, "pending", 1) > 0:
                self.playback.clear()
        if thread is not None and thread is not threading.current_thread():
            thread.join(2.0)

    def shutdown(self, timeout: float = 3.0) -> None:
        self.cancel()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def note_audio_start(self, group: int, at: float | None = None) -> None:
        """The playback started a sentence: if it is the reply's first, the first-audio time."""
        request = self._current
        if request is None or request.timing.first_audio is not None:
            return
        if group and group == request.first_group:
            request.timing.first_audio = at if at is not None else self._clock()
            self._announce(request)

    # -- the reply thread ----------------------------------------------------------------------
    def _messages(self, text: str) -> Messages:
        messages: Messages = [{"role": "system", "content": self.system_prompt}]
        for user, reply in self._history:
            messages += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
        messages.append({"role": "user", "content": text})
        return messages

    def _say(self, request: _Request, sentence: str) -> bool:
        with request.lock:
            if request.cancelled:
                return False
            group = self.playback.say(sentence)
            request.timing.sentences += 1
            if request.timing.first_sentence is None:
                request.timing.first_sentence = self._clock()
                request.first_group = group
        return True

    def _run(self, request: _Request) -> None:
        splitter = SentenceSplitter()
        reply: list[str] = []
        failed = False
        try:
            for chunk in self.backend.stream(self._messages(request.text)):
                if request.cancelled:
                    return
                if request.timing.first_token is None:
                    request.timing.first_token = self._clock()
                reply.append(chunk)
                for sentence in splitter.feed(chunk):
                    if not self._say(request, sentence):
                        return
            for sentence in splitter.flush():
                if not self._say(request, sentence):
                    return
        except Exception as error:  # noqa: BLE001 - chat must never take the loop down
            if request.cancelled:
                return
            logger.error("chat failed: %s", error)
            failed = True
        if request.cancelled:
            return
        text = "".join(reply).strip()
        if failed or not text:
            request.timing.failed = True
            with request.lock:
                if not request.cancelled:
                    try:
                        self.playback.say_phrase(self.fallback_phrase)
                    except Exception as error:  # noqa: BLE001
                        logger.error("could not speak the fallback: %s", error)
            self._announce(request)
            return
        self._history.append((request.text, text))
        if request.timing.first_audio is None and request.timing.first_sentence is None:
            self._announce(request)  # nothing speakable came out; report what we have

    def _announce(self, request: _Request) -> None:
        if request.announced or self.on_timing is None:
            return
        request.announced = True
        try:
            self.on_timing(request.timing)
        except Exception:  # noqa: BLE001
            logger.exception("on_timing callback failed")
