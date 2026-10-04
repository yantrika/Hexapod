#!/usr/bin/env python3
"""End-to-end latency: end of a spoken "sit down" (a WAV) to the first joint-target change.

    python scripts/cool_run.py -- nice -n 19 python scripts/measure_e2e.py --trials 5

Piper renders "sit down" once (the voice from ``config``), then a real ``HexaApp`` (body process
with the ``HEXA_BACKEND`` backend, real Vosk, fake chat, no audio output) listens to it through a
``FileSource`` paced like a microphone. Per trial the robot is first stood up by typed text, then
the clip plays; the time runs from the END of the speech (the file's own length, not the padding
that lets Vosk finish) to ``BodyProbe.change_time``, the first joint-target change. The numbers
include Vosk's endpointing silence, so they are what a person waits after they stop speaking.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import config  # noqa: E402
from body.process import BodyProbe  # noqa: E402
from brain.app import AppOptions, HexaApp  # noqa: E402
from scripts.cool_run import read_temperature_c  # noqa: E402
from voice.audio import FileSource  # noqa: E402
from voice.tts import PiperEngine, write_wav  # noqa: E402

LEAD_S = 6.0  # silence before the clip: the robot stands up (typed) during it
PHRASE = "sit down"


def one_trial(wav: Path, speech_s: float) -> float | None:
    """Milliseconds from the end of the speech to the first joint change (None: no change)."""
    probe = BodyProbe()
    source = FileSource(wav, realtime=True, pad_silence_s=4.0, lead_silence_s=LEAD_S)
    app = HexaApp(AppOptions(listen="always", chat="fake", no_speak=True, no_mic=True),
                  probe=probe, source=source)
    app.start()
    try:
        time.sleep(1.0)
        app.submit_text("stand up")
        while source.started_at is None:
            time.sleep(0.05)
        time.sleep(LEAD_S - 1.0)  # standing is done; now the clip starts
        before = probe.get("change_time")
        speech_end = source.started_at + LEAD_S + speech_s
        deadline = speech_end + 6.0
        while time.monotonic() < deadline:
            change = probe.get("change_time")
            if change > before and change >= speech_end:
                return (change - speech_end) * 1000.0
            time.sleep(0.002)
        return None
    finally:
        app.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    wav = Path("/tmp/hexa_sit_down.wav")
    with PiperEngine(binary=config.PIPER_BINARY, model=config.PIPER_MODEL_PATH) as engine:
        clip = engine.synthesize(PHRASE)
    write_wav(wav, clip)
    speech_s = len(clip.samples) / clip.sample_rate
    print(f"clip: {PHRASE!r}, {speech_s:.2f} s; backend {config.BACKEND_DEFAULT}")
    results: list[float] = []
    peak = read_temperature_c() or 0.0
    for trial in range(args.trials):
        value = one_trial(wav, speech_s)
        peak = max(peak, read_temperature_c() or 0.0)
        print(f"trial {trial + 1}: {'no change' if value is None else f'{value:.0f} ms'}")
        if value is not None:
            results.append(value)
    if results:
        median = statistics.median(results)
        print(f"end-to-end (speech end to joint change): median {median:.0f} ms, "
              f"max {max(results):.0f} ms, {len(results)}/{args.trials} trials moved")
    print(f"peak temperature {peak:.0f} C")
    return 0 if len(results) == args.trials else 1


if __name__ == "__main__":
    raise SystemExit(main())
