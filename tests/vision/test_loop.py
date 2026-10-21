"""VisionLoop with a fake camera, detector and clock: events, rate limits, pause, retry, release."""

from __future__ import annotations

from typing import Any

import numpy as np

from tests.vision_fakes import FACE, FakeCamera, FakeClock, FakeDetector, bearings, run_loop
from vision.camera import CameraError
from vision.detector import Box
from vision.events import FACE_BEARING, PERSON_APPEARED, PERSON_LEFT
from vision.stats import VisionStats


def test_events_appear_bearing_and_left_for_one_person() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=6.0)
    detector = FakeDetector(lambda i: [FACE] if i < 12 else [])
    events = run_loop(camera, clock, detector)
    kinds = [e.kind for e in events]
    assert kinds.count(PERSON_APPEARED) == 1 and kinds.count(PERSON_LEFT) == 1
    assert kinds.index(PERSON_APPEARED) < kinds.index(FACE_BEARING) < kinds.index(PERSON_LEFT)
    assert {e.track_id for e in events} == {1}
    assert all(abs(e.angle_deg or 0.0) < 1.0 for e in bearings(events))  # centred face


def test_bearing_events_are_rate_limited_per_track() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=10.0)
    events = run_loop(camera, clock, FakeDetector(lambda i: [FACE]))
    times = [e.time for e in bearings(events)]
    assert 15 <= len(times) <= 21  # about 2 per second over 10 s, frames come at 4 per second
    assert all(b - a >= 0.5 - 1e-6 for a, b in zip(times, times[1:], strict=False))


def test_pause_event_lowers_the_frame_rate_and_the_bearing_rate() -> None:
    free_clock, paused_clock = FakeClock(), FakeClock()
    free_camera = FakeCamera(free_clock, stop_at=10.0)
    paused_camera = FakeCamera(paused_clock, stop_at=10.0)
    free = run_loop(free_camera, free_clock, FakeDetector(lambda i: [FACE]))
    paused = run_loop(paused_camera, paused_clock, FakeDetector(lambda i: [FACE]), pause=True)
    assert free_camera.reads >= 35
    assert paused_camera.reads <= 12
    assert len(bearings(paused)) < len(bearings(free)) / 2
    times = [e.time for e in bearings(paused)]
    assert all(b - a >= 2.0 - 1e-6 for a, b in zip(times, times[1:], strict=False))


def test_camera_errors_are_retried_with_growing_backoff_and_never_raise() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=20.0, open_failures=3)
    events = run_loop(camera, clock, FakeDetector(lambda i: [FACE]))
    backoffs = [w for w in clock.waits if w >= 1.0]
    assert backoffs[:3] == [1.0, 2.0, 4.0]
    assert camera.opens == 4  # three failures, then it works
    assert PERSON_APPEARED in [e.kind for e in events]


def test_backoff_is_capped_at_the_configured_maximum() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=60.0, open_failures=6)
    run_loop(camera, clock, FakeDetector(lambda i: []))
    backoffs = [w for w in clock.waits if w >= 1.0]
    assert backoffs[:6] == [1.0, 2.0, 4.0, 4.0, 4.0, 4.0]  # capped at the second number


def test_a_camera_lost_mid_run_reports_the_person_left_and_recovers() -> None:
    clock = FakeClock()
    failed = {"done": False}

    def script(index: int, now: float) -> Any:
        if index == 5 and not failed["done"]:
            failed["done"] = True
            raise CameraError("unplugged")
        return np.zeros((240, 320, 3), dtype=np.uint8)

    camera = FakeCamera(clock, stop_at=8.0, script=script)
    events = run_loop(camera, clock, FakeDetector(lambda i: [FACE]))
    kinds = [e.kind for e in events if e.kind != FACE_BEARING]
    assert kinds == [PERSON_APPEARED, PERSON_LEFT, PERSON_APPEARED]
    assert camera.releases >= 2  # released on the error and again on the way out


def test_stop_ends_the_loop_and_always_releases_the_camera() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=2.0)
    run_loop(camera, clock, FakeDetector(lambda i: []))
    assert camera.releases >= 1 and not camera.is_open


def test_a_crashing_detector_still_releases_the_camera() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=5.0)

    def boom(index: int) -> list[Box]:
        raise RuntimeError("detector exploded")

    try:
        run_loop(camera, clock, FakeDetector(boom))
    except RuntimeError:
        pass
    assert camera.releases >= 1 and not camera.is_open


def test_stats_report_fps_detection_time_cpu_and_ram() -> None:
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=12.0)
    stats: list[VisionStats] = []
    run_loop(camera, clock, FakeDetector(lambda i: []), stats=stats)
    assert len(stats) >= 2
    first = stats[0]
    assert 3.0 <= first.capture_fps <= 4.5
    assert first.rss_mb == 40.0 and 24.0 <= first.cpu_percent <= 26.0
    assert first.detect_ms_p95 >= first.detect_ms_median >= 0.0
