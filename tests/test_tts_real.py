"""The one real-audio check: real Piper, no speaker. Excluded by default (marker ``audio``).

Run on a quiet machine: ``nice -n 19 pytest -m audio -s tests/test_tts_real.py``.
It reports time to first audio and the real-time factor of the long-lived engine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import config
from voice.tts import PiperEngine, TtsError

SENTENCES = ["Okay, I am standing up.", "I can't do that right now.", "Hello, I am hexa."]


@pytest.mark.audio
def test_real_piper_time_to_first_audio_and_real_time_factor() -> None:
    engine = PiperEngine()
    try:
        engine.check_installed()
    except TtsError as error:
        pytest.skip(str(error))
    try:
        started = time.perf_counter()
        engine.start()
        engine.synthesize("Warm up.")  # model load: paid once, at startup
        print(f"\nstartup + first sentence: {time.perf_counter() - started:.2f} s")
        for text in SENTENCES:
            began = time.perf_counter()
            clip = engine.synthesize(text)
            took = time.perf_counter() - began
            print(f"{text!r}: first audio after {took:.2f} s, audio {clip.duration_s:.2f} s, "
                  f"real-time factor {took / clip.duration_s:.2f}")
            assert clip.sample_rate > 0 and clip.duration_s > 0.3
            assert took / clip.duration_s < 1.5
        assert engine.restarts == 0
        pid = engine.pid
    finally:
        engine.close()
    assert pid is not None and not Path(f"/proc/{pid}").exists()
    assert config.PIPER_MODEL_PATH.is_file()
