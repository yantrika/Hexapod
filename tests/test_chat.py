"""Step 9: chat backends and the reply streamer (no real LLM: a stub HTTP server and FakeChat)."""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import threading
import time
from collections.abc import Callable, Iterator

import pytest

import config
from brain.chat import ChatError, ChatResponder, ChatTiming, FakeChat, OllamaChat
from tests.fakes import StubPlayback, wait_until

LONG_REPLY = ("Hello! I am Hexa, a small friendly robot. I walk, sit, stand and wave. "
              "I like to chat with people. Ask me anything you like.")


# --- FakeChat ---------------------------------------------------------------------------------
def test_fake_chat_streams_the_reply_word_by_word() -> None:
    chat = FakeChat(("one two three",))
    assert list(chat.stream([{"role": "user", "content": "hi"}])) == ["one ", "two ", "three"]
    assert chat.calls == 1 and chat.last_messages[0]["content"] == "hi"


def test_fake_chat_first_token_latency_and_speed() -> None:
    chat = FakeChat(("a b c d",), first_token_s=0.1, tokens_per_s=100.0)
    started = time.perf_counter()
    stream = chat.stream([])
    next(stream)
    first = time.perf_counter() - started
    list(stream)
    total = time.perf_counter() - started
    assert 0.09 <= first < 0.3 and 0.12 <= total < 0.5  # 0.1 s + three gaps of 10 ms


def test_fake_chat_cancel_stops_the_stream() -> None:
    chat = FakeChat((" ".join(["w"] * 500),), tokens_per_s=200.0)
    stream = chat.stream([])
    got = [next(stream), next(stream)]
    chat.cancel()
    assert list(stream) == [] and len(got) == 2 and chat.yielded < 10


def test_fake_chat_failure_after_n_chunks() -> None:
    chat = FakeChat(("one two three four",), fail_after=2)
    seen = []
    with pytest.raises(ChatError):
        for chunk in chat.stream([]):
            seen.append(chunk)
    assert len(seen) == 2


# --- OllamaChat against a stub HTTP server ----------------------------------------------------
class StubOllama:
    """A tiny Ollama-like server. ``script`` decides what /api/chat does."""

    def __init__(self, script: Callable[[http.server.BaseHTTPRequestHandler], None]) -> None:
        outer = self
        self.requests: list[dict[str, object]] = []
        self.connections_closed = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(json.loads(self.rfile.read(length)))
                try:
                    script(self)
                except (BrokenPipeError, ConnectionResetError):
                    outer.connections_closed.set()

            def log_message(self, *args: object) -> None:
                pass

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def ndjson(handler: http.server.BaseHTTPRequestHandler, *chunks: dict[str, object],
           delay: float = 0.0) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    for chunk in chunks:
        data = (json.dumps(chunk) + "\n").encode()
        handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        handler.wfile.flush()
        time.sleep(delay)
    handler.wfile.write(b"0\r\n\r\n")


def piece(text: str, done: bool = False) -> dict[str, object]:
    return {"message": {"role": "assistant", "content": text}, "done": done}


@pytest.fixture
def servers() -> Iterator[list[StubOllama]]:
    made: list[StubOllama] = []
    yield made
    for server in made:
        server.close()


def stub(servers: list[StubOllama], script: Callable[..., None]) -> StubOllama:
    servers.append(StubOllama(script))
    return servers[-1]


def test_ollama_streams_chunks_and_sends_the_configured_request(servers: list[StubOllama]) -> None:
    server = stub(servers, lambda h: ndjson(h, piece("Hello "), piece("there."), piece("", True)))
    chat = OllamaChat(server.url, model="m1", max_tokens=33, temperature=0.2, keep_alive="1m")
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert list(chat.stream(messages)) == ["Hello ", "there."]
    sent = server.requests[0]
    assert sent["model"] == "m1" and sent["messages"] == messages and sent["stream"] is True
    assert sent["keep_alive"] == "1m"
    assert sent["options"] == {"num_predict": 33, "temperature": 0.2}
    assert not chat.connection_open  # the connection is closed after a normal end


def test_ollama_defaults_come_from_config() -> None:
    chat = OllamaChat()
    assert (chat.url, chat.model, chat.max_tokens) == (
        config.OLLAMA_URL, config.OLLAMA_MODEL, config.CHAT_MAX_TOKENS)
    assert chat.temperature == config.CHAT_TEMPERATURE and chat.timeout_s == config.OLLAMA_TIMEOUT_S


