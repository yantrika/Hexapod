#!/usr/bin/env python3
"""Measure the local Ollama model: first token, tokens per second, RAM, peak temperature, and the
body's tick time while it generates. Needs a running Ollama server (config.OLLAMA_URL).

    python scripts/cool_run.py --start-below 62 --kill-at 82 -- nice -n 19 \
        python scripts/measure_chat.py
    python scripts/measure_chat.py --with-voice       # plus Vosk listening and Piper idle

Asks for a 20-token and a 60-token reply (``num_predict``), after one warm-up request that loads
the model (its load time is reported separately). A headless idle body runs during the replies
so its mean tick time shows what the LLM costs the control loop. Verdict: the model is usable on
this machine only if it makes >= 3 tokens/s and the first token arrives within 5 s.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import requests  # noqa: E402

import config  # noqa: E402
from body.process import BodyProbe, BodyProcess  # noqa: E402
from bridge import make_bridge  # noqa: E402
from scripts.cool_run import read_temperature_c  # noqa: E402

MIN_TOKENS_PER_S = 3.0
MAX_FIRST_TOKEN_S = 5.0
PROMPTS = {
    20: "Say hello and tell me who you are.",
    60: "Tell me a short story about a small robot that learns to walk.",
}


@dataclass
class Reply:
    wanted: int
    text: str
    first_token_s: float
    wall_s: float
    tokens: int
    tokens_per_s: float  # from Ollama's own eval counters
    load_s: float
    prompt_eval_s: float
    peak_c: float


class Thermometer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop_flag = threading.Event()
        self.peak = 0.0

    def run(self) -> None:
        while not self.stop_flag.is_set():
            self.peak = max(self.peak, read_temperature_c() or 0.0)
            self.stop_flag.wait(0.5)


def container_memory(name: str) -> str:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.MemUsage}} (cpu {{.CPUPerc}})", name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        return out.stdout.strip() or "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def ask(url: str, model: str, wanted: int, timeout_s: float) -> Reply:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPTS[wanted]}],
        "stream": True,
        "keep_alive": "10m",
        "options": {"num_predict": wanted, "temperature": 0.7},
    }
    thermometer = Thermometer()
    thermometer.start()
    started = time.perf_counter()
    first = 0.0
    text = ""
    final: dict[str, object] = {}
    try:
        with requests.post(
            f"{url}/api/chat", json=body, stream=True, timeout=timeout_s
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece and not first:
                    first = time.perf_counter() - started
                text += piece
                if chunk.get("done"):
                    final = chunk
    finally:
        thermometer.stop_flag.set()
        thermometer.join(2)
    wall = time.perf_counter() - started
    eval_count = int(final.get("eval_count", 0))  # type: ignore[call-overload]
    eval_ns = int(final.get("eval_duration", 0))  # type: ignore[call-overload]
    return Reply(
        wanted, text, first, wall, eval_count,
        eval_count / (eval_ns / 1e9) if eval_ns else 0.0,
        int(final.get("load_duration", 0)) / 1e9,  # type: ignore[call-overload]
        int(final.get("prompt_eval_duration", 0)) / 1e9,  # type: ignore[call-overload]
        thermometer.peak,
    )


def start_voice(
    enabled: bool, body: BodyProcess | None, probe: BodyProbe
) -> Callable[[], None] | None:
    """Vosk listening to silence (2 recognizers) and Piper loaded and idle: the standing load of
    the voice loop. Returns a function that stops them."""
    import numpy as np

    from brain.brain_loop import BrainLoop
    from brain.voice_loop import VoiceLoop
    from voice.audio import FileSource
    from voice.stt import VoskStt
    from voice.tts import PiperEngine

    if not enabled or body is None:
        return None
    engine = PiperEngine()
    engine.start()
    engine.synthesize("Warm up.")  # Piper is now loaded and idle
    brain = BrainLoop(body.bridge)
    silence = np.zeros(config.AUDIO_SAMPLE_RATE * 600, dtype=np.int16)  # up to 10 minutes
    loop = VoiceLoop(FileSource(silence, realtime=True, pad_silence_s=0.0), VoskStt(), brain,
                     threading.Event(), None)
    loop.start()
    time.sleep(3.0)
    probe.reset_window()
    time.sleep(5.0)
    ticks, mean, worst = probe.window()
    print(f"with Vosk on silence + Piper idle, no LLM: mean tick {mean * 1000:.2f} ms "
          f"(worst {worst * 1000:.1f} ms)")

    def stop() -> None:
        loop.shutdown()
        brain.close()
        engine.close()

    return stop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=config.OLLAMA_MODEL)
    parser.add_argument("--url", default=config.OLLAMA_URL)
    parser.add_argument("--container", default="ollama", help="docker container name, for RAM")
    parser.add_argument("--no-body", action="store_true", help="skip the idle headless body")
    parser.add_argument("--with-voice", action="store_true",
                        help="also run Vosk on silence and a loaded, idle Piper (the real load)")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    try:
        version = requests.get(f"{args.url}/api/version", timeout=5).json()["version"]
    except (requests.RequestException, KeyError, ValueError) as error:
        print(f"error: no Ollama server at {args.url}: {error}", file=sys.stderr)
        return 1
    print(f"ollama {version}, model {args.model}, this process nice {os.nice(0)}, "
          f"start {read_temperature_c():.0f} C")

    probe, body = BodyProbe(), None
    if not args.no_body:
        bridge = make_bridge()
        body = BodyProcess(bridge, headless=True, probe=probe)
        body.start()
        if not body.wait_ready():
            print("error: the body did not start", file=sys.stderr)
            return 1
        time.sleep(2.0)
        probe.reset_window()
        time.sleep(4.0)
        ticks, mean, worst = probe.window()
        print(f"idle body, no LLM: mean tick {mean * 1000:.2f} ms (worst {worst * 1000:.1f} ms)")
    voice_stack = start_voice(args.with_voice, body, probe) if args.with_voice else None
    try:
        print("warm-up (loads the model into memory)...", flush=True)
        warm = ask(args.url, args.model, 20, args.timeout)
        print(f"  model load {warm.load_s:.1f} s, first reply took {warm.wall_s:.1f} s, "
              f"peak {warm.peak_c:.0f} C")
        print(f"container memory: {container_memory(args.container)}")
        replies: list[Reply] = []
        for wanted in (20, 60):
            for _ in range(2):
                if body is not None:
                    probe.reset_window()
                reply = ask(args.url, args.model, wanted, args.timeout)
                tick = ""
                if body is not None:
                    ticks, mean, worst = probe.window()
                    tick = f"; body tick mean {mean * 1000:.2f} ms worst {worst * 1000:.0f} ms"
                replies.append(reply)
                print(f"{wanted}-token reply: first token {reply.first_token_s:.2f} s, "
                      f"{reply.tokens} tokens at {reply.tokens_per_s:.1f} tok/s, wall "
                      f"{reply.wall_s:.1f} s, prompt eval {reply.prompt_eval_s:.2f} s, peak "
                      f"{reply.peak_c:.0f} C{tick}")
                print(f"   {reply.text.strip()[:110]!r}")
        print(f"container memory after: {container_memory(args.container)}")
    finally:
        if voice_stack is not None:
            voice_stack()
        if body is not None:
            body.shutdown()
    rate = statistics.median(r.tokens_per_s for r in replies)
    first = statistics.median(r.first_token_s for r in replies)
    print(f"\nmedian {rate:.1f} tokens/s, median first token {first:.2f} s, peak "
          f"{max(r.peak_c for r in replies):.0f} C")
    usable = rate >= MIN_TOKENS_PER_S and first <= MAX_FIRST_TOKEN_S
    slow = (f"TOO SLOW here (need >= {MIN_TOKENS_PER_S} tokens/s and first token <= "
            f"{MAX_FIRST_TOKEN_S:.0f} s): develop against FakeChat, measure the real model "
            "on the Pi")
    print("VERDICT: " + ("usable on this machine" if usable else slow))
    return 0 if usable else 2


if __name__ == "__main__":
    raise SystemExit(main())
