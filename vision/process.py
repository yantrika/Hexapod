"""The vision process: camera -> YuNet -> tracker -> small events on a bounded queue.

It runs in its own process (spawn), never touches the Bridge, the body or audio, and never sends
or stores an image. ``VisionLoop`` is the logic with every dependency injected (camera, detector,
clock, wait), so the tests need no webcam; ``run_vision`` wires in the real ones.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import config
from vision.camera import Camera, CameraError
from vision.detector import Detection, bearing_deg
from vision.events import FACE_BEARING, PERSON_APPEARED, PERSON_LEFT, VisionEvent
from vision.stats import StatsWindow, VisionStats
from vision.tracker import IouTracker, TrackEvent, VisibleTrack

logger = logging.getLogger("hexa.vision")


@dataclass(frozen=True)
class VisionOptions:
    """Everything the process takes from config.py, so a script can override a few."""

    source: int | str = config.VISION_CAMERA_INDEX
    capture_width: int = config.VISION_CAPTURE_WIDTH
    capture_height: int = config.VISION_CAPTURE_HEIGHT
    fps: float = config.VISION_FPS
    paused_fps: float = config.VISION_PAUSED_FPS
    detect_width: int = config.VISION_DETECT_WIDTH
    hfov_deg: float = config.VISION_HFOV_DEG
    bearing_max_hz: float = config.VISION_BEARING_MAX_HZ
    paused_bearing_hz: float = config.VISION_PAUSED_BEARING_HZ
    retry_backoff_s: tuple[float, float] = config.VISION_RETRY_BACKOFF_S
    stats_s: float = config.VISION_STATS_S
    debug_preview: bool = False  # a live window in the vision process only; frames go nowhere else

    def fps_now(self, paused: bool) -> float:
        low, high = config.VISION_FPS_RANGE
        return config.clamp(self.paused_fps if paused else self.fps, low, high)


class CameraSource(Protocol):
    """What the loop needs of a camera (``vision.camera.Camera``, or a fake in the tests)."""

    @property
    def is_open(self) -> bool: ...
    def open(self) -> None: ...
    def read(self) -> Any: ...
    def release(self) -> None: ...


class Detector(Protocol):
    def resize(self, frame: Any) -> Any: ...
    def detect(self, small_frame: Any) -> list[Detection]: ...


class VisionLoop:
    def __init__(self, camera: CameraSource, detector: Detector, tracker: IouTracker,
                 emit: Callable[[VisionEvent], None], options: VisionOptions,
                 stop_event: Any, pause_event: Any,
                 clock: Callable[[], float] = time.monotonic,
                 wait: Callable[[float], bool] | None = None,
                 stats_sink: Callable[[VisionStats], None] | None = None,
                 meter: Callable[[], tuple[float, float]] | None = None,
                 preview: Callable[[Any, list[Detection], list[VisibleTrack]], None] | None = None,
                 ) -> None:
        self._camera = camera
        self._detector = detector
        self._tracker = tracker
        self._emit = emit
        self._options = options
        self._stop = stop_event
        self._pause = pause_event
        self._clock = clock
        self._wait = wait if wait is not None else stop_event.wait  # True once stop is set
        self._stats_sink = stats_sink
        self._meter = meter if meter is not None else process_meter()
        self._preview = preview
        self._last_bearing: dict[int, float] = {}
        self._retry_delay = options.retry_backoff_s[0]
        self._visible: list[VisibleTrack] = []

    def run(self) -> None:
        """Until stop_event is set. The camera is always released on the way out."""
        window = StatsWindow(self._clock())
        cpu_then, _ = self._meter()
        last_tick = self._clock() - 1.0 / self._options.fps_now(False)
        try:
            while not self._stop.is_set():
                if self._sleep_until_next(last_tick):
                    break
                last_tick = self._clock()
                try:
                    if not self._camera.is_open:
                        self._camera.open()
                    frame = self._camera.read()
                except CameraError as error:
                    self._camera_failed(error)
                    continue
                self._retry_delay = self._options.retry_backoff_s[0]
                started = self._clock()
                small = self._detector.resize(frame)
                detections = self._detector.detect(small)
                window.add_frame((self._clock() - started) * 1000.0)
                self._handle(detections, small.shape[1], last_tick)
                if self._preview is not None:
                    self._preview(small, detections, self._visible)
                cpu_then = self._report_stats(window, cpu_then)
        finally:
            self._camera.release()

    def _sleep_until_next(self, last_tick: float) -> bool:
        """Wait out the frame interval in short slices, so leaving the pause is noticed at once.
        True when stop was set meanwhile."""
        while True:
            paused = self._pause.is_set()
            remaining = last_tick + 1.0 / self._options.fps_now(paused) - self._clock()
            if remaining <= 0:
                return bool(self._stop.is_set())
            if self._wait(min(remaining, 0.2)):
                return True

    def _camera_failed(self, error: CameraError) -> None:
        self._camera.release()
        self._visible = []
        for event in self._tracker.reset():
            self._send(PERSON_LEFT, event.track_id)
        self._last_bearing.clear()
        logger.warning("camera error: %s; retrying in %.0f s", error, self._retry_delay)
        self._wait(self._retry_delay)  # returns early once stop is set; the loop then ends
        self._retry_delay = min(self._retry_delay * 2.0, self._options.retry_backoff_s[1])

    def _handle(self, detections: list[Detection], frame_width: int, now: float) -> None:
        result = self._tracker.update([d.box for d in detections])
        self._visible = result.visible
        for event in result.events:
            self._send_track_event(event)
        paused = self._pause.is_set()
        hertz = self._options.paused_bearing_hz if paused else self._options.bearing_max_hz
        interval = 1.0 / hertz if hertz > 0 else float("inf")
        for track in result.visible:
            last = self._last_bearing.get(track.track_id)
            if last is None or now - last >= interval:
                self._last_bearing[track.track_id] = now
                angle = bearing_deg(track.box, frame_width, self._options.hfov_deg)
                self._send(FACE_BEARING, track.track_id, angle)

    def _send_track_event(self, event: TrackEvent) -> None:
        if event.kind == "appeared":
            self._send(PERSON_APPEARED, event.track_id)
        else:
            self._last_bearing.pop(event.track_id, None)
            self._send(PERSON_LEFT, event.track_id)

    def _send(self, kind: str, track_id: int, angle_deg: float | None = None) -> None:
        self._emit(VisionEvent(kind, track_id, angle_deg, self._clock()))

    def _report_stats(self, window: StatsWindow, cpu_then: float) -> float:
        now = self._clock()
        if self._stats_sink is None or window.elapsed(now) < self._options.stats_s:
            return cpu_then
        cpu_now, rss_mb = self._meter()
        seconds = max(window.elapsed(now), 1e-9)
        stats = window.report(now, 100.0 * (cpu_now - cpu_then) / seconds, rss_mb,
                              self._pause.is_set())
        self._stats_sink(stats)
        return cpu_now


def process_meter() -> Callable[[], tuple[float, float]]:
    """() -> (CPU seconds used by this process so far, RSS in MB). Zero RSS without psutil."""
    try:
        import psutil

        process = psutil.Process()

        def measure() -> tuple[float, float]:
            times = process.cpu_times()
            return times.user + times.system, process.memory_info().rss / 1e6
    except ImportError:
        def measure() -> tuple[float, float]:
            return time.process_time(), 0.0
    return measure


def drop_oldest_put(target: Any, item: Any) -> None:
    """Put without ever blocking: when the queue is full the oldest item makes room."""
    try:
        target.put_nowait(item)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        try:
            target.put_nowait(item)
        except queue.Full:
            pass


def _make_preview(stop_event: Any) -> Callable[[Any, list[Detection], list[VisibleTrack]], None]:
    """A window with boxes, track ids and bearings. Pressing q or Esc in it stops the process."""
    import cv2

    broken = False

    def show(small: Any, detections: list[Detection], visible: list[VisibleTrack]) -> None:
        nonlocal broken
        if broken:
            return
        canvas = cv2.resize(small, None, fx=2.0, fy=2.0)
        width = small.shape[1]
        for detection in detections:
            x, y, w, h = (int(v * 2) for v in detection.box)
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (128, 128, 128), 1)
        best = max((d.score for d in detections), default=0.0)
        cv2.putText(canvas, f"faces {len(detections)}  best score {best:.2f}  "
                    f"brightness {float(small.mean()):.0f}", (6, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        for track in visible:
            x, y, w, h = (int(v * 2) for v in track.box)
            angle = bearing_deg(track.box, width)
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 200, 0), 2)
            cv2.putText(canvas, f"#{track.track_id} {angle:+.0f} deg", (x, max(12, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        try:
            cv2.imshow("hexa vision (q or Esc to quit)", canvas)
            key = cv2.waitKey(1) & 0xFF
        except cv2.error as error:  # no display (a Pi over ssh): carry on without the window
            logger.warning("preview unavailable: %s", error)
            broken = True
            return
        if key in (ord("q"), 27):
            stop_event.set()

    return show


def run_vision(event_queue: Any, stop_event: Any, pause_event: Any,
               options: VisionOptions | None = None, stats_queue: Any = None) -> None:
    """The process target. Returns when stop_event is set; never raises into the caller (a missing
    model is logged and ends the process quietly). pause_event: set while the LLM generates, it
    drops the frame rate and the bearing rate to their configured minimums."""
    options = options or VisionOptions()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import cv2

        from vision.detector import FaceDetector

        cv2.setNumThreads(config.VISION_CV_THREADS)
        detector = FaceDetector(detect_width=options.detect_width)
    except (ImportError, FileNotFoundError, OSError, RuntimeError) as error:
        logger.error("vision cannot start: %s", error)
        return
    camera = Camera(options.source, options.capture_width, options.capture_height)
    preview = _make_preview(stop_event) if options.debug_preview else None
    loop = VisionLoop(
        camera, detector, IouTracker(),
        emit=lambda event: drop_oldest_put(event_queue, event),
        options=options, stop_event=stop_event, pause_event=pause_event,
        stats_sink=(lambda stats: drop_oldest_put(stats_queue, stats))
        if stats_queue is not None else None,
        preview=preview)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    except Exception:  # the caller must never see a crash from here
        logger.exception("vision loop crashed")
    finally:
        camera.release()
        if preview is not None:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
        logger.info("vision stopped")


class VisionProcess:
    """Starts ``run_vision`` in a spawned process; ``stop`` always ends it and leaves no child."""

    def __init__(self, options: VisionOptions | None = None) -> None:
        context = mp.get_context("spawn")
        self.event_queue: Any = context.Queue(maxsize=config.VISION_EVENT_QUEUE_SIZE)
        self.stats_queue: Any = context.Queue(maxsize=8)
        self.stop_event: Any = context.Event()
        self.pause_event: Any = context.Event()
        self._process = context.Process(
            target=run_vision, name="hexa-vision", daemon=True,
            args=(self.event_queue, self.stop_event, self.pause_event, options,
                  self.stats_queue))

    def start(self) -> None:
        self._process.start()

    @property
    def is_alive(self) -> bool:
        return self._process.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        self._process.join(timeout)
        if self._process.is_alive():
            logger.warning("vision did not stop in %.0f s; terminating it", timeout)
            self._process.terminate()
            self._process.join(2.0)
        for handle in (self.event_queue, self.stats_queue):
            handle.close()
            handle.cancel_join_thread()

    def get_event(self, timeout: float = 0.0) -> VisionEvent | None:
        return _get(self.event_queue, timeout)

    def get_stats(self, timeout: float = 0.0) -> VisionStats | None:
        return _get(self.stats_queue, timeout)


def _get(source: Any, timeout: float) -> Any:
    try:
        return source.get(timeout=timeout) if timeout > 0 else source.get_nowait()
    except queue.Empty:
        return None
