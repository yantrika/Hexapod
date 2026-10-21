"""The phone page server (Step 12a): one static page and one WebSocket on one port.

Runs in the brain process on its own thread and event loop. It talks to the body ONLY through the
``Bridge`` (``bridge.send(new_command(...))``) and reads statuses from a ``StatusHub``
subscription, exactly like ``scripts/control_window.py``; it never imports the controller, the
gait or a backend (``tests/test_web_server.py`` enforces it).

``websockets`` answers plain HTTP too (``process_request``), so no second web framework is needed.
The WebSocket handshake carries the PIN (``/ws?pin=...``) and is refused with 401 (wrong PIN),
429 (locked out), 403 (foreign Origin) or 409 (someone else is in control). Plain HTTP on a LAN:
use it only on a network you trust. Step 12b adds hold-to-talk with the ROBOT's microphone (no
audio ever comes from the page, so no HTTPS) and typed text; brain events (heard, route, said,
listening) reach the page through a bounded drop-oldest ``EventHub`` subscription.

Step 12c: with an ``ssl.SSLContext`` the same port serves HTTPS and ``wss`` (the browser only
opens the microphone on a secure page), and the page may send BINARY audio frames while it holds
the talk button; they go to ``WebControl.audio_frame`` (controller only, press held, rate limited).

Step 12c-2: with ``voice_out`` the page may ask (``voice`` on) for Arya's voice. ``send_audio`` and
``clear_audio`` (any thread, never blocking) then put one binary message per sentence, or an
``audio_clear``, on the same outbox as everything else, so their order is kept. A slow phone loses
sentences (``WEB_VOICE_MAX_CLIPS`` wait at most); it never delays the body or the robot's speech.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import secrets
import socket
import ssl
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

import config
from brain.event_hub import EventHub
from brain.status_hub import StatusHub
from bridge import Bridge, Status, new_command
from scripts.control_logic import next_state_label
from voice.tts import AudioClip
from web.protocol import ProtocolError, encode_voice_clip, parse_audio_frame, parse_message
from web.session import NO_VOICE, PinGuard, Refused, VoiceControls, WebControl

logger = logging.getLogger(__name__)

PAGE_PATH = Path(__file__).resolve().parent / "static" / "index.html"
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; img-src 'self' data:")
_OUTBOX_SIZE = 32


def resolve_pin(configured: str | None, environment: dict[str, str]) -> tuple[str, bool]:
    """The PIN to use and whether it was generated: ``HEXA_WEB_PIN``, else ``config.WEB_PIN``,
    else a random 6-digit PIN."""
    pin = environment.get("HEXA_WEB_PIN") or configured
    if pin:
        return str(pin), False
    return f"{secrets.randbelow(1_000_000):06d}", True


def lan_addresses() -> list[str]:
    """This machine's non-loopback IPv4 addresses (no packet is sent)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # a route lookup only
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    unique: list[str] = []
    for address in found:
        if not address.startswith("127.") and address not in unique:
            unique.append(address)
    return unique


def make_ssl_context(cert: Path, key: Path) -> ssl.SSLContext:
    """A server TLS context from a certificate and its key (``scripts/make_cert.py`` makes them).
    Raises ``OSError`` with a plain message if either file is missing or unreadable."""
    for path in (cert, key):
        if not path.is_file():
            raise OSError(f"{path} not found: run `python scripts/make_cert.py` first")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(cert, key)
    except (ssl.SSLError, OSError) as error:
        raise OSError(f"could not load the certificate {cert}: {error}") from error
    return context


