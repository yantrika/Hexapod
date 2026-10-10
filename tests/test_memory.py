"""Step 13 (P1): the memory database: CRUD, forget, migrations, file mode, threads."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from brain.memory import SCHEMA_VERSION, Memory, MemoryDbError, clean_name


@pytest.fixture
def memory(tmp_path: Path) -> Iterator[Memory]:
    db = Memory(tmp_path / "data" / "memory" / "arya.db")
    yield db
    db.close()


def test_the_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "m" / "arya.db"
    Memory(path).close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_the_database_is_gitignored_and_not_synced() -> None:
    root = Path(config.PROJECT_ROOT)
    assert "data/memory/" in (root / ".gitignore").read_text()
    assert "/data/memory/" in (root / "scripts" / "sync_to_pi.sh").read_text()


@pytest.mark.parametrize("raw,expected", [
    ("siddharth", "Siddharth"), ("  mary   ann ", "Mary Ann"), ("o'neil", "O'Neil"),
    ("jean-luc", "Jean-Luc"), ("guest", None), ("Guest", None), ("", None), ("r2d2", None),
    ("a b c", None), ("x" * 40, None), ("hello!", None),
])
def test_names(raw: str, expected: str | None) -> None:
    assert clean_name(raw) == expected


def test_a_new_person_is_created_once_ignoring_case(memory: Memory) -> None:
    first = memory.ensure_person("siddharth")
    again = memory.ensure_person("SIDDHARTH")
    assert first is not None and again is not None and first.id == again.id
    assert first.name == "Siddharth" and first.roast_ok
    assert [p.name for p in memory.list_people()] == ["Siddharth"]
    assert memory.ensure_person("guest") is None
    assert memory.ensure_person("123") is None


def test_the_number_of_people_is_capped(memory: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_MAX_PEOPLE", 2)
    assert memory.ensure_person("anna") and memory.ensure_person("ben")
    assert memory.ensure_person("cara") is None
    assert memory.ensure_person("anna") is not None  # a known person still works


def test_facts_are_added_deduplicated_and_listed_newest_first(memory: Memory) -> None:
    person = memory.ensure_person("anna")
    assert person is not None
    assert memory.add_fact(person.id, "Likes cricket.")
    assert not memory.add_fact(person.id, "likes  CRICKET")  # same fact
    assert memory.add_fact(person.id, "Studies robotics.", "told")
    assert [f.text for f in memory.facts(person.id)] == ["Studies robotics.", "Likes cricket."]
    assert [f.text for f in memory.facts(person.id, 1)] == ["Studies robotics."]
    assert memory.fact_count(person.id) == 2


def test_bad_facts_are_refused(memory: Memory) -> None:
    person = memory.ensure_person("anna")
    assert person is not None
    assert not memory.add_fact(person.id, "")
    assert not memory.add_fact(person.id, "x" * (config.MEMORY_FACT_MAX_CHARS + 1))
    assert not memory.add_fact(person.id, "fine", source="guessed")
    assert not memory.add_fact(999, "Likes tea.")  # nobody has that id


def test_the_oldest_fact_goes_when_a_person_is_full(
        memory: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MEMORY_MAX_FACTS_PER_PERSON", 3)
    person = memory.ensure_person("anna")
    assert person is not None
    for n in range(5):
        assert memory.add_fact(person.id, f"Fact number {n}.")
    assert [f.text for f in memory.facts(person.id)] == [
        "Fact number 4.", "Fact number 3.", "Fact number 2."]


def test_summaries_keep_the_latest(memory: Memory) -> None:
    person = memory.ensure_person("anna")
    assert person is not None
    assert memory.latest_summary(person.id) is None
    assert memory.add_summary(person.id, "Talked about robots.")
    assert memory.add_summary(person.id, "Talked about cricket.")
    assert memory.latest_summary(person.id) == "Talked about cricket."
    assert not memory.add_summary(person.id, "   ")


def test_people_list_has_fact_counts(memory: Memory) -> None:
    anna, ben = memory.ensure_person("anna"), memory.ensure_person("ben")
    assert anna and ben
    memory.add_fact(anna.id, "Likes tea.")
    memory.add_fact(anna.id, "Likes jazz.")
    listing = {p.name: p.facts for p in memory.list_people()}
    assert listing == {"Anna": 2, "Ben": 0}


def test_forget_deletes_everything_about_the_person_and_only_them(memory: Memory) -> None:
    anna, ben = memory.ensure_person("anna"), memory.ensure_person("ben")
    assert anna and ben
    for person in (anna, ben):
        memory.add_fact(person.id, f"Likes {person.name}.")
        memory.add_summary(person.id, f"Summary of {person.name}.")
        memory.add_embedding(person.id, b"\x01\x02\x03\x04")
    assert memory.forget_person(anna.id)
    assert memory.get_person("anna") is None
    assert memory.facts(anna.id) == [] and memory.latest_summary(anna.id) is None
    assert memory.embedding_count(anna.id) == 0
    # a second delete finds nothing; Ben is untouched
    assert not memory.forget_person(anna.id)
    assert memory.fact_count(ben.id) == 1 and memory.summary_count(ben.id) == 1
    assert memory.embedding_count(ben.id) == 1
    with memory._lock:  # nothing of Anna is left in ANY table
        for table in ("facts", "summaries", "face_embeddings"):
            rows = memory._db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE person_id = ?", (anna.id,)).fetchone()
            assert rows[0] == 0


def test_forget_is_one_transaction(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    memory.add_fact(anna.id, "Likes tea.")
    memory.add_embedding(anna.id, b"abcd")
    with memory._lock:  # break the last statement: the person row's delete fails
        memory._db.execute("DROP TABLE face_embeddings")
    with pytest.raises(sqlite3.Error):
        memory.forget_person(anna.id)
    assert memory.fact_count(anna.id) == 1  # rolled back: the facts are still there
    assert memory.get_person("anna") is not None


def test_roast_ok_is_stored(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    assert memory.set_roast_ok(anna.id, False)
    assert [p.roast_ok for p in memory.list_people()] == [False]
    assert not memory.set_roast_ok(9999, True)
    person = memory.get_person("anna")
    assert person is not None and not person.roast_ok


def test_meta_roundtrip(memory: Memory) -> None:
    assert memory.get_meta("mode") is None
    memory.set_meta("mode", "roast")
    memory.set_meta("mode", "funny")
    assert memory.get_meta("mode") == "funny"


def test_a_new_file_gets_the_current_schema_and_reopening_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "arya.db"
    first = Memory(path)
    assert first.schema_version == SCHEMA_VERSION
    person = first.ensure_person("anna")
    assert person
    first.add_fact(person.id, "Likes tea.")
    first.close()
    second = Memory(path)
    again = second.get_person("anna")
    assert again is not None and second.fact_count(again.id) == 1
    second.close()


def test_a_database_from_a_newer_program_is_refused_not_damaged(tmp_path: Path) -> None:
    path = tmp_path / "arya.db"
    Memory(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
    db.commit()
    db.close()
    with pytest.raises(MemoryDbError, match="schema version"):
        Memory(path)
    check = sqlite3.connect(path)  # untouched
    assert check.execute("SELECT value FROM meta").fetchone()[0] == str(SCHEMA_VERSION + 1)
    check.close()


def test_foreign_keys_cascade(memory: Memory) -> None:
    anna = memory.ensure_person("anna")
    assert anna
    memory.add_fact(anna.id, "Likes tea.")
    with memory._lock:
        memory._db.execute("DELETE FROM people WHERE id = ?", (anna.id,))
        count = memory._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert count == 0


def test_many_threads_can_write_at_once(memory: Memory) -> None:
    person = memory.ensure_person("anna")
    assert person
    errors: list[BaseException] = []

    def work(start: int) -> None:
        try:
            for n in range(20):
                memory.add_fact(person.id, f"Thread {start} fact {n}.")
                memory.list_people()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors
    assert memory.fact_count(person.id) == min(80, config.MEMORY_MAX_FACTS_PER_PERSON)
