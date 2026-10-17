"""The persona section of the phone page: protocol validation, controller-only
actions, and the whole path over a real loopback socket (page -> server -> Arya -> memory -> page).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import config
from brain.arya import Arya
from brain.event_hub import EventHub
from brain.memory import Memory
from brain.persona import Persona
from brain.status_hub import StatusHub
from bridge import make_local_bridge
from scripts.web_check import WebClient
from tests.fakes import FakeClock, collect, wait_until
from web.protocol import ProtocolError, parse_message
from web.server import WebServer
from web.session import PersonaControls, Refused, VoiceControls, WebControl

PIN = "112233"


# -- the protocol --------------------------------------------------------------------------------
def parse(message: dict[str, Any]) -> Any:
    return parse_message(json.dumps(message))


@pytest.mark.parametrize("mode", config.PERSONA_MODES)
def test_set_mode_accepts_the_three_modes(mode: str) -> None:
    request = parse({"action": "set_mode", "mode": mode})
    assert request.action == "set_mode" and request.mode == mode


def test_set_speaker_and_forget_person_are_parsed() -> None:
    assert parse({"action": "set_speaker", "name": "  Anna  "}).name == "Anna"
    assert parse({"action": "set_speaker", "name": "guest"}).name == "guest"
    assert parse({"action": "forget_person", "id": 7}).person_id == 7
    toggle = parse({"action": "set_roast_ok", "id": 7, "roast_ok": False})
    assert toggle.person_id == 7 and toggle.roast_ok is False


@pytest.mark.parametrize("message", [
    {"action": "set_mode"}, {"action": "set_mode", "mode": "shouting"},
    {"action": "set_mode", "mode": "ROAST"}, {"action": "set_mode", "mode": 1},
    {"action": "set_mode", "mode": "roast", "extra": 1},
    {"action": "set_speaker"}, {"action": "set_speaker", "name": ""},
    {"action": "set_speaker", "name": "   "}, {"action": "set_speaker", "name": 5},
    {"action": "set_speaker", "name": "x" * (config.MEMORY_NAME_MAX_CHARS + 1)},
    {"action": "set_speaker", "name": "bad\x00name"},
    {"action": "set_speaker", "name": "Anna", "id": 1},
    {"action": "forget_person"}, {"action": "forget_person", "id": "1"},
    {"action": "forget_person", "id": 1.5}, {"action": "forget_person", "id": True},
    {"action": "forget_person", "id": 0}, {"action": "forget_person", "id": -3},
    {"action": "forget_person", "id": 2**40}, {"action": "forget_person", "id": 1, "name": "A"},
    {"action": "set_roast_ok"}, {"action": "set_roast_ok", "id": 1},
    {"action": "set_roast_ok", "roast_ok": True},
    {"action": "set_roast_ok", "id": 1, "roast_ok": "yes"},
    {"action": "set_roast_ok", "id": 1, "roast_ok": 1},
    {"action": "set_roast_ok", "id": 1, "roast_ok": None},
    {"action": "set_roast_ok", "id": "1", "roast_ok": True},
    {"action": "set_roast_ok", "id": 0, "roast_ok": True},
    {"action": "set_roast_ok", "id": True, "roast_ok": True},
    {"action": "set_roast_ok", "id": 1, "roast_ok": True, "name": "A"},
])
def test_bad_persona_messages_are_rejected(message: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError):
        parse(message)


# -- controller only -------------------------------------------------------------------------------
class FakePersona:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.accept = True
        self.controls = PersonaControls(self.set_mode, self.set_speaker, self.forget,
                                        self.roast_ok, lambda: {"type": "persona"})

    def set_mode(self, mode: str) -> bool:
        self.calls.append(("mode", mode))
        return self.accept

    def set_speaker(self, name: str) -> bool:
        self.calls.append(("speaker", name))
        return self.accept

    def forget(self, person_id: int) -> bool:
        self.calls.append(("forget", person_id))
        return self.accept

    def roast_ok(self, person_id: int, ok: bool) -> bool:
        self.calls.append(("roast_ok", (person_id, ok)))
        return self.accept


def make_control(persona: FakePersona | None) -> tuple[WebControl, list[tuple[str, dict]], object]:
    sent: list[tuple[str, dict]] = []

    def send(action: str, params: dict[str, Any]) -> int:
        sent.append((action, params))
        return len(sent)

    voice = VoiceControls(lambda on: None, lambda text: None, persona=persona.controls
                          if persona else None)
    control = WebControl(send, FakeClock().now, voice=voice)
    client = object()
    control.claim(client)
    return control, sent, client


def test_the_controller_can_change_the_mode_the_speaker_and_forget_people() -> None:
    persona = FakePersona()
    control, sent, client = make_control(persona)
    assert control.handle(client, parse({"action": "set_mode", "mode": "roast"}))
    assert control.handle(client, parse({"action": "set_speaker", "name": "Anna"}))
    assert control.handle(client, parse({"action": "forget_person", "id": 3}))
    assert control.handle(client, parse({"action": "set_roast_ok", "id": 3, "roast_ok": False}))
    assert persona.calls == [("mode", "roast"), ("speaker", "Anna"), ("forget", 3),
                             ("roast_ok", (3, False))]
    assert sent == []  # never a bridge message: the body cannot be moved from here


def test_anyone_else_is_ignored() -> None:
    persona = FakePersona()
    control, _, _ = make_control(persona)
    stranger = object()
    for message in ({"action": "set_mode", "mode": "roast"},
                    {"action": "set_speaker", "name": "Anna"},
                    {"action": "forget_person", "id": 3},
                    {"action": "set_roast_ok", "id": 3, "roast_ok": False}):
        assert not control.handle(stranger, parse(message))
    assert persona.calls == []


def test_after_the_controller_leaves_nobody_can_change_anything() -> None:
    persona = FakePersona()
    control, _, client = make_control(persona)
    control.disconnect(client)
    assert not control.handle(client, parse({"action": "forget_person", "id": 1}))
    assert persona.calls == []


def test_a_refused_change_is_reported_with_a_reason() -> None:
    persona = FakePersona()
    persona.accept = False
    control, _, client = make_control(persona)
    for message, reason in (({"action": "set_mode", "mode": "calm"}, "unknown mode"),
                            ({"action": "set_speaker", "name": "Zed"}, "know that person"),
                            ({"action": "forget_person", "id": 9}, "nobody"),
                            ({"action": "set_roast_ok", "id": 9, "roast_ok": True}, "nobody")):
        with pytest.raises(Refused, match=reason):
            control.handle(client, parse(message))


def test_without_a_persona_the_actions_are_refused_politely() -> None:
    control, _, client = make_control(None)
    with pytest.raises(Refused, match="not enabled"):
        control.handle(client, parse({"action": "set_mode", "mode": "calm"}))


def test_persona_actions_never_touch_the_deadman_or_listening() -> None:
    persona = FakePersona()
    control, sent, client = make_control(persona)
    control.handle(client, parse({"action": "walk", "forward": 1}))
    control.handle(client, parse({"action": "set_mode", "mode": "roast"}))
    assert control.moving and [a for a, _ in sent] == ["walk"]


# -- the whole path over a socket --------------------------------------------------------------
class NoSpeech:
    def say(self, text: str) -> int:
        return 0

    def say_phrase(self, name: str) -> int:
        return 0


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.bridge = make_local_bridge()
        self.hub = StatusHub(self.bridge)
        self.events = EventHub()
        self.memory = Memory(tmp_path / "arya.db")
        self.arya = Arya(Persona.from_file(), self.memory, NoSpeech(),
                         on_change=lambda state: self.events.publish(state.as_event()))
        controls = PersonaControls(
            self.arya.set_mode,
            lambda name: self.arya.set_speaker(name, announce=False, create=False),
            self.arya.forget_person, self.arya.set_roast_ok,
            lambda: self.arya.state().as_event())
        voice = VoiceControls(lambda on: None, lambda text: None, persona=controls)
        self.server = WebServer(self.bridge, self.hub, PIN, port=0, voice=voice,
                                events=self.events)
        self.server.start()

    def connect(self) -> WebClient:
        return WebClient(f"ws://127.0.0.1:{self.server.port}", PIN)

    def close(self) -> None:
        self.server.stop()
        self.memory.close()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    made = Rig(tmp_path)
    yield made
    made.close()


def persona_events(client: WebClient, got: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collect(client, got)
    return [m for m in got if m.get("type") == "persona"]


def test_hello_carries_the_persona_state(rig: Rig) -> None:
    person = rig.memory.ensure_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    client = rig.connect()
    hello = client.recv(2)
    assert hello is not None
    assert hello["persona"] == {
        "type": "persona", "mode": "calm", "modes": ["calm", "funny", "roast"],
        "speaker": "guest", "mood": "neutral", "memory": True,
        "people": [{"id": person.id, "name": "Anna", "facts": 1, "roast_ok": True}]}
    client.close()


def test_the_mode_buttons_change_the_mode_and_the_page_hears_about_it(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.send({"action": "set_mode", "mode": "roast"})
    got: list[dict[str, Any]] = []
    wait_until(lambda: any(e["mode"] == "roast" for e in persona_events(client, got)),
               what="the persona event")
    assert rig.arya.mode == "roast"
    client.close()


def test_picking_a_speaker_and_forgetting_a_person(rig: Rig) -> None:
    person = rig.memory.ensure_person("anna")
    assert person
    rig.memory.add_fact(person.id, "Likes tea.")
    client = rig.connect()
    client.recv(2)
    client.send({"action": "set_speaker", "name": "Anna"})
    got: list[dict[str, Any]] = []
    wait_until(lambda: any(e["speaker"] == "Anna" for e in persona_events(client, got)),
               what="the speaker")
    client.send({"action": "forget_person", "id": person.id})
    wait_until(lambda: any(e["people"] == [] for e in persona_events(client, got)),
               what="the person to be forgotten")
    assert rig.memory.get_person("anna") is None and rig.memory.facts(person.id) == []
    assert rig.arya.speaker == config.GUEST_NAME
    client.close()


def test_the_roast_ok_toggle_is_stored_and_shown_on_the_page(rig: Rig) -> None:
    person = rig.memory.ensure_person("anna")
    assert person
    client = rig.connect()
    client.recv(2)
    client.send({"action": "set_roast_ok", "id": person.id, "roast_ok": False})
    got: list[dict[str, Any]] = []
    wait_until(lambda: any(e["people"] and e["people"][0]["roast_ok"] is False
                           for e in persona_events(client, got)), what="the roast_ok event")
    refreshed = rig.memory.get_person("anna")
    assert refreshed is not None and not refreshed.roast_ok
    rig.arya.set_mode("roast")
    assert "Mode: roast" in rig.arya.context_parts("hi").system  # roast mode is not gated
    client.close()


def test_an_unknown_speaker_and_a_bad_id_are_refused_and_nothing_changes(rig: Rig) -> None:
    client = rig.connect()
    client.recv(2)
    client.send({"action": "set_speaker", "name": "Nobody"})
    reply = client.recv(2)
    assert reply is not None and reply["type"] == "error" and "know" in reply["reason"]
    client.send({"action": "forget_person", "id": 99})
    reply = client.recv(2)
    assert reply is not None and reply["type"] == "error"
    client.send({"action": "set_mode", "mode": "shouting"})
    reply = client.recv(2)
    assert reply is not None and reply["type"] == "error" and reply["reason"] == "unknown mode"
    assert rig.arya.mode == "calm" and rig.arya.speaker == config.GUEST_NAME
    client.close()


def test_a_second_phone_cannot_get_in_to_change_the_mode(rig: Rig) -> None:
    first = rig.connect()
    first.recv(2)
    with pytest.raises(Exception, match="409|another"):
        rig.connect()
    assert rig.arya.mode == "calm"
    first.close()


def test_the_page_has_the_persona_section_and_a_confirm_before_forgetting() -> None:
    page = (Path(config.PROJECT_ROOT) / "web" / "static" / "index.html").read_text()
    for needle in ('data-mode="calm"', 'data-mode="funny"', 'data-mode="roast"', 'id="speaker"',
                   'id="people"', "forget_person", "set_roast_ok", "set_mode", "set_speaker",
                   "window.confirm"):
        assert needle in page, needle
    assert "innerHTML" not in page  # names are people's words: only ever textContent
