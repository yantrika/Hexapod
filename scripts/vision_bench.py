#!/usr/bin/env python3
"""Headless numbers for the vision process: fps, detection time, CPU and RAM. For the Pi.

    python scripts/vision_bench.py --load 0                   one phase, no extra load
    python scripts/vision_bench.py --load 4 --seconds 30      then again with 4 busy CPU workers
    python scripts/vision_bench.py --camera clip.mp4 --fps 4  a recorded clip instead of a webcam

--load N starts N processes that spin on the CPU (standing in for the LLM using the cores), after
a first phase without them, and prints how detection time and fps changed. Nothing is shown,
saved or logged except the numbers. Needs requirements-vision.txt and the models.
"""

from __future__ import annotations

import argparse
import dataclasses
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from vision.process import VisionOptions, VisionProcess  # noqa: E402
from vision.stats import VisionStats, percentile  # noqa: E402


def spin(stop_event: Any) -> None:
    value = 1.0001
    while not stop_event.is_set():
        for _ in range(100_000):
            value = value * 1.0000001 % 1000.0 + 1.0


def camera_source(text: str) -> int | str:
    return int(text) if text.isdigit() else text


def measure(options: VisionOptions, seconds: float) -> list[VisionStats]:
    """Run the vision process for *seconds* and return its stats windows."""
    process = VisionProcess(options)
    process.start()
    stats: list[VisionStats] = []
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end and process.is_alive:
            while process.get_event() is not None:  # keep the event queue empty
                pass
            item = process.get_stats(timeout=0.2)
            if item is not None:
                stats.append(item)
                print(f"  {item.line()}", flush=True)
    finally:
        process.stop()
    return stats


def summarise(label: str, stats: list[VisionStats]) -> tuple[float, float, float]:
    if not stats:
        print(f"{label}: no numbers (camera missing, or too short a run: use --seconds 15 or more)")
        return 0.0, 0.0, 0.0
    fps = sum(s.capture_fps for s in stats) / len(stats)
    median = percentile([s.detect_ms_median for s in stats], 0.5)
    worst_p95 = max(s.detect_ms_p95 for s in stats)
    cpu = sum(s.cpu_percent for s in stats) / len(stats)
    ram = max(s.rss_mb for s in stats)
    print(f"{label}: fps {fps:.1f}, detect median {median:.1f} ms, worst p95 {worst_p95:.1f} ms, "
          f"cpu {cpu:.1f}% of one core, ram {ram:.0f} MB")
    return fps, median, worst_p95


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--camera", type=camera_source, default=config.VISION_CAMERA_INDEX)
    parser.add_argument("--fps", type=float, default=config.VISION_FPS)
    parser.add_argument("--width", type=int, default=config.VISION_DETECT_WIDTH)
    parser.add_argument("--seconds", type=float, default=20.0, help="per phase (default 20)")
    parser.add_argument("--load", type=int, default=0, help="busy CPU workers for phase two")
    args = parser.parse_args(argv)
    if not config.VISION_YUNET_FILE.is_file():
        print(f"{config.VISION_YUNET_FILE} is missing: run python scripts/fetch_vision_models.py",
              file=sys.stderr)
        return 2
    options = dataclasses.replace(
        VisionOptions(), source=args.camera, fps=args.fps, detect_width=args.width,
        stats_s=min(config.VISION_STATS_S, max(args.seconds / 3.0, 1.0)))
    print(f"phase 1: no extra load, {args.seconds:.0f} s")
    base = measure(options, args.seconds)
    base_numbers = summarise("no load", base)
    if args.load <= 0:
        return 0 if base else 1

    print(f"phase 2: {args.load} busy CPU workers, {args.seconds:.0f} s")
    context = mp.get_context("spawn")
    stop = context.Event()
    workers = [context.Process(target=spin, args=(stop,), daemon=True) for _ in range(args.load)]
    for worker in workers:
        worker.start()
    try:
        loaded = measure(options, args.seconds)
    finally:
        stop.set()
        for worker in workers:
            worker.join(5.0)
            if worker.is_alive():
                worker.terminate()
    loaded_numbers = summarise(f"load {args.load}", loaded)
    if base and loaded and base_numbers[1] > 0:
        print(f"detection time x{loaded_numbers[1] / base_numbers[1]:.2f} "
              f"({base_numbers[1]:.1f} -> {loaded_numbers[1]:.1f} ms), "
              f"fps {base_numbers[0]:.1f} -> {loaded_numbers[0]:.1f}")
    return 0 if base and loaded else 1


if __name__ == "__main__":
    sys.exit(main())
