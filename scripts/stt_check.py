#!/usr/bin/env python3
"""Check how well Vosk hears YOUR voice, one phrase at a time. Needs a microphone, no robot.

    python scripts/stt_check.py                  US English model (the default)
    python scripts/stt_check.py --model in       Indian English model
    python scripts/stt_check.py --model us --only commands     (or --only chat)

For each phrase it asks you to say it (press Enter, then speak), shows what Vosk heard, what the
router did with it and whether that is what you meant. A summary table ends the run. Run it
once per model with the same phrases and compare. Ctrl-C ends early and still prints the table.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from brain.router import route  # noqa: E402
from voice.audio import MicSource  # noqa: E402
from voice.stt import SttError, VoskStt  # noqa: E402

CHAT_SENTENCES = [
    "I sat down for lunch",
    "what is the weather like today",
    "tell me a joke",
    "turn up the music",
    "how do I stand out in an interview",
    "what is your name",
]
LISTEN_S = 6.0  # give up on one phrase after this long
SILENCE_END_S = 1.0  # nothing new for this long after a result: done


@dataclass
class Row:
    said: str
    expected: str  # router kind or action we expect
    heard: str
    got: str
    ok: bool


def expected_for(phrase: str) -> str:
    result = route(phrase)  # what the router does with the TEXT: the best Vosk can achieve
    return (result.action or "chat") if result.kind != "chat" else "chat"


def listen_once(stt: VoskStt, mic: MicSource) -> str:
    """Record from the moment of the call until Vosk has a final result (or time runs out)."""
    stt.reset()
    while mic.read(0.01) is not None:  # drop audio queued before the prompt
        pass
    started = time.monotonic()
    last_heard = started
    final = ""
    while time.monotonic() - started < LISTEN_S:
        block = mic.read(0.2)
        if block is None:
            continue
        for event in stt.feed(block):
            last_heard = time.monotonic()
            if event.kind == "final" and event.text:
                return event.text
            if event.kind == "partial":
                final = event.text
        if final and time.monotonic() - last_heard > SILENCE_END_S:
            break
    flushed = stt.flush()
    return flushed[0].text if flushed else final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=None, help="us, in, a directory name or a path")
    parser.add_argument("--only", choices=("commands", "chat"), default=None)
    parser.add_argument("--device", type=int, default=config.MIC_DEVICE)
    args = parser.parse_args()
    try:
        stt = VoskStt(args.model)
    except SttError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    phrases: list[str] = []
    if args.only != "chat":
        phrases += list(config.ROUTER_PHRASES) + list(config.STOP_WORDS)
    if args.only != "commands":
        phrases += CHAT_SENTENCES
    mic = MicSource(device=args.device)
    mic.start()
    print(f"model: {stt.model_path.name}   {len(phrases)} phrases. "
          "Press Enter, then say the phrase.")
    rows: list[Row] = []
    try:
        for index, phrase in enumerate(phrases, 1):
            input(f"\n[{index}/{len(phrases)}] say: \"{phrase}\"   (Enter to start) ")
            print("   listening...", flush=True)
            heard = listen_once(stt, mic)
            result = route(heard) if heard else None
            got = "nothing heard" if result is None else (
                (result.action or "chat") if result.kind != "chat" else "chat")
            expected = expected_for(phrase)
            ok = got == expected
            print(f"   heard: {heard!r}\n   router: {got}   expected: {expected}   "
                  f"{'OK' if ok else 'WRONG'}")
            rows.append(Row(phrase, expected, heard, got, ok))
    except (KeyboardInterrupt, EOFError):
        print("\n(stopped early)")
    finally:
        mic.stop()
    if not rows:
        return 0
    print(f"\nsummary for {stt.model_path.name}")
    print(f"{'you said':<38}{'vosk heard':<38}{'router':<10}{'expected':<10}result")
    for row in rows:
        print(f"{row.said:<38}{row.heard:<38}{row.got:<10}{row.expected:<10}"
              f"{'ok' if row.ok else 'WRONG'}")
    right = sum(row.ok for row in rows)
    print(f"\n{right}/{len(rows)} right ({right / len(rows):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
