#!/usr/bin/env python3
"""What push-to-talk saves: Vosk CPU and body tick time with PTT idle, PTT listening, and always.

    python scripts/cool_run.py -- nice -n 19 python scripts/measure_ptt.py

A speech mix (Piper renders it once, then Piper is closed) streams in real time into a real
``VoiceLoop`` (Vosk, 2 recognizers) beside a headless body. Three windows: ptt idle (nobody
presses: the recognizers get no audio), ptt listening (held on the whole window) and always.
For each: this process's CPU share (Vosk is the load) and the body's mean/worst tick work time.
One process at a time; no sound is played.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import config  # noqa: E402
from body.process import BodyProbe, BodyProcess  # noqa: E402
from brain.brain_loop import BrainLoop  # noqa: E402
from brain.status_hub import StatusHub  # noqa: E402
from brain.voice_loop import VoiceLoop  # noqa: E402
from bridge import make_bridge, new_command  # noqa: E402
from scripts.measure_voice import WINDOW_S, render_speech, speech_mix  # noqa: E402
from voice.audio import FileSource  # noqa: E402
from voice.ptt import PushToTalk  # noqa: E402
from voice.stt import VoskStt  # noqa: E402
from voice.tts import PiperEngine, TtsError  # noqa: E402


def window(label: str, body: BodyProcess, probe: BodyProbe, brain: BrainLoop, stt: VoskStt,
           audio: np.ndarray, mode: str) -> None:
    ptt = PushToTalk() if mode != "always" else None
    stt.reset()
    loop = VoiceLoop(FileSource(audio, realtime=True, pad_silence_s=0.0), stt, brain,
                     threading.Event(), None, ptt=ptt)
    loop.start()
    if ptt is not None and mode == "ptt-listening":
        ptt.press()
    cpu0 = time.process_time()
    probe.reset_window()
    started = time.monotonic()
    next_beat = 0.0
    while time.monotonic() - started < WINDOW_S:
        if time.monotonic() >= next_beat:
            body.bridge.send(new_command("heartbeat", {}))
            next_beat = time.monotonic() + 1.0 / config.HEARTBEAT_HZ
        brain.pump()
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    ticks, mean, worst = probe.window()
    cpu = time.process_time() - cpu0
    fed = loop.blocks_seen - loop.blocks_idle
    print(f"{label}: this process CPU {cpu / elapsed * 100:.0f} % of one core; body tick work mean "
          f"{mean * 1000:.2f} ms, worst {worst * 1000:.1f} ms, {ticks / elapsed:.1f} ticks/s; "
          f"blocks read {loop.blocks_seen}, fed to Vosk {fed}", flush=True)
    loop.shutdown()


def main() -> int:
    engine = PiperEngine()
    try:
        engine.check_installed()
        engine.start()
    except TtsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    audio = speech_mix(render_speech(engine), WINDOW_S + 4)
    engine.close()
    seconds = len(audio) / config.AUDIO_SAMPLE_RATE
    print(f"this process nice {os.nice(0)}; speech mix {seconds:.0f} s")
    stt = VoskStt()
    bridge = make_bridge()
    probe = BodyProbe()
    body = BodyProcess(bridge, headless=True, probe=probe)
    body.start()
    hub = StatusHub(bridge)
    hub.start()
    brain = BrainLoop(bridge, hub=hub)
    try:
        if not body.wait_ready():
            print("error: the body did not become ready", file=sys.stderr)
            return 1
        bridge.send(new_command("stand", {}))
        time.sleep(3.0)
        for label, mode in (("ptt idle (not pressed)", "ptt-idle"),
                            ("ptt listening (held)", "ptt-listening"),
                            ("always listening", "always")):
            window(label, body, probe, brain, stt, audio, mode)
            time.sleep(6.0)  # let the CPU cool between windows
    finally:
        bridge.send(new_command("stop", {}))
        brain.close()
        hub.stop()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
