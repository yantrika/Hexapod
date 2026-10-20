"""YuNet face detection and the bearing maths. cv2 is imported only when a detector is built."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

import config

Box = tuple[float, float, float, float]  # x, y, width, height in detection-frame pixels


@dataclass(frozen=True)
class Detection:
    box: Box
    score: float
    row: Any = None  # YuNet's 15 numbers (box, 5 landmarks, score); the recognizer aligns with it


def bearing_deg(box: Box, frame_width: float, hfov_deg: float = config.VISION_HFOV_DEG) -> float:
    """Horizontal angle of the box centre from the camera axis, pinhole model.

    Positive = the person is to the robot's LEFT (counter-clockwise from above, like the body
    frame's +Y), negative = right. The image is not mirrored, so a face right of the picture
    centre gives a negative angle."""
    focal_px = (frame_width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    centre_x = box[0] + box[2] / 2.0
    return -math.degrees(math.atan((centre_x - frame_width / 2.0) / focal_px))


class FaceDetector:
    """Runs YuNet on frames resized to ``detect_width``. Boxes are in that resized frame."""

    def __init__(self, model_path: Path = config.VISION_YUNET_FILE,
                 detect_width: int = config.VISION_DETECT_WIDTH,
                 score_threshold: float = config.VISION_SCORE_THRESHOLD,
                 nms_threshold: float = config.VISION_NMS_THRESHOLD,
                 top_k: int = config.VISION_TOP_K) -> None:
        import cv2

        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"{model_path} is missing: run python scripts/fetch_vision_models.py")
        self._cv2 = cv2
        self.detect_width = detect_width
        self._size: tuple[int, int] | None = None
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (detect_width, detect_width), score_threshold, nms_threshold,
            top_k)

    def resize(self, frame: Any) -> Any:
        height, width = frame.shape[:2]
        if width == self.detect_width:
            return frame
        scaled_height = max(1, round(height * self.detect_width / width))
        return self._cv2.resize(frame, (self.detect_width, scaled_height),
                                interpolation=self._cv2.INTER_AREA)

    def detect(self, small_frame: Any) -> list[Detection]:
        """Faces in a frame already resized by ``resize``."""
        height, width = small_frame.shape[:2]
        if self._size != (width, height):
            self._detector.setInputSize((width, height))
            self._size = (width, height)
        _, faces = self._detector.detect(small_frame)
        if faces is None:
            return []
        return [Detection(box=(float(r[0]), float(r[1]), float(r[2]), float(r[3])),
                          score=float(r[14]), row=np.asarray(r, dtype=np.float32))
                for r in faces]
