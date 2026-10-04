"""Step 12a: the server over a real loopback socket, with a local bridge (no body process).

Covers the handshake (PIN, rate limit, second controller, Origin), the page itself, the deadman and
stop-on-disconnect end to end, the status line, and the architecture rule: the web package never
imports the controller, the gait or a backend.
"""

from __future__ import annotations

import ast
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import config
from brain.event_hub import EventHub
from brain.status_hub import StatusHub
from bridge import Bridge, Command, Status, make_local_bridge
from scripts.web_check import Refused, WebClient
from tests.fakes import FakeClock, collect, wait_until
from tests.test_web_session import FakeVoice
from web.server import WebServer, lan_addresses, resolve_pin

PIN = "246810"
ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("body.controller", "body.gait", "body.sim_backend", "body.servo_backend",
             "body.backend", "body.kinematics", "pybullet")


class Rig:
    def __init__(self, bridge: Bridge, hub: StatusHub, server: WebServer) -> None:
        self.bridge, self.hub, self.server = bridge, hub, server
        self.commands: list[Command] = []

    @property
    def http(self) -> str:
        return f"http://127.0.0.1:{self.server.port}"

    @property
    def ws(self) -> str:
        return f"ws://127.0.0.1:{self.server.port}"

    def connect(self, pin: str = PIN, origin: str | None = None) -> WebClient:
        return WebClient(self.ws, pin, origin=origin)

    def drained(self) -> list[Command]:
        self.commands += self.bridge.drain()
        return self.commands

    def actions(self) -> list[str]:
        return [command.action for command in self.drained()]


@pytest.fixture()
def rig() -> Iterator[Rig]:
    bridge = make_local_bridge()
    hub = StatusHub(bridge)
    server = WebServer(bridge, hub, PIN, port=0)
    server.start()
    yield Rig(bridge, hub, server)
    server.stop()


# --- the page --------------------------------------------------------------------------------
def test_the_page_is_served_with_security_headers(rig: Rig) -> None:
    with urllib.request.urlopen(rig.http + "/", timeout=5) as response:
        body = response.read().decode()
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"
    assert "STOP" in body and "hexa" in body


def test_unknown_paths_404_and_a_plain_get_of_ws_is_refused(rig: Rig) -> None:
    for path, status in (("/nope", 404), ("/ws", 426), ("/ws?pin=" + PIN, 426)):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(rig.http + path, timeout=5)
        assert error.value.code == status, path


def test_the_page_is_one_self_contained_file() -> None:
    page = (ROOT / "web" / "static" / "index.html").read_text()
    assert not re.search(r"(src|href)\s*=\s*[\"'](https?:)?//", page)  # no CDN, no fonts
    assert "@import" not in page and "url(http" not in page
    assert "<script src" not in page and "<link" not in page
    assert [p.name for p in (ROOT / "web" / "static").iterdir()] == ["index.html"]


def test_the_page_guards_its_controls() -> None:
    page = (ROOT / "web" / "static" / "index.html").read_text()
    for needle in ("pointerdown", "pointerup", "pointercancel", "visibilitychange", '"blur"',
                   "pagehide", "setPointerCapture", "contextmenu", "touch-action: none",
                   "user-select: none", "-webkit-touch-callout: none", "overscroll-behavior"):
        assert needle in page, needle
    assert 'action: "stop"' in page and 'id="stop"' in page
    assert "joint" not in page.lower()  # the page never speaks of raw joint data


# --- the handshake ---------------------------------------------------------------------------
def test_a_right_pin_connects_and_gets_hello(rig: Rig) -> None:
    client = rig.connect()
    hello = client.recv(2)
    assert hello is not None and hello["type"] == "hello"
    assert hello["send_hz"] == config.WEB_CLIENT_SEND_HZ
    assert hello["deadman_s"] == config.WEB_DEADMAN_S
    client.close()


