"""vision.camera.Camera with a fake capture object (no OpenCV needed)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from vision.camera import Camera, CameraError


class FakeCapture:
    def __init__(self, opened: bool = True, frames: list[Any] | None = None) -> None:
        self.opened = opened
        self.frames = list(frames or [])
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 (the cv2 name)
        return self.opened

    def read(self) -> tuple[bool, Any]:
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self) -> None:
        self.released = True


def test_open_failure_raises_camera_error_and_releases() -> None:
    capture = FakeCapture(opened=False)
    camera = Camera(3, capture_factory=lambda source: capture)
    with pytest.raises(CameraError):
        camera.open()
    assert capture.released and not camera.is_open


def test_read_returns_frames_and_then_raises_at_the_end() -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    camera = Camera("clip.mp4", capture_factory=lambda source: FakeCapture(frames=[frame]))
    camera.open()
    assert camera.read() is frame
    with pytest.raises(CameraError):
        camera.read()


def test_read_before_open_raises() -> None:
    with pytest.raises(CameraError):
        Camera(0, capture_factory=lambda source: FakeCapture()).read()


def test_a_driver_exception_becomes_camera_error() -> None:
    class Broken(FakeCapture):
        def read(self) -> tuple[bool, Any]:
            raise OSError("usb reset")

    camera = Camera(0, capture_factory=lambda source: Broken())
    camera.open()
    with pytest.raises(CameraError):
        camera.read()


def test_release_is_idempotent() -> None:
    capture = FakeCapture()
    camera = Camera(0, capture_factory=lambda source: capture)
    camera.open()
    camera.release()
    camera.release()
    assert capture.released and not camera.is_open