def test_ollama_error_status_is_a_clear_chat_error(servers: list[StubOllama]) -> None:
    def not_found(handler: http.server.BaseHTTPRequestHandler) -> None:
        body = b'{"error":"model \\"nope\\" not found, try pulling it first"}'
        handler.send_response(404)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    chat = OllamaChat(stub(servers, not_found).url, model="nope")
    with pytest.raises(ChatError, match="404.*not found"):
        list(chat.stream([{"role": "user", "content": "hi"}]))
    assert not chat.connection_open


def test_ollama_error_inside_the_stream(servers: list[StubOllama]) -> None:
    chat = OllamaChat(stub(servers, lambda h: ndjson(h, piece("Hi "), {"error": "boom"})).url)
    seen = []
    with pytest.raises(ChatError, match="boom"):
        for text in chat.stream([]):
            seen.append(text)
    assert seen == ["Hi "]


def test_ollama_malformed_line_is_a_chat_error(servers: list[StubOllama]) -> None:
    def garbage(handler: http.server.BaseHTTPRequestHandler) -> None:
        body = b"this is not json\n"
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    with pytest.raises(ChatError, match="malformed"):
        list(OllamaChat(stub(servers, garbage).url).stream([]))


def test_ollama_down_is_a_clear_error_not_a_traceback() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(ChatError, match="cannot reach Ollama"):
        list(OllamaChat(f"http://127.0.0.1:{port}", timeout_s=1.0).stream([]))