@pytest.mark.parametrize("pin", ["000000", "", "246810 ", "24681"])
def test_a_wrong_pin_is_refused_with_401(rig: Rig, pin: str) -> None:
    with pytest.raises(Refused) as error:
        rig.connect(pin)
    assert error.value.status == 401
    assert rig.drained() == []  # nothing reached the bridge


def test_repeated_wrong_pins_lock_the_address_out(rig: Rig) -> None:
    for _ in range(config.WEB_PIN_MAX_FAILURES):
        with pytest.raises(Refused) as error:
            rig.connect("111111")
        assert error.value.status == 401
    with pytest.raises(Refused) as locked:
        rig.connect(PIN)  # even the right PIN is refused during the lockout
    assert locked.value.status == 429


def test_a_second_controller_is_rejected_and_the_first_keeps_control(rig: Rig) -> None:
    first = rig.connect()
    assert first.recv(2) is not None
    with pytest.raises(Refused) as error:
        rig.connect()
    assert error.value.status == 409
    first.walk(forward=1.0)
    wait_until(lambda: "walk" in rig.actions(), what="the first client's walk")
    first.close()
    wait_until(lambda: "stop" in rig.actions(), what="stop on disconnect")
    second = rig.connect()  # the slot is free again
    assert second.recv(2) is not None
    second.close()


def test_a_foreign_origin_is_refused(rig: Rig) -> None:
    with pytest.raises(Refused) as error:
        rig.connect(origin="http://evil.example")
    assert error.value.status == 403
    own = rig.connect(origin=f"http://127.0.0.1:{rig.server.port}")  # the page itself
    assert own.recv(2) is not None
    own.close()


