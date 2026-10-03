#!/usr/bin/env python3
"""List the input devices, record 3 s at 16 kHz mono and print peak and RMS.

Run it and speak. Silence gives a peak near 0; normal speech peaks around 3000-20000 (int16).
    python scripts/mic_check.py [--device N] [--seconds 3]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import config  # noqa: E402
from voice.audio import MicSource, list_input_devices  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", type=int, default=config.MIC_DEVICE)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    print("input devices:")
    for index, name, channels, rate, is_default in list_input_devices():
        mark = "*" if is_default else " "
        print(f" {mark}{index:2d} {name} ({channels} in, default {rate:.0f} Hz)")
    print(f"\nrecording {args.seconds:.0f} s at {config.AUDIO_SAMPLE_RATE} Hz mono "
          f"(device {args.device if args.device is not None else 'default'}): speak now")
    mic = MicSource(device=args.device)
    blocks = []
    try:
        mic.start()
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            block = mic.read(0.5)
            if block is not None:
                blocks.append(block)
    except Exception as error:  # noqa: BLE001 - report any device problem plainly
        print(f"error: could not record: {error}", file=sys.stderr)
        return 1
    finally:
        mic.stop()
    if not blocks:
        print("error: no audio arrived from the microphone", file=sys.stderr)
        return 1
    samples = np.concatenate(blocks).astype(np.float64)
    peak = int(np.abs(samples).max())
    rms = float(np.sqrt(np.mean(samples**2)))
    print(f"peak {peak} ({peak / 32767:.1%} of full scale), RMS {rms:.0f}, "
          f"{len(samples) / config.AUDIO_SAMPLE_RATE:.1f} s recorded, {mic.dropped} blocks dropped")
    if peak < 200:
        print("verdict: almost silent: wrong device, muted, or you did not speak")
        return 2
    print("verdict: the microphone works" if peak < 32000 else "verdict: works, but clipping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
