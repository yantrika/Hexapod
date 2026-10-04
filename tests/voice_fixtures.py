"""Shared fixtures for the tests that use the real Vosk model and Piper-rendered speech.

They skip (not fail) when the models were not downloaded: run ``scripts/fetch_models.sh``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from voice.stt import SttError, VoskStt
from voice.tts import PiperEngine, TtsError, read_wav, write_wav

PHRASES = [
    "sit down", "walk forward", "I sat down for lunch", "stop", "stand up", "turn left", "wave",
    "what is the weather today", "I'll walk you through it",
    "turn left and then walk forward for a while", "turn up the music",
]


@pytest.fixture(scope="session")
def speech_wavs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Each test phrase rendered once by the real Piper (one process for all)."""
    directory = tmp_path_factory.mktemp("speech")
    engine = PiperEngine()
    try:
        engine.check_installed()
        paths = {}
        for index, text in enumerate(PHRASES):
            path = directory / f"{index}.wav"
            write_wav(path, engine.synthesize(text))
            paths[text] = path
        return paths
    except TtsError as error:
        pytest.skip(f"Piper is not available: {error}")
    finally:
        engine.close()


@pytest.fixture(scope="session")
def shared_vosk() -> VoskStt:
    try:
        return VoskStt()
    except SttError as error:
        pytest.skip(str(error))


@pytest.fixture
def vosk_stt(shared_vosk: VoskStt) -> Iterator[VoskStt]:
    shared_vosk.reset()
    yield shared_vosk
    shared_vosk.reset()


def samples_of(path: Path) -> np.ndarray:
    return read_wav(path).samples
