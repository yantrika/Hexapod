#!/usr/bin/env python3
"""Check how well Vosk hears YOUR voice, with the command grammar. Needs a microphone, no robot.

    python scripts/stt_check.py --model us            US English model (the default)
    python scripts/stt_check.py --model in            Indian English model
    python scripts/stt_check.py --model us --mic 8    pick an input device (see mic_check.py)
    python scripts/stt_check.py --only chat           only the chat sentences (or: commands)
    python scripts/stt_check.py --repeats 1           a quick run
    python scripts/stt_check.py --replay logs/stt_check-us.jsonl    redo the summary offline with
                                                      the CURRENT thresholds in config.py

First it shows your microphone level. Then for each phrase press Enter and say it (3 times, one
after another). For every attempt it shows both recognizers' results with confidences, which rule
decided and what the router did. The summary counts command accuracy, chat routed correctly,
DANGEROUS false positives (chat -> a motion command) and missed stops. Every attempt is also
saved to logs/stt_check-<model>.jsonl (gitignored) so the thresholds can be re-tuned from it.
Run it in a quiet room, once per model. Ctrl-C ends early and still prints the summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import config  # noqa: E402
from brain.router import route  # noqa: E402
from brain.stt_decision import decide  # noqa: E402
from voice.audio import MicSource  # noqa: E402
from voice.stt import Hypothesis, SttError, VoskStt  # noqa: E402

CHAT_SENTENCES = [
    "I sat down for lunch",
    "I'll walk you through it",
    "turn up the music",
    "how do I stand out in an interview",
    "what is the weather like today",
    "tell me a joke",
    "what is your name",
    "can you tell me about waves",
    "let's go back to the beginning",
    "I want to sit by the window",
]
LISTEN_S = 6.0  # give up on one attempt after this long
SILENCE_END_S = 1.0  # nothing new for this long after speech: done
MOTION = ("walk", "turn", "sit", "stand", "wave")


@dataclass
class Attempt:
    said: str
    expected: str  # action, "stop" or "chat"
    free_text: str
    free_conf: float
    grammar_text: str
    grammar_conf: float
    free_words: list[list[object]]
    grammar_words: list[list[object]]
    path: str
    reason: str
    got: str  # what the router did with the decided text
    ok: bool

    @property
    def kind(self) -> str:
        return "chat" if self.expected == "chat" else ("stop" if self.expected == "stop" else "cmd")


def outcome(text: str) -> str:
    result = route(text)
    return "chat" if result.kind == "chat" else (result.action or "chat")


def expected_for(phrase: str) -> str:
    return outcome(phrase)  # what the router does with the TEXT: the best Vosk can achieve


def hypothesis_from(text: str, words: list[list[object]]) -> Hypothesis:
    return Hypothesis(text, tuple((str(w), float(c)) for w, c in words))  # type: ignore[arg-type]


def judge(said: str, free: Hypothesis, grammar: Hypothesis | None) -> Attempt:
    """Run the live decision on one attempt (also used to replay saved attempts)."""
    decision = decide(free.text, grammar)
    got = outcome(decision.text) if (free.text or decision.text) else "nothing heard"
    expected = expected_for(said)
    gram = grammar or Hypothesis()
    return Attempt(
        said, expected, free.text, free.mean_conf, gram.text, gram.mean_conf,
        [[w, c] for w, c in free.words], [[w, c] for w, c in gram.words],
        decision.path, decision.reason, got, got == expected,
    )


def level_check(mic: MicSource) -> None:
    print("microphone level: say 'testing one two three' at your normal volume now (3 s)...")
    blocks = []
    end = time.monotonic() + 3.0
    while time.monotonic() < end:
        block = mic.read(0.3)
        if block is not None:
            blocks.append(block)
    if not blocks:
        print("  NO AUDIO from the microphone: check the device with scripts/mic_check.py")
        return
    samples = np.concatenate(blocks).astype(np.float64)
    peak, rms = int(np.abs(samples).max()), float(np.sqrt(np.mean(samples**2)))
    verdict = ("too quiet: move closer or raise the input volume" if peak < 1500 else
               "clipping: lower the input volume" if peak >= 32000 else "level looks fine")
    print(f"  peak {peak} ({peak / 32767:.0%} of full scale), RMS {rms:.0f}: {verdict}")


def listen_once(stt: VoskStt, mic: MicSource) -> tuple[Hypothesis, Hypothesis | None]:
    """Listen from now until a final result (or time runs out); returns (free, grammar)."""
    stt.reset()
    while mic.read(0.01) is not None:  # drop audio queued before the prompt
        pass
    started = last_heard = time.monotonic()
    heard_speech = False
    while time.monotonic() - started < LISTEN_S:
        block = mic.read(0.2)
        if block is None:
            continue
        for event in stt.feed(block):
            last_heard = time.monotonic()
            heard_speech = True
            if event.kind == "final" and event.text:
                return event.free or Hypothesis(event.text), event.grammar
        if heard_speech and time.monotonic() - last_heard > SILENCE_END_S:
            break
    for event in stt.flush():
        return event.free or Hypothesis(event.text), event.grammar
    return Hypothesis(), None


def show(attempt: Attempt) -> None:
    free = f"{attempt.free_text!r} ({attempt.free_conf:.2f})" if attempt.free_text else "(nothing)"
    gram = (f"{attempt.grammar_text!r} ({attempt.grammar_conf:.2f})" if attempt.grammar_text
            else "(nothing)")
    print(f"     free     : {free}\n     grammar  : {gram}\n"
          f"     decided  : {attempt.path} ({attempt.reason})\n"
          f"     router   : {attempt.got}   expected: {attempt.expected}   "
          f"{'OK' if attempt.ok else 'WRONG'}")


def summary(attempts: list[Attempt], model: str) -> None:
    print(f"\n=== summary for {model} ({len(attempts)} attempts) ===")
    print(f"{'you said':<34}{'free heard':<30}{'grammar':<24}{'decided':<17}{'router':<9}result")
    for a in attempts:
        print(f"{a.said:<34}{a.free_text[:28]:<30}{a.grammar_text[:22]:<24}{a.path:<17}"
              f"{a.got:<9}{'ok' if a.ok else 'WRONG'}")

    commands = [a for a in attempts if a.kind == "cmd"]
    stops = [a for a in attempts if a.kind == "stop"]
    chats = [a for a in attempts if a.kind == "chat"]

    def rate(items: list[Attempt]) -> str:
        if not items:
            return "n/a"
        right = sum(a.ok for a in items)
        return f"{right}/{len(items)} ({right / len(items):.0%})"

    dangerous = [a for a in chats if a.got in MOTION]
    false_stops = [a for a in chats if a.got == "stop"]
    missed = [a for a in stops if a.got != "stop"]
    wrong_motion = [a for a in commands if a.got in MOTION and a.got != a.expected]
    print(f"\ncommand accuracy (motion commands)   : {rate(commands)}")
    print(f"stop accuracy                         : {rate(stops)}")
    print(f"chat routed correctly                 : {rate(chats)}")
    print(f"DANGEROUS false positives (chat -> motion): {len(dangerous)}")
    for a in dangerous:
        print(f"    said {a.said!r}: free {a.free_text!r}, grammar {a.grammar_text!r} -> {a.got} "
              f"[{a.path}]")
    print(f"wrong motion command (said X, did Y)  : {len(wrong_motion)}")
    for a in wrong_motion:
        print(f"    said {a.said!r}: -> {a.got} [{a.path}]")
    print(f"false stops from chat (safe side)     : {len(false_stops)}")
    print(f"MISSED STOPS                          : {len(missed)}")
    for a in missed:
        print(f"    said {a.said!r}: free {a.free_text!r}, grammar {a.grammar_text!r} -> {a.got}")
    print(f"thresholds: STT_STOP_CONF={config.STT_STOP_CONF}, "
          f"STT_GRAMMAR_CONF={config.STT_GRAMMAR_CONF}, "
          f"extra words {config.STT_GRAMMAR_EXTRA_WORDS}/{config.STT_STOP_EXTRA_WORDS}")


def replay(path: Path) -> int:
    attempts = []
    for line in path.read_text().splitlines():
        raw = json.loads(line)
        free = hypothesis_from(raw["free_text"], raw["free_words"])
        grammar = hypothesis_from(raw["grammar_text"], raw["grammar_words"])
        attempts.append(judge(raw["said"], free, grammar if grammar.text else None))
    summary(attempts, f"{path.name} (replayed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=None, help="us, in, a directory name or a path")
    parser.add_argument("--only", choices=("commands", "chat"), default=None)
    parser.add_argument("--mic", "--device", dest="mic", type=int, default=config.MIC_DEVICE,
                        help="input device index")
    parser.add_argument("--repeats", type=int, default=3, help="times each phrase is said")
    parser.add_argument("--replay", type=Path, help="re-judge a saved run with current thresholds")
    args = parser.parse_args()
    if args.replay:
        return replay(args.replay)
    try:
        stt = VoskStt(args.model)
    except SttError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    phrases: list[str] = []
    if args.only != "chat":
        for phrase in (*config.ROUTER_PHRASES, *config.ROUTER_ALIASES, *config.STOP_WORDS):
            if phrase not in phrases:
                phrases.append(phrase)
    if args.only != "commands":
        phrases += CHAT_SENTENCES
    name = stt.model_path.name
    saved = config.LOG_DIR / f"stt_check-{args.model or config.VOSK_MODEL_DEFAULT}.jsonl"
    mic = MicSource(device=args.mic)
    mic.start()
    attempts: list[Attempt] = []
    print(f"model: {name}   {len(phrases)} phrases x {args.repeats} = "
          f"{len(phrases) * args.repeats} attempts   (saved to {saved})")
    try:
        level_check(mic)
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with saved.open("w", encoding="utf-8") as out:
            for index, phrase in enumerate(phrases, 1):
                input(f"\n[{index}/{len(phrases)}] say \"{phrase}\" {args.repeats} times, "
                      "one after another.  Enter to start ")
                for repeat in range(1, args.repeats + 1):
                    print(f"  ({repeat}/{args.repeats}) listening...", flush=True)
                    free, grammar = listen_once(stt, mic)
                    attempt = judge(phrase, free, grammar)
                    show(attempt)
                    attempts.append(attempt)
                    out.write(json.dumps(asdict(attempt)) + "\n")
                    out.flush()
                    time.sleep(0.6)
    except (KeyboardInterrupt, EOFError):
        print("\n(stopped early)")
    finally:
        mic.stop()
    if attempts:
        summary(attempts, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
