#!/usr/bin/env python3
"""Try face recognition and consent-based enrolment on a webcam. No robot, no main.py.

    python scripts/face_check.py enroll Anna      asks "Remember Anna's face? (yes/no)", only on yes
                                                  collects samples (live preview) and stores them
    python scripts/face_check.py watch            live preview: name or unknown, score, track id
    python scripts/face_check.py list             people with a face enrolled (no embeddings shown)
    python scripts/face_check.py forget Anna      deletes Anna's face after a yes

Options (before or after the command): --db PATH (default data/memory/face_check.db, never the real
memory), --camera N, --fps, --width, --score, --seconds, --no-preview, --no-recognition (watch:
detection only, to compare the CPU numbers printed every 5 s).

The vision loop runs in this process (the same VisionLoop the robot uses), so the preview can show
names; names come from the database here, vision itself only knows person ids. Frames and crops
exist only in memory: nothing is saved but the embeddings (128 numbers per sample) in the database.
Needs requirements-vision.txt and python scripts/fetch_vision_models.py. q or Esc ends a preview.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from brain.memory import Memory, clean_name  # noqa: E402
from vision.detector import Detection  # noqa: E402
from vision.events import (  # noqa: E402
    ENROLL_BUSY,
    ENROLL_CANCELLED,
    ENROLL_INCONSISTENT,
    ENROLL_LOW_QUALITY,
    ENROLL_MULTIPLE_FACES,
    ENROLL_NO_RECOGNIZER,
    ENROLL_PERSON_LEFT,
    ENROLL_TIMEOUT,
    ENROLL_UNKNOWN_TRACK,
    FACE_BEARING,
    Enroll,
    EnrollResult,
    SetKnownFaces,
    VisionEvent,
)
from vision.recognizer import TrackIdentity, embedding_from_bytes, embedding_to_bytes  # noqa: E402
from vision.stats import VisionStats  # noqa: E402
from vision.tracker import VisibleTrack  # noqa: E402

DEFAULT_DB = config.DATA_DIR / "memory" / "face_check.db"
NO_FACE = "no_face"
REASONS = {
    ENROLL_TIMEOUT: "not enough good samples in time: face the camera, in good light",
    ENROLL_LOW_QUALITY: "the face was too small, turned away or blurry: move closer, look at the "
                        "camera, hold still",
    ENROLL_PERSON_LEFT: "the face left the picture",
    ENROLL_MULTIPLE_FACES: "more than one face was in view: only the person enrolling should be",
    ENROLL_INCONSISTENT: "the samples did not look like one person: try again, alone",
    ENROLL_CANCELLED: "cancelled",
    ENROLL_BUSY: "another enrolment is running",
    ENROLL_UNKNOWN_TRACK: "the face was not tracked yet",
    ENROLL_NO_RECOGNIZER: "the SFace model is missing: run python scripts/fetch_vision_models.py",
    NO_FACE: "no face appeared in front of the camera",
}


def camera_source(text: str) -> int | str:
    return int(text) if text.isdigit() else text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("enroll", "watch", "list", "forget"))
    parser.add_argument("name", nargs="*", help="the person (enroll, forget)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="database file (default %(default)s)")
    parser.add_argument("--camera", type=camera_source, default=config.VISION_CAMERA_INDEX)
    parser.add_argument("--fps", type=float, default=6.0, help="frames per second (default 6)")
    parser.add_argument("--width", type=int, default=config.VISION_DETECT_WIDTH,
                        help="detection width in pixels (default %(default)s)")
    parser.add_argument("--score", type=float, default=config.VISION_SCORE_THRESHOLD,
                        help="YuNet score threshold (default %(default)s)")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="watch: stop after this long; enroll: give up waiting for a face "
                             "(default 0: until q, 60 s for enroll)")
    parser.add_argument("--no-preview", action="store_true", help="never show a window")
    parser.add_argument("--no-recognition", action="store_true",
                        help="watch: detection and tracking only")
    return parser


def known_faces(memory: Memory) -> SetKnownFaces:
    """The command that tells vision who is enrolled (only people who gave consent)."""
    return SetKnownFaces(tuple(
        (person_id, tuple(embedding_from_bytes(blob) for blob in blobs))
        for person_id, blobs in memory.all_embeddings().items()))


class Session:
    """The brain's side of one run: the command queue, the events, and the preview window."""

    def __init__(self, memory: Memory, args: argparse.Namespace,
                 enroll_person_id: int | None = None) -> None:
        self.memory = memory
        self.args = args
        self.enroll_person_id = enroll_person_id
        self.commands: queue.Queue[Any] = queue.Queue()
        self.stop = threading.Event()
        self.result: EnrollResult | None = None
        self.started = time.monotonic()
        self._enroll_sent = False
        self._names: dict[int, str] = {}
        self._headless = bool(args.no_preview)
        self.commands.put(known_faces(memory))

    def next_command(self) -> Any:
        try:
            return self.commands.get_nowait()
        except queue.Empty:
            return None

    def name_of(self, person_id: int | None) -> str:
        if person_id is None:
            return "unknown"
        if person_id not in self._names:
            person = self.memory.person_by_id(person_id)
            self._names[person_id] = person.name if person else f"person {person_id}"
        return self._names[person_id]

    def stamp(self) -> str:
        return f"{time.monotonic() - self.started:7.1f}s"

    def on_event(self, event: VisionEvent) -> None:
        if event.kind == FACE_BEARING:
            return
        who = f"  {self.name_of(event.person_id)}" if event.person_id is not None else ""
        score = f"  score {event.score:.2f}" if event.score is not None else ""
        print(f"{self.stamp()}  {event.kind}(track {event.track_id}){who}{score}")

    def on_reply(self, result: EnrollResult) -> None:
        self.result = result
        print(f"{self.stamp()}  {result}")
        self.stop.set()

    def on_stats(self, stats: VisionStats) -> None:
        print(f"{self.stamp()}  SUMMARY {stats.line()}")

    def status(self) -> str:
        if self.enroll_person_id is None:
            return "watching"
        return ("collecting samples: look at the camera" if self._enroll_sent
                else "waiting for one face in view")

    def on_frame(self, small: Any, detections: list[Detection], visible: list[VisibleTrack],
                 identities: dict[int, TrackIdentity]) -> None:
        enrolling = self.enroll_person_id is not None
        if enrolling and not self._enroll_sent and len(visible) == 1 and len(detections) == 1:
            self._enroll_sent = True
            self.commands.put(Enroll(visible[0].track_id, self.enroll_person_id or 0))
        limit = self.args.seconds or (60.0 if enrolling else 0.0)
        waiting = not (enrolling and self._enroll_sent)  # a started enrolment ends on its own
        if limit and waiting and time.monotonic() - self.started >= limit:
            self.stop.set()
        if not self._headless:
            self._draw(small, detections, visible, identities)

    def _draw(self, small: Any, detections: list[Detection], visible: list[VisibleTrack],
              identities: dict[int, TrackIdentity]) -> None:
        import cv2

        canvas = cv2.resize(small, None, fx=2.0, fy=2.0)
        for detection in detections:
            x, y, w, h = (int(v * 2) for v in detection.box)
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (128, 128, 128), 1)
        for track in visible:
            identity = identities.get(track.track_id) or TrackIdentity()
            known = identity.person_id is not None
            name = (self.name_of(identity.person_id) if known
                    else "unknown" if identity.unknown else "checking")
            label = f"#{track.track_id} {name}"
            if known or identity.unknown or identity.score > 0:
                label += f" {identity.score:.2f}"
            x, y, w, h = (int(v * 2) for v in track.box)
            colour = (0, 200, 0) if known else (0, 165, 255)
            cv2.rectangle(canvas, (x, y), (x + w, y + h), colour, 2)
            cv2.putText(canvas, label, (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        colour, 1)
        cv2.putText(canvas, self.status(), (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255), 1)
        try:
            cv2.imshow("hexa face check (q or Esc to quit)", canvas)
            key = cv2.waitKey(1) & 0xFF
        except cv2.error as error:  # no display: carry on without the window
            print(f"preview unavailable: {error}", file=sys.stderr)
            self._headless = True
            return
        if key in (ord("q"), 27):
            self.stop.set()


def run_session(session: Session, recognize: bool = True) -> None:
    """Run the real vision loop (webcam, YuNet, tracker, SFace) in this thread until stop."""
    import cv2

    from vision.camera import Camera
    from vision.detector import FaceDetector
    from vision.process import VisionLoop, VisionOptions
    from vision.recognizer import FaceRecognizer
    from vision.tracker import IouTracker

    args = session.args
    cv2.setNumThreads(config.VISION_CV_THREADS)
    options = VisionOptions(source=args.camera, fps=args.fps, detect_width=args.width,
                            stats_s=config.VISION_STATS_S)
    camera = Camera(args.camera)
    loop = VisionLoop(
        camera, FaceDetector(detect_width=args.width, score_threshold=args.score), IouTracker(),
        emit=session.on_event, options=options, stop_event=session.stop,
        pause_event=threading.Event(), stats_sink=session.on_stats, preview=session.on_frame,
        recognizer=FaceRecognizer() if recognize else None, commands=session.next_command,
        reply=session.on_reply)
    try:
        loop.run()
    finally:
        camera.release()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass


def collect_face(args: argparse.Namespace, memory: Memory, person_id: int) -> EnrollResult:
    """Run the webcam until the enrolment finishes (the one place enrol touches the camera)."""
    session = Session(memory, args, enroll_person_id=person_id)
    run_session(session)
    return session.result or EnrollResult(person_id, (), NO_FACE)


def say_yes(answer: str) -> bool:
    return answer.strip().lower() == "yes"


def cmd_enroll(args: argparse.Namespace, memory: Memory, ask: Callable[[str], str],
               collect: Callable[[argparse.Namespace, Memory, int], EnrollResult]) -> int:
    name = clean_name(" ".join(args.name))
    if name is None:
        print("Give a name of one or two words, letters only (not 'guest').", file=sys.stderr)
        return 2
    if not say_yes(ask(f"Remember {name}'s face? (yes/no) ")):
        print("OK. Nothing was stored.")
        return 0
    person = memory.ensure_person(name)
    if person is None:
        print("Cannot add another person: the memory is full.", file=sys.stderr)
        return 1
    had_face = memory.embedding_count(person.id) > 0
    memory.set_face_consent(person.id, True)
    print("Look at the camera, alone in the picture "
          f"(up to {config.VISION_ENROLL_TIMEOUT_S:.0f} s once your face is found).")
    try:
        result = collect(args, memory, person.id)
    except (FileNotFoundError, ImportError, RuntimeError) as error:
        result = EnrollResult(person.id, (), str(error))
    if result.ok and memory.replace_embeddings(
            person.id, [embedding_to_bytes(e) for e in result.embeddings]):
        print(f"Stored {len(result.embeddings)} face samples for {name}. No picture was saved.")
        return 0
    if not had_face:
        memory.set_face_consent(person.id, False)  # nothing was stored: do not keep a "yes"
    print(f"Enrolment failed: {REASONS.get(result.reason or '', result.reason)}", file=sys.stderr)
    return 1


def cmd_watch(args: argparse.Namespace, memory: Memory) -> int:
    session = Session(memory, args)
    known = len(memory.all_embeddings())
    print(f"watching ({known} enrolled people); q or Esc ends it")
    run_session(session, recognize=not args.no_recognition)
    return 0


def cmd_list(memory: Memory) -> int:
    enrolled = [p for p in memory.list_people() if p.faces]
    if not enrolled:
        print("No face is enrolled.")
    for summary in enrolled:
        person = memory.person_by_id(summary.id)
        since = person.face_consent_at if person else None
        stamp = datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M") if since else "?"
        print(f"{summary.name}: {summary.faces} samples, consent given {stamp}")
    return 0


def cmd_forget(args: argparse.Namespace, memory: Memory, ask: Callable[[str], str]) -> int:
    typed = " ".join(args.name)
    person = memory.get_person(clean_name(typed) or typed)
    if person is None or not (person.face_consent or memory.embedding_count(person.id)):
        print(f"No face is stored for {typed!r}.")
        return 1
    if not say_yes(ask(f"Delete {person.name}'s face? (yes/no) ")):
        print("OK. Nothing was deleted.")
        return 0
    memory.forget_face(person.id)
    print(f"Deleted {person.name}'s face.")
    return 0


def main(argv: list[str] | None = None, ask: Callable[[str], str] = input,
         collect: Callable[[argparse.Namespace, Memory, int], EnrollResult] = collect_face) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("enroll", "forget") and not args.name:
        print(f"{args.command} needs a name", file=sys.stderr)
        return 2
    if args.db.resolve() == config.MEMORY_DB_PATH.resolve():
        print("This script never uses the real memory database; pass another --db.",
              file=sys.stderr)
        return 2
    memory = Memory(args.db)
    try:
        if args.command == "enroll":
            return cmd_enroll(args, memory, ask, collect)
        if args.command == "watch":
            try:
                return cmd_watch(args, memory)
            except (FileNotFoundError, ImportError) as error:
                print(error, file=sys.stderr)
                return 2
        if args.command == "list":
            return cmd_list(memory)
        return cmd_forget(args, memory, ask)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        memory.close()


if __name__ == "__main__":
    sys.exit(main())
