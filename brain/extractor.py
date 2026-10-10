"""Learn facts about a named speaker from a finished conversation (Step 13).

When the conversation has been quiet for ``EXTRACTOR_IDLE_S`` the extractor, in its own thread,
asks the chat backend for facts as JSON ``{"facts": ["...", ...]}``. Code, not the model, decides
what is kept:

- only for a NAMED speaker (never for ``guest``) and only if they said at least
  ``EXTRACTOR_MIN_TURNS`` things;
- the JSON must match the schema exactly (an object with one key, ``facts``, a list of strings;
  anything else is thrown away whole);
- each fact: a short plain sentence (at most ``MEMORY_FACT_MAX_CHARS``), none of the sensitive
  categories of the output check (``OutputCheck.fact_problem``: looks, body, skin, race, religion,
  caste, gender, sexuality, disability, family, health, and private data such as emails and long
  numbers), no duplicate of a fact already kept, at most ``EXTRACTOR_MAX_FACTS_PER_RUN`` per run
  and ``MEMORY_MAX_FACTS_PER_PERSON`` per person;
- it never blocks the voice loop (a thread; ``tick()`` is cheap), runs only while the robot is
  idle (``is_idle``), cancels nothing, and a failure is logged and ignored.

``extract_facts`` is the pure core (text in, validated facts out) and has the tests.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence

import config
from brain.chat import ChatBackend
from brain.memory import Memory
from brain.output_check import OutputCheck

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

EXTRACTOR_PROMPT = (
    "You read a short conversation and write down lasting facts the PERSON told about "
    "themselves (their likes, their job or studies, their plans, their pets, where they are "
    "from). Skip anything about looks, body, skin colour, race, religion, caste, gender, "
    "sexuality, disability, family or health, and skip private data. Write each fact as one short "
    "plain sentence about the person. Answer with JSON only, exactly like "
    '{"facts": ["Likes cricket.", "Studies robotics."]}. If there is nothing, answer {"facts": []}.'
)


def parse_facts_json(raw: str) -> list[str] | None:
    """The ``facts`` list of the model's answer, or None if the answer does not match the schema
    ``{"facts": [str, ...]}`` exactly (extra keys, wrong types, no JSON at all)."""
    text = _FENCE.sub("", raw.strip())
    candidates = [text]
    found = _JSON_OBJECT.search(text)
    if found is not None and found.group(0) != text:
        candidates.append(found.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if (isinstance(data, dict) and set(data) == {"facts"} and isinstance(data["facts"], list)
                and all(isinstance(item, str) for item in data["facts"])):
            return list(data["facts"])
        return None
    return None


def valid_facts(candidates: Sequence[str], existing: Sequence[str], check: OutputCheck,
                limit: int = config.EXTRACTOR_MAX_FACTS_PER_RUN) -> list[str]:
    """The candidates that pass every rule, in order, at most *limit*."""
    kept: list[str] = []
    seen = {_key(fact) for fact in existing}
    for candidate in candidates:
        fact = " ".join(candidate.split())
        if (not fact or len(fact) > config.MEMORY_FACT_MAX_CHARS or _key(fact) in seen
                or check.fact_problem(fact) is not None):
            continue
        kept.append(fact)
        seen.add(_key(fact))
        if len(kept) >= limit:
            break
    return kept


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def extract_facts(backend: ChatBackend, turns: Sequence[tuple[str, str]], existing: Sequence[str],
                  check: OutputCheck) -> list[str]:
    """Ask *backend* about *turns* and return the facts that pass the rules (may be empty).
    Raises whatever the backend raises; the caller logs it."""
    lines = []
    for user, reply in turns[-config.EXTRACTOR_MAX_TURNS:]:
        lines.append(f"Person: {user}")
        lines.append(f"Arya: {reply}")
    messages = [{"role": "system", "content": EXTRACTOR_PROMPT},
                {"role": "user", "content": "\n".join(lines)}]
    raw = "".join(backend.stream(messages))
    parsed = parse_facts_json(raw)
    if parsed is None:
        logger.info("extractor: the answer did not match the schema; nothing saved")
        return []
    return valid_facts(parsed, existing, check)


class MemoryExtractor:
    """Collects the conversation and, once it is idle, saves the facts (see the module doc).

    ``add_turn(speaker, user, reply)`` is called by the chat responder for each completed reply.
    ``tick()`` is called by the thread every ``EXTRACTOR_POLL_S`` (and by tests with a fake clock).
    """

    def __init__(self, memory: Memory, backend_source: Callable[[], ChatBackend | None],
                 check: OutputCheck | None = None, is_idle: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float] = time.monotonic,
                 idle_s: float | None = None,
                 on_saved: Callable[[int], None] | None = None) -> None:
        self.memory = memory
        self._on_saved = on_saved  # told how many facts a run saved (the phone page's counts)
        self._backend_source = backend_source
        self._check = check or OutputCheck()
        self._is_idle = is_idle
        self._clock = clock
        self._idle_s = config.EXTRACTOR_IDLE_S if idle_s is None else idle_s
        self._lock = threading.Lock()
        self._speaker: str | None = None
        self._turns: list[tuple[str, str]] = []
        self._closed: list[tuple[str, list[tuple[str, str]]]] = []  # talks of earlier speakers
        self._last_turn_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0  # for the tests and the reports
        self.saved = 0

    # -- fed by the chat responder ---------------------------------------------------------------
    def add_turn(self, speaker: str, user: str, reply: str) -> None:
        with self._lock:
            if speaker != self._speaker:  # someone else is talking: the old talk is closed first
                if self._speaker is not None and self._turns:
                    self._closed.append((self._speaker, self._turns))
                self._speaker, self._turns = speaker, []
            self._turns.append((user, reply))
            del self._turns[:-2 * config.EXTRACTOR_MAX_TURNS]  # bounded: only the latest are shown
            self._last_turn_at = self._clock()

    # -- the work ----------------------------------------------------------------------------------
    def tick(self) -> int:
        """Run an extraction if the conversation is idle. Returns how many facts were saved."""
        if not self._is_idle():
            return 0  # the robot is moving or talking: later
        with self._lock:
            closed, self._closed = self._closed, []
            # a talk of fewer than EXTRACTOR_MIN_TURNS is kept: the next turn joins it
            idle = (len(self._turns) >= config.EXTRACTOR_MIN_TURNS
                    and self._clock() - self._last_turn_at >= self._idle_s)
            speaker, turns = self._speaker, list(self._turns)
            if idle:
                self._turns = []
        saved = sum(self._run(who, talk) for who, talk in closed)
        if idle:
            saved += self._run(speaker, turns)
        return saved

    def _run(self, speaker: str | None, turns: Sequence[tuple[str, str]]) -> int:
        if (speaker is None or speaker == config.GUEST_NAME
                or len(turns) < config.EXTRACTOR_MIN_TURNS):
            return 0  # nothing is ever remembered about a guest
        backend = self._backend_source()
        if backend is None:
            return 0
        self.runs += 1
        try:
            person = self.memory.get_person(speaker)
            if person is None:
                return 0
            existing = [fact.text for fact in self.memory.facts(person.id)]
            facts = extract_facts(backend, turns, existing, self._check)
            saved = sum(1 for fact in facts if self.memory.add_fact(person.id, fact, "told"))
        except Exception as error:  # noqa: BLE001 - a failed extraction must never hurt anything
            logger.warning("extractor: failed (%s); ignored", type(error).__name__)
            return 0
        self.saved += saved
        logger.info("extractor: saved %d fact(s)", saved)  # the count only, never the text
        if saved and self._on_saved is not None:
            try:
                self._on_saved(saved)
            except Exception:  # noqa: BLE001
                logger.exception("extractor: on_saved failed")
        return saved

    def forget_pending(self, speaker: str) -> None:
        """Drop an unprocessed conversation of *speaker* (they asked to be forgotten)."""
        with self._lock:
            if self._speaker == speaker:
                self._turns = []
            self._closed = [(who, talk) for who, talk in self._closed if who != speaker]

    # -- the thread ------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="memory-extractor", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(config.EXTRACTOR_POLL_S):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("extractor tick failed")

    def shutdown(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)


_LIKE = re.compile(r"\bi (?:really )?(?:like|love|enjoy) ([a-z][a-z ]{1,40})", re.IGNORECASE)
_STUDY = re.compile(r"\bi (?:study|am studying) ([a-z][a-z ]{1,40})", re.IGNORECASE)
_WORK = re.compile(r"\bi work (?:as|at|in) ([a-z][a-z ]{1,40})", re.IGNORECASE)
_FROM = re.compile(r"\bi(?: am|m) from ([a-z][a-z ]{1,40})", re.IGNORECASE)
_STOP_AT = re.compile(r"\b(?:and|but|because|so|when|then|too|a lot)\b.*$", re.IGNORECASE)


class RuleExtractorChat:
    """A stand-in extractor model for development and tests: no LLM, a few regular expressions
    over the person's own lines ("I like cricket" -> "Likes cricket."). It speaks the same JSON the
    real extractor prompt asks for, so the whole path (schema check, rules, save) is exercised.
    Used with ``--chat fake``; the real model runs the real prompt."""

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        text = messages[-1]["content"]
        said = [line[len("Person:"):].strip() for line in text.splitlines()
                if line.startswith("Person:")]
        facts: list[str] = []
        for line in said:
            for pattern, template in ((_LIKE, "Likes {}."), (_STUDY, "Studies {}."),
                                      (_WORK, "Works {} {}."), (_FROM, "Is from {}.")):
                found = pattern.search(line)
                if found is None:
                    continue
                value = _STOP_AT.sub("", found.group(1)).strip(" .,!?")
                if not value:
                    continue
                if template.startswith("Works"):
                    kind = "at" if re.search(r"\bwork at\b", line, re.IGNORECASE) else (
                        "in" if re.search(r"\bwork in\b", line, re.IGNORECASE) else "as")
                    facts.append(template.format(kind, value))
                else:
                    facts.append(template.format(value))
        yield json.dumps({"facts": facts})

    def cancel(self) -> None:
        pass
