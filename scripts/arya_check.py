#!/usr/bin/env python3
"""Does Vosk (the small US model) recognise "Arya"? Piper renders phrases, Vosk listens. No mic.

    python scripts/arya_check.py                    the built-in phrases, 3 speaking speeds each
    python scripts/arya_check.py --model in         the Indian English model
    python scripts/arya_check.py "hey arya" "arya stand up"

Each phrase is synthesized by Piper (the robot's voice, resampled to three speeds), fed to the
real recognizers (free text and command grammar) and compared with what was said. A hit means
the free text contains the word "arya". This is ONE synthetic voice, so it is a floor for the
vocabulary question, not a recognition rate for a human: use ``scripts/stt_check.py`` with a
microphone for that. Results go to the terminal (and ``--out`` as JSONL).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import config  # noqa: E402
from brain.router import route  # noqa: E402
from voice.stt import SttError, VoskStt  # noqa: E402
from voice.tts import PiperEngine, TtsError  # noqa: E402

PHRASES = [
    "Arya", "hey Arya", "hello Arya", "Arya sit down", "Arya, stand up", "Arya, roast mode",
    "Arya, calm mode", "Arya, what do you remember about me", "stop roasting",
    "my name is Siddharth", "my name is Priya", "call me Rahul", "forget me", "yes",
]
SPEEDS = (1.1, 1.0, 0.9)  # the clip is resampled (faster / as rendered / slower, pitch moves too)


def resample(samples: np.ndarray, speed: float) -> np.ndarray:
    if speed == 1.0:
        return samples
    positions = np.arange(0, len(samples) - 1, speed)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16)


def recognise(stt: VoskStt, samples: np.ndarray) -> tuple[str, str]:
    """(free text, grammar text) for a whole utterance."""
    stt.reset()
    size = config.AUDIO_BLOCKSIZE
    padded = np.concatenate([samples, np.zeros(config.AUDIO_SAMPLE_RATE, dtype=np.int16)])
    last = ""
    for start in range(0, len(padded), size):
        for event in stt.feed(padded[start:start + size]):
            if event.kind == "final":
                last = event.text
                grammar = event.grammar.text if event.grammar else ""
                return last, grammar
    for event in stt.flush():
        return event.text, event.grammar.text if event.grammar else ""
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phrases", nargs="*", help="phrases to say (default: the built-in list)")
    parser.add_argument("--model", default=None, help="Vosk model: us (default) or in")
    parser.add_argument("--out", type=Path, default=None, help="write the attempts as JSONL")
    args = parser.parse_args()
    phrases = args.phrases or PHRASES
    try:
        stt = VoskStt(args.model)
        engine = PiperEngine()
        engine.check_installed()
    except (SttError, TtsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    rows: list[dict[str, Any]] = []
    try:
        for phrase in phrases:
            for speed in SPEEDS:
                clip = engine.synthesize(phrase)
                free, grammar = recognise(stt, resample(clip.samples, speed))
                said_arya = "arya" in phrase.lower()
                rows.append({"said": phrase, "speed": speed, "free": free, "grammar": grammar,
                             "has_arya": said_arya, "arya_heard": "arya" in free.split(),
                             "route": route(free).kind, "action": route(free).action})
                print(f"{phrase!r:42} x{speed:<4} free={free!r:40} grammar={grammar!r}")
    finally:
        engine.close()
    named = [r for r in rows if r["has_arya"]]
    heard = sum(1 for r in named if r["arya_heard"])
    print(f"\n'arya' recognised as a word: {heard}/{len(named)} attempts that said it")
    routed = [r for r in named if r["said"].lower().count("arya") and len(r["said"].split()) > 2]
    ok = sum(1 for r in routed if r["action"])
    print(f"phrases that start with 'arya' and carry a command, routed by the free text: "
          f"{ok}/{len(routed)}")
    print("what the free text made of 'arya':",
          sorted({w for r in named for w in r["free"].split()
                  if w not in r["said"].lower().replace(",", "").split()}))
    if args.out:
        args.out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