def test_ollama_times_out_when_the_server_goes_silent(servers: list[StubOllama]) -> None:
    def silent(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        first = (json.dumps(piece("one ")) + "\n").encode()
        handler.wfile.write(f"{len(first):x}\r\n".encode() + first + b"\r\n")
        handler.wfile.flush()
        time.sleep(3)  # never finishes the stream

    chat = OllamaChat(stub(servers, silent).url, timeout_s=0.4)
    started = time.perf_counter()
    with pytest.raises(ChatError):
        list(chat.stream([]))
    assert time.perf_counter() - started < 2.5
    assert not chat.connection_open


def test_ollama_cancel_wakes_a_blocked_read_and_closes_the_connection(
    servers: list[StubOllama],
) -> None:
    def stalls(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        first = (json.dumps(piece("start ")) + "\n").encode()
        handler.wfile.write(f"{len(first):x}\r\n".encode() + first + b"\r\n")
        handler.wfile.flush()
        time.sleep(5)  # the model is "thinking"

    server = stub(servers, stalls)
    chat = OllamaChat(server.url, timeout_s=10.0)
    got: list[str] = []
    finished = threading.Event()

    def reader() -> None:
        got.extend(chat.stream([]))  # must end quietly when cancelled
        finished.set()

    threading.Thread(target=reader, daemon=True).start()
    wait_until(lambda: bool(got), what="the first chunk")
    started = time.perf_counter()
    chat.cancel()
    assert finished.wait(1.0), "cancel did not wake the blocked read"
    assert time.perf_counter() - started < 0.5 and got == ["start "]
    assert not chat.connection_open


def test_ollama_warm_up_loads_the_model_without_generating(servers: list[StubOllama]) -> None:
    def generate(handler: http.server.BaseHTTPRequestHandler) -> None:
        body = b'{"done":true}'
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    server = stub(servers, generate)
    chat = OllamaChat(server.url, model="m1", keep_alive="5m")
    assert chat.warm_up() is True
    assert server.requests == [{"model": "m1", "keep_alive": "5m"}]  # no prompt: nothing generated


def test_ollama_warm_up_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert OllamaChat(f"http://127.0.0.1:{port}").warm_up(timeout_s=1.0) is False
    assert "could not warm up" in caplog.text


def test_ollama_cancel_before_any_stream_is_harmless() -> None:
    OllamaChat().cancel()


# --- ChatResponder -----------------------------------------------------------------------------
class Rig:
    def __init__(self, backend: FakeChat, history_turns: int = 4) -> None:
        self.backend = backend
        self.playback = StubPlayback()
        self.timings: list[ChatTiming] = []
        self.responder = ChatResponder(
            backend, self.playback, history_turns=history_turns, on_timing=self.timings.append
        )

    def wait_done(self) -> None:
        wait_until(lambda: not self.responder.active, 5.0, "the reply thread to end")


@pytest.fixture
def make() -> Iterator[Callable[..., Rig]]:
    made: list[Rig] = []

    def build(backend: FakeChat | None = None, **args: int) -> Rig:
        made.append(Rig(backend or FakeChat((LONG_REPLY,)), **args))
        return made[-1]

    yield build
    for rig in made:
        rig.responder.shutdown()


def test_the_reply_is_spoken_sentence_by_sentence(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.responder.submit("who are you")
    rig.wait_done()
    assert rig.playback.sentences == [
        "Hello!", "I am Hexa, a small friendly robot.", "I walk, sit, stand and wave.",
        "I like to chat with people.", "Ask me anything you like."]
    assert rig.responder.history == [("who are you", LONG_REPLY)]


def test_the_first_sentence_reaches_playback_before_the_stream_ends(
    make: Callable[..., Rig],
) -> None:
    backend = FakeChat((LONG_REPLY,), tokens_per_s=300.0)
    rig = make(backend)
    rig.responder.submit("hi")
    wait_until(lambda: bool(rig.playback.said), 5.0, "the first sentence")
    assert backend.stream_open  # still generating: the speech did not wait for the whole reply
    rig.wait_done()
    assert len(rig.playback.said) == 5


def test_cancel_mid_stream_stops_generation_and_clears_playback_within_a_bound(
    make: Callable[..., Rig],
) -> None:
    backend = FakeChat((" ".join(["Word."] * 300),), tokens_per_s=100.0)
    rig = make(backend)
    rig.responder.submit("go on")
    wait_until(lambda: len(rig.playback.said) >= 2, 5.0, "some speech")
    started = time.perf_counter()
    rig.responder.cancel()
    elapsed = time.perf_counter() - started
    assert elapsed < config.TTS_CLEAR_MAX_S + 0.2
    assert len(rig.playback.clears) == 1 and not rig.responder.active
    spoken = len(rig.playback.said)
    produced = backend.yielded
    time.sleep(0.2)
    assert len(rig.playback.said) == spoken and backend.yielded == produced  # really stopped
    assert backend.cancelled_calls == 1


def test_a_cancelled_reply_is_not_added_to_the_history(make: Callable[..., Rig]) -> None:
    rig = make(FakeChat((" ".join(["Word."] * 300),), tokens_per_s=100.0))
    rig.responder.submit("go on")
    wait_until(lambda: bool(rig.playback.said), 5.0, "speech")
    rig.responder.cancel()
    assert rig.responder.history == []
    assert rig.timings == [] or rig.timings[0].cancelled


def test_nothing_is_spoken_after_cancel_returns(make: Callable[..., Rig]) -> None:
    for _ in range(20):  # the race: a sentence completing exactly while cancel() runs
        rig = make(FakeChat((" ".join(["Hi."] * 200),), tokens_per_s=2000.0))
        rig.responder.submit("go")
        wait_until(lambda r=rig: bool(r.playback.said), 5.0, "speech")  # type: ignore[misc]
        rig.responder.cancel()
        count = len(rig.playback.said)
        time.sleep(0.03)
        assert len(rig.playback.said) == count


def test_the_history_is_trimmed_to_n_turns_and_sent_with_the_system_prompt(
    make: Callable[..., Rig],
) -> None:
    backend = FakeChat(("Reply one.", "Reply two.", "Reply three."))
    rig = make(backend, history_turns=2)
    for text in ("first", "second", "third"):
        rig.responder.submit(text)
        rig.wait_done()
    assert [user for user, _ in rig.responder.history] == ["second", "third"]  # trimmed to 2
    roles = [(m["role"], m["content"]) for m in backend.last_messages]
    assert roles[0] == ("system", config.CHAT_SYSTEM_PROMPT)
    assert roles[1:] == [("user", "first"), ("assistant", "Reply one."),
                         ("user", "second"), ("assistant", "Reply two."), ("user", "third")]


def test_a_backend_error_speaks_the_fallback_and_the_loop_keeps_running(
    make: Callable[..., Rig], caplog: pytest.LogCaptureFixture
) -> None:
    backend = FakeChat(("Fine thanks.",), fail_after=0)
    rig = make(backend)
    with caplog.at_level("ERROR"):
        rig.responder.submit("hello")
        rig.wait_done()
    assert rig.playback.phrases == ["cant_think"] and rig.playback.sentences == []
    assert rig.responder.history == [] and "chat failed" in caplog.text
    assert rig.timings and rig.timings[0].failed
    backend.fail_after = None  # the backend recovers
    rig.responder.submit("hello again")
    rig.wait_done()
    assert rig.playback.sentences == ["Fine thanks."]


def test_a_failure_in_the_middle_still_speaks_what_came_before_then_the_fallback(
    make: Callable[..., Rig],
) -> None:
    rig = make(FakeChat(("One two. Three four five six seven.",), fail_after=3))
    rig.responder.submit("go")
    rig.wait_done()
    assert rig.playback.sentences == ["One two."] and rig.playback.phrases == ["cant_think"]
    assert rig.responder.history == []


def test_an_empty_reply_counts_as_a_failure(make: Callable[..., Rig]) -> None:
    rig = make(FakeChat(("",)))
    rig.responder.submit("hello")
    rig.wait_done()
    assert rig.playback.phrases == ["cant_think"]


def test_a_new_utterance_cancels_the_reply_in_progress(make: Callable[..., Rig]) -> None:
    backend = FakeChat((" ".join(["Word."] * 300), "Second reply."), tokens_per_s=100.0)
    rig = make(backend)
    rig.responder.submit("first")
    wait_until(lambda: bool(rig.playback.said), 5.0, "speech")
    rig.responder.submit("second")
    rig.wait_done()
    assert rig.playback.clears  # the first reply's speech was cleared
    assert rig.playback.sentences[-1] == "Second reply."
    assert rig.responder.history == [("second", "Second reply.")]


def test_timings_first_token_sentence_and_audio(make: Callable[..., Rig]) -> None:
    rig = make(FakeChat(("Hello there. More words here.",), first_token_s=0.05, tokens_per_s=200.0))
    started = time.monotonic()
    rig.responder.submit("hi", started_at=started)
    rig.wait_done()
    group = rig.responder._current.first_group  # type: ignore[union-attr]
    assert rig.timings == []  # not announced until the audio starts
    rig.responder.note_audio_start(group, at=time.monotonic())
    (timing,) = rig.timings
    first_token, first_sentence = timing.seconds("first_token"), timing.seconds("first_sentence")
    assert first_token is not None and first_sentence is not None
    assert 0.04 <= first_token <= first_sentence
    assert timing.seconds("first_audio") is not None and timing.sentences == 2


def test_shutdown_leaves_no_threads_and_no_open_connection() -> None:
    backend = FakeChat((" ".join(["Word."] * 300),), tokens_per_s=100.0)
    rig = Rig(backend)
    rig.responder.submit("go")
    wait_until(lambda: bool(rig.playback.said), 5.0, "speech")
    rig.responder.shutdown()
    assert not [t for t in threading.enumerate() if t.name == "chat-reply"]
    assert not backend.stream_open


def test_cancel_when_nothing_was_asked_is_a_no_op(make: Callable[..., Rig]) -> None:
    rig = make()
    rig.responder.cancel()
    assert rig.playback.clears == []


def test_cancel_stops_the_real_playback_within_the_clear_bound() -> None:
    """With the real Playback (fake sink and clock): the first sentence is playing when the
    reply is cancelled; the sink is aborted and nothing else is queued."""
    from tests.fakes import FakeClock, FakeEngine, FakeSink
    from voice.playback import Playback

    clock = FakeClock()  # never advanced: a sentence "plays" until it is aborted
    sink = FakeSink(clock)
    playback = Playback(FakeEngine(clock), sink, clock=clock)
    playback.start()
    responder = ChatResponder(FakeChat((LONG_REPLY,), tokens_per_s=500.0), playback)
    try:
        responder.submit("hi")
        wait_until(lambda: bool(sink.plays), 5.0, "the first sentence to start playing")
        started = time.perf_counter()
        responder.cancel()
        assert time.perf_counter() - started < config.TTS_CLEAR_MAX_S + 0.2
        wait_until(lambda: sink.plays[0]["end"] >= 0, 2.0, "the sound to stop")
        assert sink.plays[0]["aborted"] == 1.0 and playback.wait_idle(2.0)
        assert len(sink.plays) == 1 and responder.history == []
    finally:
        responder.shutdown()
        playback.shutdown()