# --- messages, deadman, disconnect -----------------------------------------------------------
def test_combined_hold_reaches_the_bridge_as_one_walk_then_heartbeats(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.hold(0.5, forward=1.0, yaw=1.0)
    client.walk()  # release
    wait_until(lambda: rig.actions().count("walk") >= 2, what="the release walk")
    commands = rig.drained()
    walks = [c for c in commands if c.action == "walk"]
    assert walks[0].params == {"speed": config.WEB_WALK_SPEED, "direction": "fwd", "yaw": 1.0}
    assert walks[-1].params["strafe"] == 0.0 and walks[-1].params["yaw"] == 0.0
    assert any(c.action == "heartbeat" for c in commands)
    assert len(walks) == 2
    client.close()


def test_invalid_messages_get_an_error_and_never_reach_the_bridge(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    for raw in ('{"action": "jump"}', "garbage", '{"action": "walk", "forward": "1"}',
                '{"action": "stop", "x": 1}', '{"action": "walk", "joints": [1]}'):
        client.send(raw)
        reply = client.recv(2)
        assert reply is not None and reply["type"] == "error", raw
    client.close()
    wait_until(lambda: "stop" in rig.actions(), what="stop on disconnect")
    assert rig.actions() == ["stop"]  # only the disconnect's stop


def test_too_many_invalid_messages_close_the_connection(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    for _ in range(config.WEB_MAX_INVALID + 2):
        client.send("nope")
    wait_until(lambda: "stop" in rig.actions(), what="stop after the forced close")


def test_server_deadman_stops_a_walk_when_the_client_goes_silent(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.walk(forward=1.0)  # one message, then silence (the socket stays open)
    wait_until(lambda: "walk" in rig.actions(), what="the walk")
    wait_until(lambda: "stop" in rig.actions(), timeout=2.0, what="the deadman stop")
    assert rig.bridge.stop_event.is_set()  # on the stop_event path
    messages = client.messages(0.3)
    assert {"type": "deadman"} in messages
    client.close()


def test_disconnect_without_a_stop_message_stops_the_robot(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.hold(0.3, forward=1.0)
    client.drop()  # no close handshake, no stop message
    wait_until(lambda: "stop" in rig.actions(), timeout=2.0, what="stop on disconnect")


def test_a_page_stop_message_goes_through_the_stop_event(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.walk(forward=1.0)
    client.send({"action": "stop"})  # what the page sends on blur / hidden / cancel
    wait_until(lambda: rig.bridge.stop_event.is_set(), what="the stop event")
    assert rig.actions()[:2] == ["walk", "stop"]
    client.close()


def test_statuses_reach_the_page_as_a_state_line(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.send({"action": "sit"})
    wait_until(lambda: "sit" in rig.actions(), what="the sit")
    sit_seq = rig.drained()[-1].seq
    rig.hub.publish(Status("accepted", sit_seq, {}, 1, 0.0))
    rig.hub.publish(Status("done", sit_seq, {"action": "sit"}, 2, 0.0))
    states = [m["state"] for m in client.messages(0.5) if m.get("type") == "status"]
    assert states == ["sitting down", "sitting"]
    client.close()


# --- start, stop, defaults -------------------------------------------------------------------
def test_stop_closes_clients_and_stops_the_robot() -> None:
    bridge = make_local_bridge()
    server = WebServer(bridge, StatusHub(bridge), PIN, port=0)
    server.start()
    client = WebClient(f"ws://127.0.0.1:{server.port}", PIN)
    client.recv(2)
    client.hold(0.3, forward=1.0)
    server.stop()
    assert bridge.stop_event.is_set()
    assert client.recv(1) is None  # closed
    server.stop()  # idempotent


def test_the_port_in_use_is_a_clean_error(rig: Rig) -> None:
    other = WebServer(make_local_bridge(), StatusHub(make_local_bridge()), PIN,
                      port=rig.server.port)
    with pytest.raises(OSError, match="could not listen"):
        other.start()


def test_default_bind_is_loopback_only() -> None:
    bridge = make_local_bridge()
    server = WebServer(bridge, StatusHub(bridge), PIN, port=0)
    assert server.host == "127.0.0.1"
    from brain.app import AppOptions

    assert AppOptions().lan is False and AppOptions().web is False
    source = (ROOT / "brain" / "app.py").read_text()
    assert 'host = "0.0.0.0" if options.lan else "127.0.0.1"' in source


def test_pin_resolution() -> None:
    assert resolve_pin("1234", {}) == ("1234", False)
    assert resolve_pin("1234", {"HEXA_WEB_PIN": "9999"}) == ("9999", False)  # env wins
    pin, generated = resolve_pin(None, {})
    assert generated and re.fullmatch(r"\d{6}", pin)
    assert config.WEB_PIN is None  # no PIN is ever committed


def test_lan_addresses_are_not_loopback() -> None:
    assert all(not address.startswith("127.") for address in lan_addresses())


# --- architecture ----------------------------------------------------------------------------
def test_the_web_package_never_imports_the_controller_the_gait_or_a_backend() -> None:
    sources = list((ROOT / "web").glob("*.py")) + [ROOT / "scripts" / "web_check.py"]
    assert len(sources) >= 4
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            for name in names:
                assert not any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN), (
                    f"{path.name} imports {name}")
    server = (ROOT / "web" / "server.py").read_text()
    assert "from bridge import" in server and "new_command" in server


# --- Step 12b: hold-to-talk, typed text and events over a real socket ------------------------
class VoiceRig(Rig):
    def __init__(self, bridge: Bridge, hub: StatusHub, server: WebServer, voice: FakeVoice,
                 events: EventHub, clock: FakeClock) -> None:
        super().__init__(bridge, hub, server)
        self.voice, self.events, self.clock = voice, events, clock


def make_voice_rig(voice: FakeVoice) -> VoiceRig:
    bridge, clock, events = make_local_bridge(), FakeClock(), EventHub()
    hub = StatusHub(bridge)
    server = WebServer(bridge, hub, PIN, port=0, clock=clock.now, voice=voice.controls,
                       events=events)
    server.start()
    return VoiceRig(bridge, hub, server, voice, events, clock)


@pytest.fixture()
def voice_rig() -> Iterator[VoiceRig]:
    made = make_voice_rig(FakeVoice())
    yield made
    made.server.stop()


def hello_of(client: WebClient) -> dict[str, Any]:
    hello = client.recv(2)
    assert hello is not None and hello["type"] == "hello"
    return hello


def test_hello_tells_the_page_whether_it_can_talk_and_type(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello = hello_of(client)
    assert hello["ptt"] == {"available": True, "reason": None, "max_s": config.WEB_PTT_MAX_S}
    assert hello["say"] == {"available": True, "reason": None,
                            "max_chars": config.WEB_SAY_MAX_CHARS}
    client.close()


def test_a_server_without_voice_says_so(rig: Rig) -> None:  # the 12a-style rig
    client = rig.connect()
    hello = hello_of(client)
    assert hello["ptt"]["available"] is False and hello["say"]["available"] is False
    assert hello["ptt"]["reason"] == "voice is not enabled"
    client.close()


def test_press_and_release_reach_the_voice_side_and_not_the_bridge(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello_of(client)
    client.press()
    wait_until(lambda: voice_rig.voice.listening_changes == [True], what="listening on")
    client.release()
    wait_until(lambda: voice_rig.voice.listening_changes == [True, False], what="listening off")
    client.close()
    wait_until(lambda: "stop" in voice_rig.actions(), what="stop on disconnect")
    assert voice_rig.actions() == ["stop"]  # nothing but the disconnect's stop reached the body


def test_a_press_is_forced_to_release_at_the_maximum_duration(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello_of(client)
    client.press()
    wait_until(lambda: voice_rig.voice.listening_changes == [True], what="listening on")
    voice_rig.clock.sleep(config.WEB_PTT_MAX_S - 0.5)
    time.sleep(0.2)  # a few pump ticks
    assert voice_rig.voice.listening_changes == [True]
    voice_rig.clock.sleep(1.0)
    wait_until(lambda: voice_rig.voice.listening_changes == [True, False],
               what="the forced release")
    assert any(m.get("type") == "ptt_timeout" for m in client.messages(0.3))  # the page is told
    client.close()


def test_disconnect_and_a_dropped_connection_release_listening(voice_rig: VoiceRig) -> None:
    for leave in ("close", "drop"):
        voice_rig.voice.calls.clear()
        client = voice_rig.connect()
        hello_of(client)
        client.press()
        wait_until(lambda: voice_rig.voice.listening_changes == [True], what="listening on")
        getattr(client, leave)()
        wait_until(lambda: voice_rig.voice.listening_changes == [True, False],
                   what=f"release after {leave}")


def test_the_page_stop_message_releases_listening_and_stops_the_robot(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello_of(client)
    client.press()
    wait_until(lambda: voice_rig.voice.listening_changes == [True], what="listening on")
    client.send({"action": "stop"})  # the page's hidden / blur / STOP
    wait_until(lambda: voice_rig.voice.listening_changes == [True, False], what="release")
    assert "stop" in voice_rig.actions() and voice_rig.bridge.stop_event.is_set()
    client.close()


def test_closing_the_server_releases_listening() -> None:
    rig = make_voice_rig(FakeVoice())
    client = rig.connect()
    hello_of(client)
    client.press()
    wait_until(lambda: rig.voice.listening_changes == [True], what="listening on")
    rig.server.stop()
    assert rig.voice.listening_changes == [True, False]


def test_say_reaches_the_voice_side_and_a_flood_is_refused(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello_of(client)
    client.say("walk forward")
    wait_until(lambda: ("say", "walk forward") in voice_rig.voice.calls, what="the typed text")
    client.say("sit down")  # the fake clock did not move: too fast
    reply = client.recv(2)
    assert reply is not None and reply["type"] == "error" and "too fast" in reply["reason"]
    assert [c for c in voice_rig.voice.calls if c[0] == "say"] == [("say", "walk forward")]
    voice_rig.clock.sleep(config.WEB_SAY_MIN_INTERVAL_S + 0.1)
    client.say("sit down")
    wait_until(lambda: ("say", "sit down") in voice_rig.voice.calls, what="the second text")
    client.close()


@pytest.mark.parametrize("text", ["", "x" * (config.WEB_SAY_MAX_CHARS + 1), "bell\x07"])
def test_a_bad_say_is_an_error_and_goes_nowhere(voice_rig: VoiceRig, text: str) -> None:
    client = voice_rig.connect()
    hello_of(client)
    client.say(text)
    reply = client.recv(2)
    assert reply is not None and reply["type"] == "error"
    assert voice_rig.voice.calls == []
    client.close()


def test_no_mic_disables_ptt_but_typing_still_works() -> None:
    reason = "the robot runs with --no-mic: it has no microphone to hold"
    rig = make_voice_rig(FakeVoice(ptt_unavailable=reason))
    try:
        client = rig.connect()
        hello = hello_of(client)
        assert hello["ptt"]["available"] is False and hello["ptt"]["reason"] == reason
        assert hello["say"]["available"] is True
        client.press()
        reply = client.recv(2)
        assert reply == {"type": "error", "reason": reason}
        assert rig.voice.listening_changes == []  # the microphone was never opened
        client.say("hello")
        wait_until(lambda: ("say", "hello") in rig.voice.calls, what="typing")
        client.close()
    finally:
        rig.server.stop()


def test_a_second_controller_cannot_press_and_the_first_keeps_its_press(
    voice_rig: VoiceRig,
) -> None:
    first = voice_rig.connect()
    hello_of(first)
    first.press()
    wait_until(lambda: voice_rig.voice.listening_changes == [True], what="listening on")
    with pytest.raises(Refused) as error:
        voice_rig.connect()
    assert error.value.status == 409  # refused at the handshake: it cannot send anything
    assert voice_rig.voice.listening_changes == [True]
    first.close()
    wait_until(lambda: voice_rig.voice.listening_changes == [True, False], what="release")


def test_events_reach_the_page_in_order(voice_rig: VoiceRig) -> None:
    client = voice_rig.connect()
    hello_of(client)
    published: list[dict[str, Any]] = [
        {"type": "listening", "on": True},
        {"type": "heard", "text": "walk forward"},
        {"type": "route", "route": "command", "action": "walk", "text": "walk forward",
         "early_stop": False},
        {"type": "said", "text": "okay"},
        {"type": "listening", "on": False},
    ]
    for event in published:
        voice_rig.events.publish(dict(event))
    got: list[dict[str, Any]] = []
    wait_until(lambda: len(collect(client, got, 0.1)) >= len(published), what="the events")
    assert got[:len(published)] == published
    client.close()


def test_events_from_before_the_page_connected_are_not_replayed(voice_rig: VoiceRig) -> None:
    voice_rig.events.publish({"type": "heard", "text": "old news"})
    time.sleep(0.2)  # the pump drains the subscription with nobody connected
    client = voice_rig.connect()
    hello_of(client)
    assert not any(m.get("type") == "heard" for m in client.messages(0.3))
    client.close()


def test_a_slow_page_drops_the_oldest_events_and_never_blocks_the_publisher() -> None:
    from web.server import _Client

    client = _Client(None)  # type: ignore[arg-type]  # only its outbox is used
    for number in range(100):
        client.post({"type": "said", "text": str(number)})
    queued = [json.loads(client.outbox.get_nowait())["text"]
              for _ in range(client.outbox.qsize())]
    newest = [str(number) for number in range(100 - len(queued), 100)]
    assert queued == newest  # the newest, in order
    assert 0 < len(queued) <= 32

    # and end to end: a burst nobody reads costs the publisher almost nothing
    rig = make_voice_rig(FakeVoice())
    try:
        page = rig.connect()
        hello_of(page)
        start = time.monotonic()
        for number in range(20000):
            rig.events.publish({"type": "said", "text": str(number)})
        assert time.monotonic() - start < 2.0
        page.send({"action": "stop"})  # and STOP still works through the flood
        wait_until(lambda: rig.bridge.stop_event.is_set(), what="stop through a flood of events")
        page.close()
    finally:
        rig.server.stop()
