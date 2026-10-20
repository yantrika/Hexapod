"""SFace embeddings, matching and temporal voting. No storage and no names: the brain owns the
database and sends the known embeddings in (``SetKnownFaces``); vision only knows person ids."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

import config


def cosine_similarity(a: Any, b: Any) -> float:
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / norm) if norm > 0 else 0.0


def embedding_to_bytes(embedding: Any) -> bytes:
    return np.asarray(embedding, dtype=np.float32).ravel().tobytes()


def embedding_from_bytes(data: bytes) -> Any:
    return np.frombuffer(data, dtype=np.float32).copy()


def best_match(embedding: Any, known: Sequence[Any],
               threshold: float = config.VISION_MATCH_THRESHOLD) -> tuple[int | None, float]:
    """(index into *known*, cosine score) of the most similar known embedding, or (None, score)
    when even the best one is below *threshold* (or *known* is empty: (None, 0.0))."""
    best_index: int | None = None
    best_score = 0.0
    for index, candidate in enumerate(known):
        score = cosine_similarity(embedding, candidate)
        if best_index is None or score > best_score:
            best_index, best_score = index, score
    if best_index is None or best_score < threshold:
        return None, best_score
    return best_index, best_score


class FaceRecognizer:
    def __init__(self, model_path: Path = config.VISION_SFACE_FILE) -> None:
        import cv2

        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"{model_path} is missing: run python scripts/fetch_vision_models.py")
        self._recognizer = cv2.FaceRecognizerSF.create(str(model_path), "")

    def embed(self, frame: Any, face_row: Any) -> Any:
        """The 128-number SFace embedding of one face: *frame* is the BGR image the detector saw
        and *face_row* the ``Detection.row`` of that face (the landmarks align the crop)."""
        aligned = self._recognizer.alignCrop(frame, np.asarray(face_row, dtype=np.float32))
        return self._recognizer.feature(aligned).ravel().copy()

    def match(self, embedding: Any, known: Sequence[Any],
              threshold: float = config.VISION_MATCH_THRESHOLD) -> tuple[int | None, float]:
        return best_match(embedding, known, threshold)


class KnownFaces:
    """The embeddings vision matches against, each owned by a person id."""

    def __init__(self) -> None:
        self._owners: list[int] = []
        self._vectors: list[Any] = []

    def __len__(self) -> int:
        return len(self._vectors)

    def replace(self, faces: Iterable[tuple[int, Iterable[Any]]]) -> None:
        self._owners, self._vectors = [], []
        for person_id, vectors in faces:
            for vector in vectors:
                self._owners.append(person_id)
                self._vectors.append(np.asarray(vector, dtype=np.float32).ravel())

    def forget(self, person_id: int) -> None:
        kept = [(o, v) for o, v in zip(self._owners, self._vectors, strict=True)
                if o != person_id]
        self._owners = [o for o, _ in kept]
        self._vectors = [v for _, v in kept]

    def match(self, embedding: Any,
              threshold: float = config.VISION_MATCH_THRESHOLD) -> tuple[int | None, float]:
        """(person id, best cosine score), or (None, best score) below *threshold* (>= matches)."""
        index, score = best_match(embedding, self._vectors, threshold)
        return (None if index is None else self._owners[index]), score


@dataclass
class TrackIdentity:
    """What vision currently thinks of one track (person ids only, never a name)."""

    person_id: int | None = None
    score: float = 0.0  # the latest match score, whether or not it was a match
    unknown: bool = False  # person_unknown was sent for this track
    looks: int = 0  # good-quality looks since the track was last unnamed


class IdentityVoter:
    """Temporal voting: a person is named only when *needed* of the last *window* matches agree.
    A match against nobody is a vote for "unknown" and never helps a name."""

    def __init__(self, window: int = config.VISION_VOTE_WINDOW,
                 needed: int = config.VISION_VOTE_NEEDED) -> None:
        self._needed = min(needed, window)
        self._votes: deque[tuple[int | None, float]] = deque(maxlen=window)

    def clear(self) -> None:
        self._votes.clear()

    def add(self, person_id: int | None, score: float) -> tuple[int, float] | None:
        """Record one match result. (person id, mean score of its votes) once enough agree."""
        self._votes.append((person_id, score))
        counts = Counter(p for p, _ in self._votes if p is not None)
        if not counts:
            return None
        winner, count = counts.most_common(1)[0]
        if count < self._needed:
            return None
        scores = [sc for p, sc in self._votes if p == winner]
        return winner, sum(scores) / len(scores)