def _response(status: HTTPStatus, body: bytes, content_type: str = "text/plain; charset=utf-8",
              extra: dict[str, str] | None = None) -> Response:
    headers = Headers([("Content-Type", content_type), ("Content-Length", str(len(body))),
                       ("Connection", "close"), ("Cache-Control", "no-store"),
                       ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")])
    for key, value in (extra or {}).items():
        headers[key] = value
    return Response(status.value, status.phrase, headers, body)


class _Client:
    """One connected page: its outbound queue (a stalled phone never blocks the server loop)."""

    def __init__(self, connection: ServerConnection) -> None:
        self.connection = connection
        self.outbox: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=_OUTBOX_SIZE)
        self.invalid = 0
        self.voice_on = False  # the page asked for Arya's voice
        self.audio_queued = 0  # voice clips waiting in the outbox (event loop thread only)

    def _put(self, item: str | bytes) -> None:
        while True:
            try:
                self.outbox.put_nowait(item)
                return
            except asyncio.QueueFull:
                if isinstance(self.outbox.get_nowait(), bytes):  # drop the oldest
                    self.audio_queued -= 1

    def post(self, message: dict[str, Any]) -> None:
        self._put(json.dumps(message, default=str))

    def post_audio(self, payload: bytes) -> bool:
        """Queue one sentence of voice; False (dropped) if the phone is already that far behind."""
        if self.audio_queued >= config.WEB_VOICE_MAX_CLIPS:
            return False
        self.audio_queued += 1
        self._put(payload)
        return True

    def clear_audio(self) -> None:
        """Drop the voice not yet sent, then tell the page to drop what it holds."""
        kept: list[str | bytes] = []
        while True:
            try:
                item = self.outbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(item, bytes):
                self.audio_queued -= 1
            else:
                kept.append(item)
        for item in kept:
            self.outbox.put_nowait(item)
        self.post({"type": "audio_clear"})


class WebServer:
    def __init__(
        self,
        bridge: Bridge,
        hub: StatusHub,
        pin: str,
        *,
        host: str = "127.0.0.1",
        port: int = config.WEB_PORT,
        clock: Callable[[], float] = time.monotonic,
        voice: VoiceControls = NO_VOICE,
        events: EventHub | None = None,
        ssl_context: ssl.SSLContext | None = None,
        voice_out: bool = False,
    ) -> None:
        self._voice_out = voice_out  # Arya's voice can go to the page (``--phone-voice``)
        self._bridge = bridge
        self._hub = hub
        self._voice = voice
        self._events = events if events is not None else EventHub()
        self._event_subscription = self._events.subscribe("web")
        self.host = host
        self._ssl = ssl_context
        self._port = port
        self._clock = clock
        self._page = PAGE_PATH.read_bytes()
        self._guard = PinGuard(pin, clock)
        self._control = WebControl(self._send_command, clock, voice=voice)
        self._subscription = hub.subscribe("web")
        self._sent: collections.OrderedDict[int, str] = collections.OrderedDict()
        self._label = "standing"
        self._client: _Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # --- lifecycle (any thread) ----------------------------------------------------------
    @property
    def port(self) -> int:
        return self._port

    @property
    def native_id(self) -> int | None:
        """The OS thread id of the server thread (measurement scripts read its CPU time)."""
        return self._thread.native_id if self._thread is not None else None

    def start(self, timeout: float = 5.0) -> None:
        """Start listening; raises ``OSError`` if the port cannot be bound."""
        if self._thread is not None:
            return
        logging.getLogger("websockets").setLevel(logging.WARNING)  # DEBUG would log the PIN
        self._thread = threading.Thread(target=self._run, name="web-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise OSError("the web server did not start in time")
        if self._error is not None:
            self._thread.join(1.0)
            self._thread = None
            raise OSError(f"web server could not listen on {self.host}:{self._port}: "
                          f"{self._error}") from self._error

    def stop(self, timeout: float = 5.0) -> None:
        """Close every connection (each sends ``stop``) and join the thread. Idempotent."""
        thread, self._thread = self._thread, None
        loop, stopping = self._loop, self._stopping
        if loop is not None and stopping is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stopping.set)
            except RuntimeError:  # the loop closed between the check and the call
                pass
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("web server did not stop within %.1f s", timeout)
        self._hub.unsubscribe(self._subscription)
        self._events.unsubscribe(self._event_subscription)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as error:  # noqa: BLE001 - reported to start() or logged
            if not self._ready.is_set():
                self._error = error
                self._ready.set()
            else:
                logger.exception("web server crashed")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = asyncio.Event()
        server: Server = await serve(
            self._handler, self.host, self._port, process_request=self._process_request,
            ssl=self._ssl, compression=None,
            max_size=max(config.WEB_MAX_MESSAGE_BYTES, config.WEB_AUDIO_MAX_FRAME_BYTES),
            ping_interval=config.WEB_PING_S, ping_timeout=config.WEB_PING_S,
            close_timeout=config.WEB_CLOSE_S)
        self._port = server.sockets[0].getsockname()[1]
        pump = asyncio.create_task(self._pump())
        self._ready.set()
        logger.info("web page on %s://%s:%d", "https" if self._ssl else "http", self.host,
                    self._port)
        try:
            await self._stopping.wait()
        finally:
            pump.cancel()
            self._send_command("stop", {})  # first, not after the closes: the robot never
            self._control.release_listening()  # and listening ends with the page
            server.close(close_connections=True)  # outlives the page (each handler stops too)
            await server.wait_closed()
            logger.info("web server stopped")

    # --- HTTP and the handshake ----------------------------------------------------------
    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        url = urlsplit(request.path)
        if url.path == "/ws":
            return self._check_handshake(connection, request, url.query)
        if url.path in ("/", "/index.html"):
            return _response(HTTPStatus.OK, self._page, "text/html; charset=utf-8",
                             {"Content-Security-Policy": _CSP})
        if url.path == "/favicon.ico":
            return _response(HTTPStatus.NO_CONTENT, b"")
        return _response(HTTPStatus.NOT_FOUND, b"not found\n")

    def _check_handshake(
        self, connection: ServerConnection, request: Request, query: str
    ) -> Response | None:
        if "websocket" not in request.headers.get("Upgrade", "").lower():
            return _response(HTTPStatus.UPGRADE_REQUIRED, b"WebSocket only\n",
                             extra={"Upgrade": "websocket"})
        origin, host = request.headers.get("Origin"), request.headers.get("Host", "")
        if origin is not None and urlsplit(origin).netloc.lower() != host.lower():
            logger.warning("web: refused a foreign Origin %r", origin)
            return _response(HTTPStatus.FORBIDDEN, b"foreign origin\n")
        remote = connection.remote_address
        address = str(remote[0]) if remote else "?"
        pin = (parse_qs(query).get("pin") or [""])[0]
        verdict = self._guard.check(address, pin)
        if verdict == "locked":
            logger.warning("web: %s is locked out", address)
            return _response(HTTPStatus.TOO_MANY_REQUESTS, b"too many wrong PINs\n")
        if verdict != "ok":
            logger.warning("web: wrong PIN from %s", address)
            return _response(HTTPStatus.UNAUTHORIZED, b"wrong PIN\n")
        if self._control.controller is not None:
            logger.info("web: %s refused, another phone is in control", address)
            return _response(HTTPStatus.CONFLICT, b"another controller is connected\n")
        return None

    # --- one WebSocket -----------------------------------------------------------------
    async def _handler(self, connection: ServerConnection) -> None:
        client = _Client(connection)
        if not self._control.claim(client):  # lost a race with another handshake
            await connection.close(1013, "another controller is connected")
            return
        self._client = client
        logger.info("web: controller connected from %s", connection.remote_address)
        writer = asyncio.create_task(self._write(client))
        client.post({"type": "hello", "state": self._label, "send_hz": config.WEB_CLIENT_SEND_HZ,
                     "deadman_s": config.WEB_DEADMAN_S,
                     "ptt": {"available": not self._voice.ptt_unavailable,
                             "reason": self._voice.ptt_unavailable,
                             "max_s": config.WEB_PTT_MAX_S},
                     "audio": {"available": not self._voice.audio_unavailable,
                               "reason": self._voice.audio_unavailable,
                               "rate": config.AUDIO_SAMPLE_RATE,
                               "max_frame_bytes": config.WEB_AUDIO_MAX_FRAME_BYTES},
                     "voice": {"available": self._voice_out},
                     "say": {"available": not self._voice.say_unavailable,
                             "reason": self._voice.say_unavailable,
                             "max_chars": config.WEB_SAY_MAX_CHARS},
                     "persona": self._persona_state()})
        try:
            async for raw in connection:
                self._on_message(client, raw)
                if client.invalid >= config.WEB_MAX_INVALID:
                    self._control.disconnect(client)  # stop FIRST: a close can take a while
                    await connection.close(1008, "too many invalid messages")
                    break
        except Exception:  # noqa: BLE001 - any failure of this connection ends it, with a stop
            logger.exception("web: connection failed")
        finally:
            self._control.disconnect(client)  # ALWAYS sends stop
            if self._client is client:
                self._client = None
            writer.cancel()
            logger.info("web: controller disconnected")

    def _persona_state(self) -> dict[str, Any] | None:
        persona = self._voice.persona
        if persona is None:
            return None
        try:
            return persona.state()
        except Exception:  # noqa: BLE001 - the page just shows no persona section
            logger.exception("web: could not read the persona state")
            return None

    def _on_audio(self, client: _Client, frame: bytes) -> None:
        try:
            self._control.audio_frame(client, parse_audio_frame(frame))
        except (ProtocolError, Refused) as error:
            client.invalid += 1  # a flood of bad or too-fast audio ends the connection
            if client.invalid == 1 or isinstance(error, ProtocolError):
                client.post({"type": "error", "reason": str(error)})
        except Exception:  # noqa: BLE001 - the audio side failed; the page is told, nothing else
            logger.exception("web: audio frame failed")
            client.post({"type": "error", "reason": "audio failed"})

    def _on_message(self, client: _Client, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            self._on_audio(client, raw)
            return
        try:
            request = parse_message(raw)
        except ProtocolError as error:
            client.invalid += 1
            client.post({"type": "error", "reason": str(error)})
            return
        try:
            if request.action == "voice":
                self._set_voice(client, request.on)
            else:
                self._control.handle(client, request)
        except Refused as error:
            client.post({"type": "error", "reason": str(error)})
        except Exception:  # noqa: BLE001 - the voice side failed; the page is told, nothing else
            logger.exception("web: %s failed", request.action)
            client.post({"type": "error", "reason": f"{request.action} failed"})

    def _set_voice(self, client: _Client, on: bool) -> None:
        if self._control.controller is not client:
            return  # like every other action: only the controller
        if on and not self._voice_out:
            raise Refused("Arya's voice on the phone is not enabled (start with --phone-voice)")
        client.voice_on = on
        logger.info("web: the phone %s Arya's voice", "wants" if on else "no longer wants")
        if not on:
            client.clear_audio()

    # --- Arya's voice to the page (any thread) -------------------------------------------------
    def send_audio(self, clip: AudioClip) -> None:
        """One sentence for the page. Never blocks; dropped if nobody wants it or it is too late."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload = encode_voice_clip(clip.samples.astype("<i2", copy=False).tobytes(),
                                    clip.sample_rate)
        try:
            loop.call_soon_threadsafe(self._post_audio, payload)
        except RuntimeError:  # the loop closed between the check and the call
            pass

    def clear_audio(self) -> None:
        """Barge-in, STOP, a new reply: the page drops what it has not played."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._clear_audio)
        except RuntimeError:
            pass

    def _post_audio(self, payload: bytes) -> None:
        client = self._client
        if client is None or not client.voice_on:
            logger.info("web: voice not sent (%s)", "no phone" if client is None
                        else "the phone has not asked for it: tap the voice button")
        elif client.post_audio(payload):
            logger.info("web: sent %d bytes of voice to the phone", len(payload))
        else:
            logger.warning("web: the phone is behind; dropped a sentence of voice")

    def _clear_audio(self) -> None:
        client = self._client
        if client is not None and client.voice_on:
            client.clear_audio()

    @staticmethod
    async def _write(client: _Client) -> None:
        try:
            while True:
                item = await client.outbox.get()
                if isinstance(item, bytes):
                    client.audio_queued -= 1
                await client.connection.send(item)
        except Exception:  # noqa: BLE001 - the connection is gone; the reader ends the session
            return

    # --- the periodic pump: deadman and statuses -------------------------------------------
    async def _pump(self) -> None:
        while True:
            await asyncio.sleep(config.WEB_TICK_S)
            client = self._client
            if self._control.tick() and client is not None:
                client.post({"type": "deadman"})
            if self._control.expire_listening() and client is not None:
                client.post({"type": "ptt_timeout", "max_s": config.WEB_PTT_MAX_S})
            for status in self._subscription.get_all():
                self._relay(client, status)
            for event in self._event_subscription.get_all():  # always drained: none go stale
                if client is not None:
                    client.post(event)

    def _relay(self, client: _Client | None, status: Status) -> None:
        self._label = next_state_label(self._label, status, self._sent)
        if client is not None:
            client.post({"type": "status", "status": status.status, "detail": status.detail,
                         "state": self._label})

    # --- the bridge ------------------------------------------------------------------------
    def _send_command(self, action: str, params: dict[str, Any]) -> int:
        command = new_command(action, params)
        self._bridge.send(command)  # `stop` sets the stop_event inside (the fast path)
        if action != "heartbeat":
            zero_walk = action == "walk" and not any(
                params.get(key) for key in ("direction", "strafe", "yaw"))
            self._sent[command.seq] = "halt" if zero_walk else action
            while len(self._sent) > config.WEB_SENT_MEMORY:
                self._sent.popitem(last=False)
        return command.seq

