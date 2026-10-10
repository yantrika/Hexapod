"""Arya: the persona, the speaker, the memory and the mode, in one object (Step 13, P1).

Arya never touches the body. She decides how replies TALK (mode, mood, who is speaking, what is
remembered) and answers the persona routes the router found ("my name is X", "forget me", "what do
you remember about me", "roast mode", "stop roasting"). Commands, the router and every safety rule
are identical in every mode: ``route()`` runs first and knows nothing about her.

Threads: the voice worker (routes, typed text), the chat reply thread (context, sentence check,
turns), the web server thread (mode, speaker, forget buttons) and the extractor thread all call
in, so the small state is behind one lock; the slow things (speech, the database) are never called
while it is held.

Privacy: "forget me" asks for a spoken yes first (``FORGET_CONFIRM_S``); any other utterance
cancels the question. The web page's Forget button has its own confirm dialog. Nothing here logs
what a person said or what is remembered (only counts and category names).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import config
from brain.context import ContextParts
from brain.memory import Memory, Person, clean_name
from brain.output_check import OutputCheck
from brain.persona import Mood, Persona, is_greeting_by_name
from brain.router import RouteResult, is_no, is_yes
from voice.tts import TtsError

logger = logging.getLogger(__name__)


class Speech(Protocol):
    """What Arya needs to talk: the logged playback."""

    def say(self, text: str) -> int: ...

    def say_phrase(self, name: str) -> int: ...


@dataclass(frozen=True)
class PersonaState:
    """What the phone page shows."""

    mode: str
    speaker: str
    mood: str
    people: tuple[tuple[int, str, int, bool], ...]  # (id, name, number of facts, roast_ok)
    memory: bool  # False: the database could not be opened

    def as_event(self) -> dict[str, Any]:
        return {"type": "persona", "mode": self.mode, "modes": list(config.PERSONA_MODES),
                "speaker": self.speaker, "mood": self.mood, "memory": self.memory,
                "people": [{"id": i, "name": n, "facts": f, "roast_ok": r}
                           for i, n, f, r in self.people]}


class Arya:
    def __init__(
        self,
        persona: Persona,
        memory: Memory | None,
        speech: Speech,
        check: OutputCheck | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[PersonaState], None] | None = None,
    ) -> None:
        self.persona = persona
        self.memory = memory
        self.speech = speech
        self.check = check or OutputCheck()
        self.mood = Mood(clock)
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        self._mode = config.PERSONA_DEFAULT_MODE
        self._speaker = config.GUEST_NAME
        self._forget_until: float | None = None
        # the extractor drops that person's unprocessed talk
        self.on_forgotten: Callable[[str], None] | None = None
        saved = memory.get_meta("mode") if memory is not None else None
        if saved in config.PERSONA_MODES:
            self._mode = str(saved)

    # -- state ---------------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def speaker(self) -> str:
        return self._speaker

    def _speaker_person(self) -> Person | None:
        if self.memory is None or self._speaker == config.GUEST_NAME:
            return None
        return self.memory.get_person(self._speaker)

    def state(self) -> PersonaState:
        people: tuple[tuple[int, str, int, bool], ...] = ()
        if self.memory is not None:
            people = tuple((p.id, p.name, p.facts, p.roast_ok)
                           for p in self.memory.list_people())
        return PersonaState(self._mode, self._speaker, self.mood.state, people,
                            self.memory is not None)

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self.state())
            except Exception:  # noqa: BLE001 - the page is a viewer; it must not break talking
                logger.exception("persona change listener failed")

    def _say(self, text: str) -> None:
        try:
            self.speech.say(text)
        except TtsError as error:
            logger.error("could not say it: %s", error)

    def _phrase(self, name: str) -> None:
        try:
            self.speech.say_phrase(name)
        except TtsError as error:
            logger.error("could not say %r: %s", name, error)

    # -- changes (voice and the web page) --------------------------------------------------------
    def set_mode(self, mode: str, announce: bool = True) -> bool:
        """Switch how Arya talks. False for an unknown mode."""
        if mode not in config.PERSONA_MODES:
            return False
        self._mode = mode
        if self.memory is not None:
            try:
                self.memory.set_meta("mode", mode)
            except Exception:  # noqa: BLE001
                logger.exception("could not remember the mode")
        logger.info("persona: mode is now %s", mode)
        if announce:
            self._phrase(f"mode_{mode}")
        self._changed()
        return True

    def set_speaker(self, name: str, announce: bool = True, create: bool = True) -> bool:
        """Say who is talking. ``guest`` clears it. With ``create`` a new name is added (voice:
        "my name is X"); the web page may only pick someone who exists. False if refused."""
        if name.lower() == config.GUEST_NAME:
            self._speaker = config.GUEST_NAME
            self._changed()
            return True
        clean = clean_name(name)
        if clean is None or self.check.fact_problem(clean) is not None:
            if announce:
                self._say("I can't use that name.")
            return False
        if self.memory is None:
            self._speaker = clean  # no database: the name works for this session only
            if announce:
                self._say(f"Nice to meet you, {clean}. I can't remember things right now.")
            self._changed()
            return True
        existing = self.memory.get_person(clean)
        if existing is None and not create:
            return False
        person = self.memory.ensure_person(clean)
        if person is None:
            if announce:
                self._phrase("memory_off")
            return False
        self._speaker = person.name
        if announce:
            self._say(f"Welcome back, {person.name}." if existing is not None
                      else f"Nice to meet you, {person.name}.")
        self._changed()
        return True

    def request_forget(self) -> None:
        """"forget me": ask for a yes first."""
        if self._speaker == config.GUEST_NAME or self.memory is None:
            self._phrase("remember_nothing")
            return
        with self._lock:
            self._forget_until = self._clock() + config.FORGET_CONFIRM_S
        self._phrase("say_yes_to_confirm")

    def forget_person(self, person_id: int) -> bool:
        """Delete a person (facts, summaries, face embeddings, row). If they are the speaker,
        the speaker becomes guest. Used by the voice confirmation and the web Forget button."""
        if self.memory is None:
            return False
        person = self.memory.person_by_id(person_id)
        if person is None:
            return False
        removed = self.memory.forget_person(person_id)
        if self._speaker == person.name:
            self._speaker = config.GUEST_NAME
        if self.on_forgotten is not None:
            self.on_forgotten(person.name)
        self._changed()
        return removed

    def set_roast_ok(self, person_id: int, ok: bool) -> bool:
        """The page's roast_ok toggle. It does not change what roast MODE does (that applies to
        everyone who is talking to Arya); it is what the later auto-roast (P5) asks first."""
        if self.memory is None or not self.memory.set_roast_ok(person_id, ok):
            return False
        self._changed()
        return True

    def recall(self) -> None:
        """"what do you remember about me": code builds the sentence from the stored facts."""
        person = self._speaker_person()
        if person is None or self.memory is None:
            self._phrase("remember_nothing")
            return
        facts = [fact.text.rstrip(".") for fact in reversed(
            self.memory.facts(person.id, config.CONTEXT_MAX_FACTS))]
        if not facts:
            self._phrase("remember_nothing")
            return
        self._say(f"Here is what I remember, {person.name}. " + ". ".join(facts) + ".")

    # -- the voice loop's hooks ------------------------------------------------------------------
    def consume(self, text: str) -> bool:
        """A pending "say yes to confirm" is answered here. True if *text* was the answer (the
        loop does nothing more with it). Anything else cancels the question and goes on."""
        with self._lock:
            until, self._forget_until = self._forget_until, None
        if until is None:
            return False
        if self._clock() > until:
            return False
        if is_yes(text):
            person = self._speaker_person()
            if person is not None and self.forget_person(person.id):
                self._phrase("forget_done")
            else:
                self._phrase("remember_nothing")
            return True
        if is_no(text):
            self._phrase("forget_cancelled")
            return True
        return False

    def cancel_pending(self) -> None:
        """Any pending "say yes to confirm" is dropped (a stop, or anything else was said)."""
        with self._lock:
            self._forget_until = None

    def note(self, result: RouteResult) -> None:
        """Mood events from an utterance."""
        self.mood.heard()
        if is_greeting_by_name(result.text):
            self.mood.greeted()
        if result.kind == "command":
            self.mood.command()

    def handle(self, result: RouteResult) -> None:
        """Act on a ``persona`` route."""
        if result.action == "set_mode":
            self.set_mode(str(result.params.get("mode", "")))
        elif result.action == "set_speaker":
            self.set_speaker(str(result.params.get("name", "")))
        elif result.action == "forget_me":
            self.request_forget()
        elif result.action == "recall_me":
            self.recall()
        else:
            logger.warning("persona: unknown action %r", result.action)

    # -- the chat responder's hooks ----------------------------------------------------------------
    def context_parts(self, user_text: str) -> ContextParts:
        """What goes into this reply's prompt (the builder trims it)."""
        straight = (self.persona.wants_straight_answer(user_text)
                    or self.persona.is_help_question(user_text))
        system = self.persona.system_prompt(self._mode, self.mood.state, straight)
        facts: list[str] = []
        summary = None
        person = self._speaker_person()
        if person is not None and self.memory is not None:
            facts = [fact.text for fact in self.memory.facts(person.id, config.CONTEXT_MAX_FACTS)]
            summary = self.memory.latest_summary(person.id)
        return ContextParts(system, self._speaker, facts, summary)

    def sentence_check(self, sentence: str, user_text: str) -> str | None:
        """The category that makes *sentence* unfit to speak, or None."""
        if not config.PERSONA_OUTPUT_CHECK:
            return None
        verdict = self.check.check(sentence, self.persona.is_help_question(user_text))
        return None if verdict.ok else verdict.category
