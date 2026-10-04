"""Step 10b: the end-to-end smoke test of ``main.py`` as a real process.

Piper-rendered "sit down" and a chat sentence go in as an audio file (real Vosk, headless body,
FakeChat): the robot sits, the chat sentence gets a fake reply, the process exits 0 and leaves no
orphan. A second test sends SIGTERM / SIGINT to a running ``main.py``. Both skip without models.
Run alone if the laptop is hot: ``pytest tests/test_main_smoke.py``.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import config
from voice.stt import VoskStt
from voice.tts import AudioClip, read_wav, write_wav

MAIN = config.PROJECT_ROOT / "main.py"
BASE = [sys.executable, str(MAIN), "--headless", "--chat", "fake", "--no-speak", "--no-mic"]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Running:
    """A main.py process whose stderr is collected by a thread."""

    def __init__(self, args: list[str], tmp_path: Path) -> None:
        self.lines: list[str] = []
        self.process = subprocess.Popen(
            [*BASE, "--log-file", str(tmp_path / "hexa.log"), *args], stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL, text=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.lines.append(line.rstrip())

    def wait_for(self, text: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(text in line for line in self.lines):
                return True
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        return any(text in line for line in self.lines)

    def finish(self, timeout: float) -> int:
        code = self.process.wait(timeout)
        self._reader.join(5)
        return code

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def body_pid(self) -> int:
        match = re.search(r"body process pid (\d+)", self.text)
        assert match, self.text
        return int(match.group(1))


@pytest.fixture(scope="module")
def models(shared_vosk: VoskStt) -> None:
    """Skips the module when Vosk is missing (``speech_wavs`` skips when Piper is)."""


def test_main_sits_answers_a_chat_sentence_and_exits_cleanly(
    models: None, speech_wavs: dict[str, Path], tmp_path: Path
) -> None:
    sit = read_wav(speech_wavs["sit down"])
    chat = read_wav(speech_wavs["what is the weather today"])
    rate = sit.sample_rate
    gap = np.zeros(int(3 * rate), dtype=np.int16)  # longer than the sit transition
    lead = np.zeros(rate // 2, dtype=np.int16)
    audio = tmp_path / "smoke.wav"
    write_wav(audio, AudioClip(np.concatenate([lead, sit.samples, gap, chat.samples]), rate))

    run = Running(["--listen", "always", "--audio-file", str(audio)], tmp_path)
    try:
        code = run.finish(150)
    except subprocess.TimeoutExpired:
        run.process.kill()
        raise
    text = run.text
    assert code == 0, text
    assert "route command action=sit" in text  # the robot was told to sit ...
    assert re.search(r"status <- done\s+ref=\d+\s+action=sit", text)  # ... and did
    assert "route chat" in text and "hexa says: Hello!" in text  # the fake reply
    assert "hexa stopped" in text
    assert not pid_alive(run.body_pid)  # no orphan body process
    assert (tmp_path / "hexa.log").read_text().count("status <-") >= 2  # one line per status


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_shuts_the_robot_down_cleanly(models: None, tmp_path: Path, sig: int) -> None:
    run = Running([], tmp_path)
    try:
        assert run.wait_for("hexa is ready", 60), run.text
        pid = run.body_pid
        assert pid_alive(pid)
        run.process.send_signal(sig)
        code = run.finish(30)
    except Exception:
        run.process.kill()
        raise
    assert code == 0, run.text
    assert "shutting down" in run.text and "hexa stopped" in run.text
    assert not pid_alive(pid)
