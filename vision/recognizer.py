"""SFace embeddings and matching, for P4. No storage, no enrolment, no names: the caller owns
the known embeddings and passes them in. Nothing here is called by the vision process yet."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

import config


def cosine_similarity(a: Any, b: Any) -> float:
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / norm) if norm > 0 else 0.0


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
