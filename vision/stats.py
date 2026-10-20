"""Numbers the vision process reports about itself (rates and times, never pixels)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of *values* (0 for an empty list); fraction 0.5 is the median."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class VisionStats:
    seconds: float  # length of the window these numbers cover
    frames: int  # frames read in the window
    capture_fps: float
    detect_ms_median: float
    detect_ms_p95: float
    cpu_percent: float  # of ONE core (can pass 100 with several threads); 0 without psutil
    rss_mb: float  # 0 without psutil
    paused: bool

    def line(self) -> str:
        return (f"fps {self.capture_fps:4.1f} ({self.frames} frames)  "
                f"detect median {self.detect_ms_median:5.1f} ms, p95 {self.detect_ms_p95:5.1f} ms  "
                f"cpu {self.cpu_percent:5.1f}%  ram {self.rss_mb:5.1f} MB"
                f"{'  [paused]' if self.paused else ''}")


class StatsWindow:
    """Collects frame and detection times; ``report`` summarises and starts a new window."""

    def __init__(self, now: float) -> None:
        self._start = now
        self._detect_ms: list[float] = []
        self._frames = 0

    def elapsed(self, now: float) -> float:
        return now - self._start

    def add_frame(self, detect_ms: float) -> None:
        self._frames += 1
        self._detect_ms.append(detect_ms)

    def report(self, now: float, cpu_percent: float, rss_mb: float, paused: bool) -> VisionStats:
        seconds = max(now - self._start, 1e-9)
        stats = VisionStats(seconds=seconds, frames=self._frames,
                            capture_fps=self._frames / seconds,
                            detect_ms_median=percentile(self._detect_ms, 0.5),
                            detect_ms_p95=percentile(self._detect_ms, 0.95),
                            cpu_percent=cpu_percent, rss_mb=rss_mb, paused=paused)
        self._start = now
        self._detect_ms = []
        self._frames = 0
        return stats
