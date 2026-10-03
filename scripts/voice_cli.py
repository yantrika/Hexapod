#!/usr/bin/env python3
"""The whole voice loop: microphone -> Vosk -> router -> body, hexa answers "okay" (Step 8).

    python scripts/voice_cli.py                  headless body, US English model
    python scripts/voice_cli.py --model in       Indian English model
    python scripts/voice_cli.py --gui            with the PyBullet window (needs the Mesa override)
    python scripts/voice_cli.py --no-speak       no Piper: nothing is said, so no self-hearing

Prints each partial and final text, the route result and every body status. Say "walk forward",
"sit down", "stand up", "turn left", "wave", "stop". A stop word in a partial result stops the
robot at once. While hexa speaks (and a moment after) the microphone is ignored. Ctrl-C quits.
On the dev laptop use a headless body: GUI + Piper + walking gives about 90 ms body ticks.
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
from body.process import BodyProcess  # noqa: E402
from brain.brain_loop import BrainLoop  # noqa: E402
from brain.status_hub import StatusHub  # noqa: E402
from brain.voice_loop import VoiceEvent, VoiceLoop  # noqa: E402
from bridge import make_bridge, new_command  # noqa: E402
from commandline import format_status  # noqa: E402
from scripts.brain_cli import describe  # noqa: E402
from voice.audio import MicSource  # noqa: E402
from voice.playback import Playback, SoundDeviceSink  # noqa: E402
from voice.stt import SttError, VoskStt  # noqa: E402
from voice.tts import PiperEngine, TtsError  # noqa: E402


def print_event(event: VoiceEvent) -> None:
    if event.kind == "partial":
        print(f"  ... {event.text}", flush=True)
    elif event.kind == "final":
        print(f"heard: {event.text!r}" if event.text else "heard: (nothing)", flush=True)
    elif event.kind == "route" and event.route is not None:
        early = " [early, from a partial]" if event.early_stop else ""
        print(f"{describe(event.route)}{early}", flush=True)


def print_statuses(hub: StatusHub, done: threading.Event) -> None:
    subscription = hub.subscribe("printer")
    while not done.is_set():
        status = subscription.get(timeout=0.1)
        if status is not None:
            print(format_status(status), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gui", action="store_true", help="PyBullet window (default: headless)")
    parser.add_argument("--model", default=None, help="us, in, a directory name or a path")
    parser.add_argument("--no-speak", action="store_true", help="do not load Piper or say okay")
    parser.add_argument("--device", type=int, default=config.MIC_DEVICE, help="input device index")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")

    try:
        stt = VoskStt(args.model)
    except SttError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    playback: Playback | None = None
    speaking = threading.Event()
    if not args.no_speak:
        engine = PiperEngine()
        try:
            engine.check_installed()
            engine.start()  # pay the model load now, not at the first "okay"
        except TtsError as error:
            print(f"error: {error} (or use --no-speak)", file=sys.stderr)
            return 1
        playback = Playback(engine, SoundDeviceSink(), speaking=speaking)
        playback.start()

    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    hub = StatusHub(bridge)
    hub.start()
    brain = BrainLoop(bridge, hub=hub)
    voice = VoiceLoop(MicSource(device=args.device), stt, brain, speaking, playback, print_event)
    done = threading.Event()
    threading.Thread(target=print_statuses, args=(hub, done), daemon=True).start()
    try:
        if not body.wait_ready():
            print("the body process did not start", file=sys.stderr)
            return 1
        voice.start()
        print(f"listening (model {stt.model_path.name}). Say 'walk forward', 'sit down', "
              "'stand up', 'turn left', 'wave', 'stop'. Ctrl-C quits.", flush=True)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        done.set()
        bridge.send(new_command("stop"))
        voice.shutdown()
        if playback is not None:
            playback.shutdown()
        brain.close()
        hub.stop()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
