#!/usr/bin/env python3
"""Render the fixed phrases and fillers in ``config.TTS_PHRASES`` to ``assets/phrases/*.wav``.

Playback of these has no synthesis wait. Existing files are skipped (``--force`` re-renders).
Uses ONE Piper process for all of them. Run after ``scripts/fetch_models.sh``, which calls it.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from voice.tts import PiperEngine, TtsError, phrase_path, write_wav  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-render phrases that exist")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    todo = {}
    for name, text in config.TTS_PHRASES.items():
        path = phrase_path(name)
        if path.is_file() and not args.force:
            print(f"skip: {name} already rendered")
        else:
            todo[name] = text
    if not todo:
        print("phrases: nothing to render")
        return 0
    with PiperEngine() as engine:
        try:
            engine.check_installed()
        except TtsError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for name, text in todo.items():
            started = time.perf_counter()
            try:
                clip = engine.synthesize(text)
            except TtsError as error:
                print(f"error: {name}: {error}", file=sys.stderr)
                return 1
            write_wav(phrase_path(name), clip)
            print(f"rendered: {name:<18} {clip.duration_s:4.2f} s audio in "
                  f"{time.perf_counter() - started:4.2f} s  {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
