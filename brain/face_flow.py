"""The spoken side of face enrolment: ask a name, confirm it, ask for consent, collect samples.

    ask_name -> "What is your name?" -> "Did you say Priya?" -> "May I remember your face?" -> yes
        -> Enroll(track, person) to the vision process -> EnrollResult -> embeddings stored

Nothing is stored before the last yes: the person row is made, the consent recorded and the
vision process asked to collect samples only after the person said yes to remembering their face
(``Memory`` refuses embeddings without that consent). A name heard once is often another word, so
it is always read back. Anything the person says that is not an answer (a command, a long
sentence, "no") ends the question and goes on to the router as usual; a stop always ends it
(``cancel``). The flow never moves the robot and never logs a name or an utterance.

Threads: ``consume`` runs on the voice worker, ``tick`` / ``on_enroll_result`` / ``ask_name`` on the
vision link thread, so the small state is behind one lock; speech and the database are never
called with it held.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import config
from brain.memory import Memory, clean_name
from brain.router import is_no, is_yes, normalize, remove_fillers, route
from vision.events import (
    ENROLL_INCONSISTENT,
    ENROLL_LOW_QUALITY,
    ENROLL_MULTIPLE_FACES,
    ENROLL_PERSON_LEFT,
    ENROLL_TIMEOUT,
    CancelEnroll,
    Enroll,
    EnrollResult,
)
from vision.recognizer import embedding_to_bytes
from voice.tts import TtsError

logger = logging.getLogger(__name__)

IDLE, ASK_NAME, CONFIRM_NAME, ASK_CONSENT, ENROLLING = (
    "idle", "ask_name", "confirm_name", "ask_consent", "enrolling")

_NOT_NAMES = frozenset((
    "hello", "hi", "hey", "thanks", "thank", "you", "what", "why", "who", "how", "yes", "no",
    "nope", "yeah", "ok", "okay", "sure", "maybe", "none", "nothing", "skip", "cancel", "dont",
    "never", "mind", "idk", "um", "uh", "hmm", "its", "it", "this", "is", "my", "name", "me",
))
_NAME_LEAD_INS = ("its ", "it is ", "this is ", "they call me ", "everyone calls me ")


class Speech(Protocol):
    def say(self, text: str) -> int: ...

    def say_phrase(self, name: str) -> int: ...


def name_from_reply(text: str) -> str | None:
    """The name in a reply to "what is your name?" ("Priya", "my name is Priya", "it's Priya"), or
    None. The result is not checked against the word lists; the caller does that."""
    result = route(text)
    if result.kind == "persona" and result.action == "set_speaker":
        return str(result.params.get("name", "")) or None
    if result.kind != "chat":
        return None
    words = remove_fillers(normalize(text))
    for lead in _NAME_LEAD_INS:
        if words.startswith(lead):
            words = words[len(lead):]
            break
    parts = words.split()
    if not 1 <= len(parts) <= 2 or any(
            part in config.ROUTER_NAME_STOPWORDS or part in _NOT_NAMES for part in parts):
        return None
    return clean_name(words)


@dataclass
class _Flow:
    state: str = IDLE
    track_id: int | None = None
    name: str = ""
    tries: int = 0
    deadline: float = 0.0
    person_id: int | None = None
    had_face: bool = False  # the person already had samples before this enrolment started


class FaceFlow:
    def __init__(
        self,
        memory: Memory | None,
        speech: Speech,
        send: Callable[[Any], bool],
        visible_track: Callable[[], int | None],
        name_ok: Callable[[str], bool],
        on_known: Callable[[str], None],
        on_faces_changed: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._memory = memory
        self._speech = speech
        self._send = send  # a command to the vision process; False when its queue is full
        self._visible_track = visible_track  # the one face in view, else None
        self._name_ok = name_ok  # the output check's verdict on a name
        self._on_known = on_known  # Arya's speaker becomes this person
        self._on_faces_changed = on_faces_changed  # tell vision who is known now
        self._clock = clock
        self._lock = threading.Lock()
        self._flow = _Flow()

    # -- state ---------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._flow.state

    @property
    def active(self) -> bool:
        return self._flow.state != IDLE

    def _say(self, text: str) -> None:
        try:
            self._speech.say(text)
        except TtsError as error:
            logger.error("could not say it: %s", error)

    def _set(self, state: str, **fields: Any) -> None:
        with self._lock:
            self._flow = _Flow(state, deadline=self._clock() + config.FACE_FLOW_ANSWER_S, **fields)

    def _end(self) -> None:
        with self._lock:
            self._flow = _Flow()

    # -- starting ------------------------------------------------------------------------------
    def ask_name(self, track_id: int, opening: str = "Hi! I don't think we have met. "
                 "What is your name?") -> bool:
        """Ask the person on *track_id* who they are. False when nothing can be remembered (no
        database) or another question is open."""
        if self._memory is None or self.active:
            return False
        self._set(ASK_NAME, track_id=track_id)
        self._say(opening)
        return True

    def remember_my_face(self, speaker: str) -> None:
        """"remember my face": a known speaker is asked for consent; a guest is asked their name."""
        if self._memory is None:
            self._speech.say_phrase("memory_off")
            return
        track = self._visible_track()
        if track is None:
            self._say("I need to see exactly one face to do that.")
            return
        if self.active:
            self.cancel()
        if speaker == config.GUEST_NAME:
            self.ask_name(track, "Sure. What is your name?")
            return
        self._set(ASK_CONSENT, track_id=track, name=speaker)
        self._say(f"Okay {speaker}. I keep numbers from your face, never a picture. "
                  "May I remember it? Say yes or no.")

    def forget_my_face(self, speaker: str) -> None:
        memory = self._memory
        person = memory.get_person(speaker) if memory is not None else None
        if memory is None or person is None or not (
                person.face_consent or memory.embedding_count(person.id)):
            self._say("I don't have your face.")
            return
        self.cancel()
        memory.forget_face(person.id)
        self._on_faces_changed()
        self._say("Okay, I have forgotten your face.")

    # -- the answers ---------------------------------------------------------------------------
    def consume(self, text: str) -> bool:
        """True if *text* was the answer to the open question (nothing more happens with it)."""
        with self._lock:
            flow = self._flow
        if flow.state == IDLE or flow.state == ENROLLING:
            if flow.state == ENROLLING and is_no(text):
                self.cancel()
                self._say("Okay, I stopped.")
                return True
            return False
        if self._clock() > flow.deadline:
            self._end()
            return False
        if flow.state == ASK_NAME:
            return self._answer_name(text, flow)
        if flow.state == CONFIRM_NAME:
            return self._answer_confirm(text, flow)
        return self._answer_consent(text, flow)

    def _answer_name(self, text: str, flow: _Flow) -> bool:
        if is_no(text):
            self._end()
            self._say("Okay, no problem.")
            return True
        name = name_from_reply(text)
        if name is None:
            self._end()  # not a name: let the router have it
            return False
        return self._propose(name, flow)

    def _propose(self, name: str, flow: _Flow) -> bool:
        if not self._name_ok(name):
            self._end()
            self._say("I can't use that name.")
            return True
        self._set(CONFIRM_NAME, track_id=flow.track_id, name=name, tries=flow.tries + 1)
        self._say(f"Did you say {name}?")
        return True

    def _answer_confirm(self, text: str, flow: _Flow) -> bool:
        if is_yes(text):
            self._set(ASK_CONSENT, track_id=flow.track_id, name=flow.name)
            self._say(f"Nice to meet you, {flow.name}. May I remember your face, so I know you "
                      "next time? I keep numbers, never a picture. Say yes or no.")
            return True
        other = None if is_no(text) else name_from_reply(text)
        if other is not None and other != flow.name:
            return self._propose(other, flow)
        if flow.tries >= config.FACE_FLOW_NAME_TRIES:
            self._end()
            self._say("Sorry, I keep getting it wrong. You can type your name on the phone page.")
            return True
        self._set(ASK_NAME, track_id=flow.track_id, tries=flow.tries)
        self._say("Sorry. Please say just your name again.")
        return True

    def _answer_consent(self, text: str, flow: _Flow) -> bool:
        if is_yes(text):
            return self._start_enrolment(flow)
        if is_no(text):
            self._end()
            self._on_known(flow.name)  # the name is kept (they said it); the face is not
            self._say("Okay, I won't remember your face.")
            return True
        self._end()  # anything else: the question is dropped, the router gets the sentence
        return False

    # -- enrolment -----------------------------------------------------------------------------
    def _start_enrolment(self, flow: _Flow) -> bool:
        memory = self._memory
        person = memory.ensure_person(flow.name) if memory is not None else None
        if memory is None or person is None:
            self._end()
            self._speech.say_phrase("memory_off")
            return True
        track = self._visible_track() or flow.track_id
        if track is None:
            self._end()
            self._say("I can't see you any more. Say remember my face when you are in front of me.")
            return True
        had_face = memory.embedding_count(person.id) > 0
        memory.set_face_consent(person.id, True)
        if not self._send(Enroll(track, person.id)):
            self._abort_enrolment(person.id, had_face)
            self._say("My eyes are busy. Please try again in a moment.")
            return True
        with self._lock:
            self._flow = _Flow(
                ENROLLING, track_id=track, name=flow.name, person_id=person.id,
                deadline=self._clock() + config.VISION_ENROLL_TIMEOUT_S
                + config.FACE_FLOW_ENROLL_MARGIN_S, had_face=had_face)
        self._say("Thank you. Please look at me and hold still for a few seconds.")
        return True

    def _abort_enrolment(self, person_id: int, had_face: bool) -> None:
        """A failed enrolment must not leave a "yes" behind when nothing was stored."""
        if not had_face and self._memory is not None:
            self._memory.set_face_consent(person_id, False)
        self._end()

    def on_enroll_result(self, result: EnrollResult) -> None:
        with self._lock:
            flow = self._flow
        if flow.state != ENROLLING or result.person_id != flow.person_id:
            return
        memory = self._memory
        assert memory is not None and flow.person_id is not None
        if result.ok and memory.replace_embeddings(
                flow.person_id, [embedding_to_bytes(vector) for vector in result.embeddings]):
            self._end()
            self._on_faces_changed()
            self._on_known(flow.name)
            self._say(f"Got it, {flow.name}. I will know you next time.")
            return
        self._abort_enrolment(flow.person_id, flow.had_face)
        self._say(_failure_sentence(result.reason))

    def tick(self) -> None:
        """Drop an open question nobody answered; give up on an enrolment that never ended."""
        with self._lock:
            flow = self._flow
        if flow.state == IDLE or self._clock() <= flow.deadline:
            return
        if flow.state == ENROLLING and flow.person_id is not None:
            self._send(CancelEnroll())
            self._abort_enrolment(flow.person_id, flow.had_face)
            self._say(_failure_sentence(ENROLL_TIMEOUT))
        else:
            self._end()

    def cancel(self) -> None:
        """A stop, or a new request: nothing stays open. An enrolment under way is cancelled."""
        with self._lock:
            flow, self._flow = self._flow, _Flow()
        if flow.state == ENROLLING and flow.person_id is not None:
            self._send(CancelEnroll())
            if not flow.had_face and self._memory is not None:
                self._memory.set_face_consent(flow.person_id, False)


def _failure_sentence(reason: str | None) -> str:
    if reason == ENROLL_MULTIPLE_FACES:
        return "I can see more than one person. Say remember my face when it is just you."
    if reason == ENROLL_PERSON_LEFT:
        return "You moved out of my view. Say remember my face to try again."
    if reason in (ENROLL_LOW_QUALITY, ENROLL_INCONSISTENT, ENROLL_TIMEOUT):
        return "I could not get a good look. Face me in good light and say remember my face."
    return "I could not learn your face this time."
