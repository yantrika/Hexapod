#!/usr/bin/env python3
"""Type a line, hexa speaks it sentence by sentence (Step 7, no STT, no body).

    python scripts/say.py                    interactive: /clear cancels speech, /quit exits
    python scripts/say.py "Hello, I am hexa" speak one line and exit
    python scripts/say.py --phrase okay      play a pre-rendered phrase (--list-phrases)

Prints the time to first sound for each line (text typed to sound started). Piper runs as one
long-lived process, started at launch; the first line pays no model load.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import config  # noqa: E402
from voice.playback import Playback, SoundDeviceSink, Utterance  # noqa: E402
from voice.tts import PiperEngine, TtsError  # noqa: E402


class FirstSound:
    """Remembers when the first sentence of each say() call started playing."""

    def __init__(self) -> None:
        self.submitted: dict[int, float] = {}
        self.latency: dict[int, float] = {}
        self.arrived = threading.Event()

    def __call__(self, utterance: Utterance) -> None:
        if utterance.group in self.submitted and utterance.group not in self.latency:
            self.latency[utterance.group] = time.perf_counter() - self.submitted[utterance.group]
            self.arrived.set()

    def submit(self, group: int, started: float) -> None:
        self.submitted[group] = started
        self.arrived.clear()

    def report(self, group: int, timeout: float = 15.0) -> None:
        self.arrived.wait(timeout)
        if group in self.latency:
            print(f"time to first sound: {self.latency[group] * 1000:.0f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text", nargs="?", help="speak this line and exit")
    parser.add_argument("--phrase", metavar="NAME", help="play a pre-rendered phrase")
    parser.add_argument("--list-phrases", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.list_phrases:
        for name, text in config.TTS_PHRASES.items():
            print(f"{name:<18} {text}")
        return 0

    engine = PiperEngine()
    try:
        engine.check_installed()
    except TtsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    first = FirstSound()
    playback = Playback(engine, SoundDeviceSink(), on_start=first)
    playback.start()
    try:
        if args.phrase is not None or args.text is not None:
            return speak_once(playback, first, args)
        engine.start()  # pay the model load now, not on the first line
        print("type a line to speak it; /clear cancels, /quit exits")
        return interactive(playback, first)
    finally:
        playback.shutdown()


def speak_once(playback: Playback, first: FirstSound, args: argparse.Namespace) -> int:
    started = time.perf_counter()
    try:
        group = playback.say_phrase(args.phrase) if args.phrase else playback.say(args.text)
    except TtsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    first.submit(group, started)
    first.report(group)
    playback.wait_idle(60.0)
    time.sleep(config.SPEAK_TAIL_S)
    return 1 if playback.errors else 0


def interactive(playback: Playback, first: FirstSound) -> int:
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line == "/quit":
            return 0
        if line == "/clear":
            started = time.perf_counter()
            playback.clear()
            print(f"cleared in {(time.perf_counter() - started) * 1000:.1f} ms")
            continue
        if not line:
            continue
        started = time.perf_counter()
        try:
            group = playback.say(line)
        except TtsError as error:
            print(f"error: {error}", file=sys.stderr)
            continue
        first.submit(group, started)
        threading.Thread(target=first.report, args=(group,), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
