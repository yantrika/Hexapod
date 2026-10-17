"""Arya's memory: people, facts, summaries and (later) face embeddings in one SQLite file.

The brain process owns the database; nothing else opens it (the planned vision process only sends
events). All access goes through ONE class, ``Memory``, which is thread-safe (one connection, one
lock) because the voice worker, the extractor thread and the web server all use it. The file is
``config.MEMORY_DB_PATH`` (``data/memory/arya.db``): gitignored, excluded from ``sync_to_pi.sh``
and created with mode 600 in a directory with mode 700.

Schema versions are applied in order by ``MIGRATIONS`` (the version lives in ``meta``); a database
from a newer program is refused rather than damaged. ``forget_person`` deletes the person's
facts, summaries, face embeddings and row in ONE transaction. Face embeddings are created now but
unused until face recognition is built (docs/ROADMAP.md); their bytes never go into a log.

The unknown speaker is ``config.GUEST_NAME`` and has no row: nothing is remembered about a guest.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_MIGRATIONS = {
    1: """
    CREATE TABLE people (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        roast_ok INTEGER NOT NULL DEFAULT 1,
        created REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE TABLE facts (
        id INTEGER PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX facts_person ON facts(person_id, id);
    CREATE TABLE summaries (
        id INTEGER PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX summaries_person ON summaries(person_id, id);
    CREATE TABLE face_embeddings (
        id INTEGER PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        vector BLOB NOT NULL,
        created REAL NOT NULL
    );
    CREATE INDEX face_person ON face_embeddings(person_id);
    """,
}
FACT_SOURCES = ("told", "inferred")
_NAME = re.compile(r"[A-Za-z][A-Za-z'\-]*(?: [A-Za-z][A-Za-z'\-]*)?")


class MemoryDbError(RuntimeError):
    """The database cannot be used (the message says why)."""


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    roast_ok: bool
    created: float
    last_seen: float


@dataclass(frozen=True)
class PersonSummary:
    """What the phone page lists: a name and how many facts are kept."""

    id: int
    name: str
    facts: int
    last_seen: float
    roast_ok: bool = True


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    source: str
    created: float


def clean_name(raw: str) -> str | None:
    """A name Arya may use: one or two words of letters, at most ``MEMORY_NAME_MAX_CHARS``, in
    title case, and not the guest's. None if it is not usable."""
    name = " ".join(raw.split()).strip()
    if not name or len(name) > config.MEMORY_NAME_MAX_CHARS or not _NAME.fullmatch(name):
        return None
    name = name.title()
    return None if name.lower() == config.GUEST_NAME else name


class Memory:
    """The one gateway to ``arya.db`` (see the module docstring)."""

    def __init__(self, path: Path | str = config.MEMORY_DB_PATH,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._db = self._open(self.path)

    # -- open / migrate -----------------------------------------------------------------------
    def _open(self, path: Path) -> sqlite3.Connection:
        in_memory = str(path) == ":memory:"
        if not in_memory:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not path.exists():  # created private from the first byte
                os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
            os.chmod(path, 0o600)
        db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        db.execute("PRAGMA foreign_keys = ON")
        self._migrate(db)
        return db

    @staticmethod
    def _version(db: sqlite3.Connection) -> int:
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else 0

    def _migrate(self, db: sqlite3.Connection) -> None:
        version = self._version(db)
        if version > SCHEMA_VERSION:
            db.close()
            raise MemoryDbError(
                f"{self.path} is schema version {version}; this program knows up to "
                f"{SCHEMA_VERSION} (update the program, or move the file away)")
        for target in range(version + 1, SCHEMA_VERSION + 1):
            db.execute("BEGIN")
            try:
                for statement in _MIGRATIONS[target].split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                           (str(target),))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            logger.info("memory: migrated to schema version %d", target)

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._version(self._db)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- people -------------------------------------------------------------------------------
    def _person(self, row: tuple[int, str, int, float, float] | None) -> Person | None:
        return None if row is None else Person(row[0], row[1], bool(row[2]), row[3], row[4])

    def get_person(self, name: str) -> Person | None:
        with self._lock:
            return self._person(self._db.execute(
                "SELECT id, name, roast_ok, created, last_seen FROM people WHERE name = ?",
                (name,)).fetchone())

    def person_by_id(self, person_id: int) -> Person | None:
        with self._lock:
            return self._person(self._db.execute(
                "SELECT id, name, roast_ok, created, last_seen FROM people WHERE id = ?",
                (person_id,)).fetchone())

    def ensure_person(self, name: str) -> Person | None:
        """The person called *name* (made if new, ``last_seen`` updated). None if the name is not
        usable or ``MEMORY_MAX_PEOPLE`` people are already kept."""
        clean = clean_name(name)
        if clean is None:
            return None
        with self._lock:
            now = self._clock()
            existing = self.get_person(clean)
            if existing is not None:
                self._db.execute("UPDATE people SET last_seen = ? WHERE id = ?",
                                 (now, existing.id))
                return self.get_person(clean)
            count = self._db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            if count >= config.MEMORY_MAX_PEOPLE:
                logger.warning("memory: %d people are kept; not adding another", count)
                return None
            self._db.execute(
                "INSERT INTO people(name, roast_ok, created, last_seen) VALUES(?, 1, ?, ?)",
                (clean, now, now))
            return self.get_person(clean)

    def list_people(self) -> list[PersonSummary]:
        with self._lock:
            rows = self._db.execute(
                "SELECT p.id, p.name, (SELECT COUNT(*) FROM facts f WHERE f.person_id = p.id), "
                "p.last_seen, p.roast_ok FROM people p ORDER BY p.name COLLATE NOCASE").fetchall()
        return [PersonSummary(row[0], row[1], row[2], row[3], bool(row[4])) for row in rows]

    def set_roast_ok(self, person_id: int, ok: bool) -> bool:
        """Store whether Arya may roast this person on her own (planned auto-roast). False: nobody
        has that id. Roast MODE is not gated by it."""
        with self._lock:
            cursor = self._db.execute("UPDATE people SET roast_ok = ? WHERE id = ?",
                                      (int(ok), person_id))
            return cursor.rowcount > 0

    # -- facts --------------------------------------------------------------------------------
    def add_fact(self, person_id: int, text: str, source: str = "told") -> bool:
        """Keep a short fact. False if it is empty, too long, a duplicate (ignoring case and
        punctuation) or the person is unknown. The oldest fact goes when the person is at
        ``MEMORY_MAX_FACTS_PER_PERSON``."""
        text = " ".join(text.split())
        if source not in FACT_SOURCES or not text or len(text) > config.MEMORY_FACT_MAX_CHARS:
            return False
        key = _fact_key(text)
        with self._lock:
            if self.person_by_id(person_id) is None:
                return False
            if any(_fact_key(existing.text) == key for existing in self.facts(person_id)):
                return False
            self._db.execute("BEGIN")
            try:
                self._db.execute(
                    "INSERT INTO facts(person_id, text, source, created) VALUES(?, ?, ?, ?)",
                    (person_id, text, source, self._clock()))
                self._db.execute(
                    "DELETE FROM facts WHERE person_id = ? AND id NOT IN "
                    "(SELECT id FROM facts WHERE person_id = ? ORDER BY id DESC LIMIT ?)",
                    (person_id, person_id, config.MEMORY_MAX_FACTS_PER_PERSON))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
            return True

    def facts(self, person_id: int, limit: int | None = None) -> list[Fact]:
        """The person's facts, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text, source, created FROM facts WHERE person_id = ? "
                "ORDER BY id DESC LIMIT ?", (person_id, -1 if limit is None else limit)
            ).fetchall()
        return [Fact(*row) for row in rows]

    def fact_count(self, person_id: int) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM facts WHERE person_id = ?", (person_id,)).fetchone()[0])

    # -- summaries ----------------------------------------------------------------------------
    def add_summary(self, person_id: int, text: str) -> bool:
        text = " ".join(text.split())[:config.MEMORY_SUMMARY_MAX_CHARS]
        with self._lock:
            if not text or self.person_by_id(person_id) is None:
                return False
            self._db.execute("INSERT INTO summaries(person_id, text, created) VALUES(?, ?, ?)",
                             (person_id, text, self._clock()))
            return True

    def latest_summary(self, person_id: int) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT text FROM summaries WHERE person_id = ? ORDER BY id DESC LIMIT 1",
                (person_id,)).fetchone()
        return None if row is None else str(row[0])

    def summary_count(self, person_id: int) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM summaries WHERE person_id = ?", (person_id,)).fetchone()[0])

    # -- face embeddings (unused until face recognition is built; the bytes are never logged) ----
    def add_embedding(self, person_id: int, vector: bytes) -> bool:
        with self._lock:
            if not vector or self.person_by_id(person_id) is None:
                return False
            self._db.execute(
                "INSERT INTO face_embeddings(person_id, vector, created) VALUES(?, ?, ?)",
                (person_id, vector, self._clock()))
            return True

    def embedding_count(self, person_id: int) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE person_id = ?",
                (person_id,)).fetchone()[0])

    # -- forget -------------------------------------------------------------------------------
    def forget_person(self, person_id: int) -> bool:
        """Delete the person's facts, summaries, face embeddings and row, in ONE transaction."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for table in ("facts", "summaries", "face_embeddings"):
                    self._db.execute(f"DELETE FROM {table} WHERE person_id = ?", (person_id,))
                removed = self._db.execute("DELETE FROM people WHERE id = ?",
                                           (person_id,)).rowcount
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        logger.info("memory: forgot person %d", person_id)  # the id only, never the name
        return bool(removed)

    # -- meta ---------------------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


_KEY = re.compile(r"[^a-z0-9]+")


def _fact_key(text: str) -> str:
    return _KEY.sub(" ", text.lower()).strip()
