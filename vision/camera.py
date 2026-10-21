"""Webcam capture. Frames stay in memory: nothing here writes an image anywhere."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import config


class CameraError(Exception):
    """The camera could not be opened or stopped delivering frames."""


class Camera:
    """One ``cv2.VideoCapture``. *source* is a device index, or a video file path (a recorded clip;
    at its end ``read`` raises CameraError and the retry loop reopens it, so it loops)."""

    def __init__(self, source: int | str = config.VISION_CAMERA_INDEX,
                 width: int = config.VISION_CAPTURE_WIDTH,
                 height: int = config.VISION_CAPTURE_HEIGHT,
                 capture_factory: Callable[..., Any] | None = None) -> None:
        self.source = source
        self.width = width
        self.height = height
        self._capture_factory = capture_factory
        self._capture: Any = None

    def open(self) -> None:
        self.release()
        if self._capture_factory is not None:
            capture = self._capture_factory(self.source)
        else:
            import cv2

            # DirectShow opens far faster than the default Media Foundation backend on Windows.
            if isinstance(self.source, int) and sys.platform == "win32":
                capture = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
            else:
                capture = cv2.VideoCapture(self.source)
            if capture.isOpened():
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # we read slowly: do not serve old frames
        if not capture.isOpened():
            capture.release()
            raise CameraError(f"cannot open camera {self.source!r}")
        self._capture = capture

    @property
    def is_open(self) -> bool:
        return self._capture is not None

    def read(self) -> Any:
        """The next BGR frame (a numpy array). Raises CameraError when there is none."""
        if self._capture is None:
            raise CameraError("camera is not open")
        try:
            ok, frame = self._capture.read()
        except Exception as error:  # a driver or backend failure: treat like a lost camera
            raise CameraError(f"camera read failed: {error}") from error
        if not ok or frame is None:
            raise CameraError(f"camera {self.source!r} returned no frame")
        return frame

    def release(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
