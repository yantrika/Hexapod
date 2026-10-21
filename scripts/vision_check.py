#!/usr/bin/env python3
"""Try the vision process on a webcam (or a video file). No robot, nothing else running.

    python scripts/vision_check.py                          events and a summary, no window
    python scripts/vision_check.py --debug-preview          also a live window (q or Esc quits)
    python scripts/vision_check.py --camera 1 --fps 6 --width 320 --seconds 30
    python scripts/vision_check.py --camera clip.mp4        a recorded clip (it loops)

Prints every event the vision process sends (person_appeared, person_left, face_bearing) and,
every 5 s, the capture fps, the detection time per frame (median, p95), the CPU % of the vision
process (of one core) and its RAM. The preview window is drawn inside the vision process, from
frames that exist only in memory: no frame or face is ever written to disk or logged.
Needs requirements-vision.txt and python scripts/fetch_vision_models.py. Ctrl-C ends it.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from vision.process import VisionOptions, VisionProcess  # noqa: E402


def camera_source(text: str) -> int | str:
    return int(text) if text.isdigit() else text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--camera", type=camera_source, default=config.VISION_CAMERA_INDEX,
                        help="device index (default %(default)s) or a video file")
    parser.add_argument("--fps", type=float, default=config.VISION_FPS,
                        help="frames per second (default %(default)s)")
    parser.add_argument("--width", type=int, default=config.VISION_DETECT_WIDTH,
                        help="detection width in pixels (default %(default)s)")
    parser.add_argument("--debug-preview", action="store_true",
                        help="show the live window with boxes, track ids and bearing")
    parser.add_argument("--no-preview", action="store_true",
                        help="never show a window (wins over --debug-preview)")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after this long (default: until Ctrl-C or q)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not config.VISION_YUNET_FILE.is_file():
        print(f"{config.VISION_YUNET_FILE} is missing: run python scripts/fetch_vision_models.py",
              file=sys.stderr)
        return 2
    options = dataclasses.replace(
        VisionOptions(), source=args.camera, fps=args.fps, detect_width=args.width,
        debug_preview=args.debug_preview and not args.no_preview)
    process = VisionProcess(options)
    process.start()
    started = time.monotonic()
    print("vision started (Ctrl-C to stop)")
    try:
        while process.is_alive and not process.stop_event.is_set():
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
            event = process.get_event(timeout=0.1)
            if event is not None:
                print(f"{time.monotonic() - started:7.1f}s  {event}")
            stats = process.get_stats()
            if stats is not None:
                print(f"{time.monotonic() - started:7.1f}s  SUMMARY {stats.line()}")
    except KeyboardInterrupt:
        print()
    finally:
        died = not process.is_alive and not process.stop_event.is_set()
        process.stop()
    if died:
        print("the vision process ended on its own: see its log lines above", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
