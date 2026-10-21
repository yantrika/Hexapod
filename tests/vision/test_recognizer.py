"""The embedding match maths of the P4 stub (no model needed)."""

from __future__ import annotations

import numpy as np

from vision.recognizer import best_match, cosine_similarity


def test_cosine_of_same_opposite_and_orthogonal_vectors() -> None:
    a = np.array([1.0, 2.0, 3.0])
    assert abs(cosine_similarity(a, a * 5) - 1.0) < 1e-6
    assert abs(cosine_similarity(a, -a) + 1.0) < 1e-6
    assert abs(cosine_similarity([1, 0], [0, 1])) < 1e-6
    assert cosine_similarity([0, 0], [1, 1]) == 0.0


def test_best_match_picks_the_closest_known_embedding() -> None:
    known = [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.1])]
    index, score = best_match(np.array([0.0, 1.0, 0.0]), known, threshold=0.5)
    assert index == 1 and score > 0.99


def test_below_the_threshold_is_no_match_and_empty_is_none() -> None:
    index, score = best_match(np.array([1.0, 0.0]), [np.array([0.0, 1.0])], threshold=0.5)
    assert index is None and score < 0.5
    assert best_match(np.array([1.0, 0.0]), [], threshold=0.5) == (None, 0.0)
