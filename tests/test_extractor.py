"""The memory extractor: the JSON schema, the saving rules, the thread behaviour."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from brain.chat import ChatError, FakeChat
from brain.extractor import (
    MemoryExtractor,
    RuleExtractorChat,
    extract_facts,
    parse_facts_json,
    valid_facts,
)
from brain.memory import Memory
from brain.output_check import OutputCheck
from tests.fakes import FakeClock

CHECK = OutputCheck()


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    db = Memory(tmp_path / "arya.db")
    yield db
    db.close()


# -- the JSON schema -----------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ('{"facts": ["Likes tea.", "Studies maths."]}', ["Likes tea.", "Studies maths."]),
    ('{"facts": []}', []),
    ('```json\n{"facts": ["Likes tea."]}\n```', ["Likes tea."]),
    ('Sure! {"facts": ["Likes tea."]} Hope that helps.', ["Likes tea."]),
    ('  {"facts":["Likes tea."]}  ', ["Likes tea."]),
])
def test_valid_json_is_accepted(raw: str, expected: list[str]) -> None:
    assert parse_facts_json(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "no json here", "[]", '["Likes tea."]', '{"fact": ["Likes tea."]}',
    '{"facts": "Likes tea."}', '{"facts": [1, 2]}', '{"facts": [["nested"]]}',
    '{"facts": ["a"], "extra": 1}', '{"facts": [null]}', '{"facts": ["a"]', "{'facts': ['a']}",
])
def test_anything_off_schema_is_thrown_away_whole(raw: str) -> None:
    assert parse_facts_json(raw) is None


# -- the rules -------------------------------------------------------------------------------------
def test_rules_drop_long_sensitive_duplicate_and_surplus_facts() -> None:
    candidates = [
        "Likes cricket.",
        "likes cricket",  # duplicate of the first
        "Already known.",  # duplicate of an existing fact
        "x" * (config.MEMORY_FACT_MAX_CHARS + 1),  # too long
        "Is Hindu.",  # religion
        "Has diabetes.",  # health
        "Has a brother.",  # family
        "Email is a@b.com.",  # private
        "",
        "   ",
        "Studies robotics.",
        "Plays chess.",
        "Has a cat.",
    ]
    kept = valid_facts(candidates, ["Already known."], CHECK, limit=3)
    assert kept == ["Likes cricket.", "Studies robotics.", "Plays chess."]


def test_extract_facts_shows_the_model_the_turns_and_applies_the_rules() -> None:
    backend = FakeChat(('{"facts": ["Likes tea.", "Is Muslim."]}',))
    facts = extract_facts(backend, [("I like tea", "nice")], [], CHECK)
    assert facts == ["Likes tea."]
    sent = backend.last_messages
    assert sent[0]["role"] == "system" and "JSON" in sent[0]["content"]
    assert "Person: I like tea" in sent[1]["content"]


def test_extract_facts_with_a_broken_answer_saves_nothing() -> None:
    assert extract_facts(FakeChat(("I think they like tea.",)), [("x", "y")], [], CHECK) == []


def test_the_rule_based_stand_in_speaks_the_same_json() -> None:
    turns = [("I like cricket and football", "ok"), ("I study robotics", "ok"),
             ("hello there", "hi")]
    facts = extract_facts(RuleExtractorChat(), turns, [], CHECK)
    assert facts == ["Likes cricket.", "Studies robotics."]
    raw = "".join(RuleExtractorChat().stream([{"role": "user", "content": "Person: I love tea"}]))
    assert json.loads(raw) == {"facts": ["Likes tea."]}


# -- the extractor object -------------------------------------------------------------------------
def make(memory: Memory, replies: tuple[str, ...], clock: FakeClock,
         idle: bool = True) -> tuple[MemoryExtractor, FakeChat]:
    backend = FakeChat(replies)
    extractor = MemoryExtractor(memory, lambda: backend, CHECK, lambda: idle, clock.now,
                                idle_s=30.0)
    return extractor, backend


def test_facts_are_saved_only_after_the_conversation_goes_idle(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock)
    extractor.add_turn("Anna", "I like tea", "nice")
    extractor.add_turn("Anna", "and biscuits", "yum")
    clock.advance(29)
    assert extractor.tick() == 0 and backend.calls == 0
    clock.advance(2)
    assert extractor.tick() == 1 and backend.calls == 1
    assert [f.text for f in memory.facts(anna.id)] == ["Likes tea."]
    assert extractor.tick() == 0 and backend.calls == 1  # the talk was used up


def test_nothing_is_ever_learned_about_a_guest(memory: Memory) -> None:
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock)
    extractor.add_turn(config.GUEST_NAME, "I like tea", "nice")
    extractor.add_turn(config.GUEST_NAME, "really", "yes")
    clock.advance(60)
    assert extractor.tick() == 0 and backend.calls == 0
    assert memory.list_people() == []


def test_a_short_talk_is_not_worth_a_model_call(memory: Memory) -> None:
    memory.ensure_person("anna")
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock)
    extractor.add_turn("Anna", "I like tea", "nice")  # one turn only
    clock.advance(60)
    assert extractor.tick() == 0 and backend.calls == 0


def test_a_single_turn_is_kept_and_joined_by_the_next_one(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock)
    extractor.add_turn("Anna", "I like tea", "nice")
    clock.advance(60)
    assert extractor.tick() == 0 and backend.calls == 0
    extractor.add_turn("Anna", "and biscuits", "yum")  # the talk now has two turns
    clock.advance(31)
    assert extractor.tick() == 1 and backend.calls == 1
    assert [f.text for f in memory.facts(anna.id)] == ["Likes tea."]


def test_it_waits_while_the_robot_is_busy(memory: Memory) -> None:
    memory.ensure_person("anna")
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock, idle=False)
    for _ in range(2):
        extractor.add_turn("Anna", "I like tea", "nice")
    clock.advance(60)
    assert extractor.tick() == 0 and backend.calls == 0


def test_a_failing_backend_is_logged_and_ignored(memory: Memory,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    clock = FakeClock()
    backend = FakeChat(fail_after=0)
    extractor = MemoryExtractor(memory, lambda: backend, CHECK, lambda: True, clock.now, 30.0)
    for _ in range(2):
        extractor.add_turn("Anna", "I like tea", "nice")
    clock.advance(60)
    assert extractor.tick() == 0  # no exception
    assert memory.fact_count(anna.id) == 0
    assert any("failed" in r.message for r in caplog.records)
    with pytest.raises(ChatError):  # the same failure does surface from extract_facts itself
        extract_facts(backend, [("a", "b")], [], CHECK)


def test_no_backend_means_no_extraction(memory: Memory) -> None:
    memory.ensure_person("anna")
    clock = FakeClock()
    extractor = MemoryExtractor(memory, lambda: None, CHECK, lambda: True, clock.now, 30.0)
    for _ in range(2):
        extractor.add_turn("Anna", "x", "y")
    clock.advance(60)
    assert extractor.tick() == 0 and extractor.runs == 0


def test_a_change_of_speaker_closes_the_earlier_talk(memory: Memory) -> None:
    anna, ben = memory.ensure_person("anna"), memory.ensure_person("ben")
    assert anna and ben
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}', '{"facts": ["Plays chess."]}'),
                              clock)
    for _ in range(2):
        extractor.add_turn("Anna", "I like tea", "nice")
    for _ in range(2):
        extractor.add_turn("Ben", "I play chess", "cool")
    assert extractor.tick() == 1  # Anna's talk is closed at once; Ben's is still going on
    assert [f.text for f in memory.facts(anna.id)] == ["Likes tea."]
    assert memory.facts(ben.id) == []
    clock.advance(60)
    assert extractor.tick() == 1
    assert [f.text for f in memory.facts(ben.id)] == ["Plays chess."]


def test_forgetting_a_person_drops_their_unprocessed_talk(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    clock = FakeClock()
    extractor, backend = make(memory, ('{"facts": ["Likes tea."]}',), clock)
    for _ in range(2):
        extractor.add_turn("Anna", "I like tea", "nice")
    extractor.forget_pending("Anna")
    clock.advance(60)
    assert extractor.tick() == 0 and backend.calls == 0


def test_sensitive_facts_from_the_model_never_reach_the_database(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    clock = FakeClock()
    reply = json.dumps({"facts": ["Is Christian.", "Has cancer.", "Likes tea."]})
    extractor, _ = make(memory, (reply,), clock)
    for _ in range(2):
        extractor.add_turn("Anna", "I like tea", "nice")
    clock.advance(60)
    assert extractor.tick() == 1
    assert [f.text for f in memory.facts(anna.id)] == ["Likes tea."]


def test_the_thread_starts_and_stops(memory: Memory) -> None:
    extractor, _ = make(memory, ("{}",), FakeClock())
    extractor.start()
    extractor.shutdown()
    assert extractor._thread is None
