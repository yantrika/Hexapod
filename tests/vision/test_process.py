"""The real vision process (spawn): it shuts down cleanly and leaves no child. Needs OpenCV and
the YuNet model; no webcam (an index with no camera behind it exercises the retry path)."""

from __future__ import annotations

import dataclasses
import multiprocessing as mp
import time
from importlib.util import find_spec

import pytest

import config
from vision.process import VisionOptions, VisionProcess

pytestmark = pytest.mark.skipif(
    find_spec("cv2") is None or not config.VISION_YUNET_FILE.is_file(),
    reason="needs opencv-python and the YuNet model (scripts/fetch_vision_models.py)")


def test_the_process_with_no_camera_stops_on_request_and_leaves_no_child() -> None:
    options = dataclasses.replace(VisionOptions(), source=98, retry_backoff_s=(0.2, 0.4))
    process = VisionProcess(options)
    process.start()
    time.sleep(1.0)  # a few failed opens: they are retried, not fatal, and send no event
    assert process.is_alive
    assert process.get_event() is None
    start = time.monotonic()
    process.stop(timeout=5.0)
    assert time.monotonic() - start < 4.0
    assert not process.is_alive
    assert mp.active_children() == []


def test_stopping_before_the_camera_is_ever_tried_is_clean() -> None:
    process = VisionProcess(dataclasses.replace(VisionOptions(), source=98))
    process.stop_event.set()
    process.start()
    process.stop(timeout=5.0)
    assert not process.is_alive and mp.active_children() == []
