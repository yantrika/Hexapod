"""The quality gate before a face is embedded: too small, not frontal or blurry crops are skipped,
never matched. Pure checks on the detection row and a grey crop; nothing is stored."""

from __future__ import annotations

import math
from typing import Any

import config

TOO_SMALL = "too_small"
NOT_FRONTAL = "not_frontal"
BLURRY = "blurry"
NO_LANDMARKS = "no_landmarks"

_BLUR_SIDE = 96  # crops are scaled to this before the Laplacian: the number ignores distance


def yaw_ratio(row: Any) -> float:
    """How far the nose is from the middle of the eyes, in eye distances (0 = looking straight at
    the camera). YuNet's row: box (0-3), right eye, left eye, nose, mouth corners (4-13), score."""
    right_x, right_y, left_x, left_y, nose_x = (float(row[i]) for i in (4, 5, 6, 7, 8))
    eye_distance = math.hypot(left_x - right_x, left_y - right_y)
    if eye_distance <= 0.0:
        return math.inf
    return abs(nose_x - (right_x + left_x) / 2.0) / eye_distance


def sharpness(frame: Any, row: Any) -> float:
    """Variance of the Laplacian of the face crop (higher = sharper). 0.0 for an empty crop."""
    import cv2

    x, y, w, h = (int(float(v)) for v in row[:4])
    crop = frame[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]
    if crop.size == 0:
        return 0.0
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    grey = cv2.resize(grey, (_BLUR_SIDE, _BLUR_SIDE), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def face_quality(frame: Any, row: Any, min_face_px: float = config.VISION_MIN_FACE_PX,
                 blur_min: float = config.VISION_BLUR_MIN,
                 frontal_max: float = config.VISION_FRONTAL_MAX) -> str | None:
    """None when the face is good enough to embed, else the reason (one of the constants above)."""
    if row is None or len(row) < 15:
        return NO_LANDMARKS
    if min(float(row[2]), float(row[3])) < min_face_px:
        return TOO_SMALL
    if yaw_ratio(row) > frontal_max:
        return NOT_FRONTAL
    if sharpness(frame, row) < blur_min:
        return BLURRY
    return None
