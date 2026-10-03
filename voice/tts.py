"""Text-to-speech: a small engine interface and Piper as ONE long-lived subprocess.

``TtsEngine.synthesize(text)`` returns an ``AudioClip`` (int16 mono samples plus the sample
rate). ``PiperEngine`` starts the Piper binary once (lazily, or ``start()`` at boot) and feeds
it one line per sentence on stdin. Piper runs in ``--output_dir`` mode, so it answers each line
with the path of the WAV it wrote: a clean end-of-utterance marker, which raw stdout lacks.
Never one process per sentence: the model load costs about 1.5 s per call on the dev laptop.

Failure handling: if Piper dies or a sentence exceeds its timeout, the failure is logged, the
process is killed and restarted once, and that one utterance fails with ``TtsError`` (the next
one works). Piper exits when its stdin closes, so it also exits when the parent dies; ``close()``
never leaves an orphan. This module makes no sound: only ``voice/playback.py`` touches the
audio output.
"""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

import config

logger = logging.getLogger(__name__)

# Piper's onnxruntime and BLAS must not spin up a thread per core on the dev laptop.
_THREAD_PINS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


class TtsError(RuntimeError):
    """A sentence could not be synthesized (the message says why and what to do)."""


class _ProcessFailure(TtsError):
    """Piper died, hung or stopped answering: the process is restarted."""


@dataclass(frozen=True)
class AudioClip:
    """Mono int16 audio at ``sample_rate`` Hz."""

    samples: NDArray[np.int16]
    sample_rate: int

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0


class TtsEngine(Protocol):
    """Text in, audio out. Called from one thread at a time."""

    def synthesize(self, text: str) -> AudioClip: ...

    def close(self) -> None: ...


# --- text -----------------------------------------------------------------------------------
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SPEAKABLE = re.compile(r"[A-Za-z0-9]")
_MIN_SENTENCE_CHARS = 3  # "Hi." stays; a stray "A." is glued onto the next sentence


def split_sentences(text: str) -> list[str]:
    """Split *text* into sentences for sentence-by-sentence synthesis (prefetch needs units).

    Lines without any letter or digit are dropped (Piper has nothing to say for them).
    """
    sentences: list[str] = []
    carry = ""
    for line in text.splitlines():
        for part in _SENTENCE_END.split(line.strip()):
            part = f"{carry} {part}".strip() if carry else part.strip()
            carry = ""
            if not _SPEAKABLE.search(part):
                continue
            if len(part) < _MIN_SENTENCE_CHARS:
                carry = part
                continue
            sentences.append(part)
    if carry and _SPEAKABLE.search(carry):
        sentences.append(carry)
    return sentences


# --- WAV helpers ------------------------------------------------------------------------------
def read_wav(path: Path) -> AudioClip:
    """Read a mono 16-bit WAV file."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise TtsError(f"{path}: expected mono 16-bit audio")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).copy()
        return AudioClip(samples, wav.getframerate())


def write_wav(path: Path, clip: AudioClip) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(clip.sample_rate)
        wav.writeframes(clip.samples.tobytes())


def phrase_path(name: str, directory: Path | None = None) -> Path:
    return (directory or config.PHRASES_DIR) / f"{name}.wav"


def load_phrase(name: str, directory: Path | None = None) -> AudioClip:
    """A pre-rendered phrase from ``config.TTS_PHRASES``; clear error if it was not rendered."""
    if name not in config.TTS_PHRASES:
        raise TtsError(f"unknown phrase {name!r}; known: {', '.join(sorted(config.TTS_PHRASES))}")
    path = phrase_path(name, directory)
    if not path.is_file():
        raise TtsError(
            f"pre-rendered phrase {name!r} is missing ({path}); run scripts/prerender_phrases.py"
        )
    return read_wav(path)


# --- Piper --------------------------------------------------------------------------------------
class PiperEngine:
    """One long-lived Piper process: lines on stdin, a WAV path per line on stdout.

    Not thread-safe by itself beyond a lock: ``synthesize`` calls are serialised.
    """

    def __init__(
        self,
        binary: str | Path = config.PIPER_BINARY,
        model: str | Path = config.PIPER_MODEL_PATH,
        synth_timeout_s: float = config.TTS_SYNTH_TIMEOUT_S,
        start_timeout_s: float = config.TTS_START_TIMEOUT_S,
        stop_timeout_s: float = config.TTS_STOP_TIMEOUT_S,
        nice: int = config.PIPER_NICE,
        cpu_list: str | None = config.PIPER_CPU_LIST,
    ) -> None:
        self.binary = Path(binary)
        self.model = Path(model)
        self.synth_timeout_s = synth_timeout_s
        self.start_timeout_s = start_timeout_s
        self.stop_timeout_s = stop_timeout_s
        self.nice = nice
        self.cpu_list = cpu_list
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._scratch: Path | None = None
        self._first_line_pending = True  # the first line after a start also pays the model load
        self._buffer = b""
        self._closed = False
        self.restarts = 0

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    # -- lifecycle ---------------------------------------------------------------------------
    def check_installed(self) -> None:
        """Raise a clear ``TtsError`` when the binary or voice is missing."""
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise TtsError(
                f"Piper binary not found or not executable: {self.binary}. "
                "Run scripts/fetch_models.sh"
            )
        if not self.model.is_file():
            raise TtsError(
                f"Piper voice model not found: {self.model}. Run scripts/fetch_models.sh"
            )

    def start(self) -> None:
        """Start Piper now (otherwise it starts on the first sentence)."""
        with self._lock:
            self._ensure_started()

    def _ensure_started(self) -> None:
        if self._closed:
            raise TtsError("the Piper engine is closed")
        if self._process is not None and self._process.poll() is None:
            return
        self._discard_process()
        self.check_installed()
        self._scratch = Path(tempfile.mkdtemp(prefix="hexa-piper-"))
        env = dict(os.environ)
        for name in _THREAD_PINS:
            env[name] = "1"
        command = [
            *self._wrapper(),
            str(self.binary), "-m", str(self.model), "--output_dir", str(self._scratch), "-q",
        ]
        try:
            with (self._scratch / "stderr.txt").open("wb") as stderr:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    env=env,
                    cwd=str(self.binary.parent),
                )
        except OSError as error:
            self._discard_process()
            raise TtsError(f"could not start Piper ({self.binary}): {error}") from error
        self._buffer = b""
        self._first_line_pending = True
        logger.info("piper started (pid %d)", self._process.pid)

    def _wrapper(self) -> list[str]:
        """``nice`` / ``taskset`` prefix (they exec Piper, so the pid is Piper's own)."""
        prefix: list[str] = []
        if self.cpu_list is not None and (taskset := shutil.which("taskset")):
            prefix += [taskset, "-c", self.cpu_list]
        if self.nice and (nice := shutil.which("nice")):
            prefix += [nice, "-n", str(self.nice)]
        return prefix

    def _stderr_tail(self) -> str:
        if self._scratch is None:
            return ""
        try:
            return (self._scratch / "stderr.txt").read_text(errors="replace").strip()[-300:]
        except OSError:
            return ""

    def _discard_process(self) -> None:
        """Kill and reap the process (if any) and delete its scratch directory."""
        process, scratch = self._process, self._scratch
        self._process, self._scratch = None, None
        if process is not None:
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=self.stop_timeout_s)  # Piper exits when stdin closes
                except subprocess.TimeoutExpired:
                    logger.warning("piper did not exit after stdin closed; killing it")
                    process.kill()
                    process.wait()
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    def close(self) -> None:
        """Stop Piper for good. Safe to call twice; leaves no process behind."""
        self._closed = True
        if not self._lock.acquire(timeout=0.2):  # a synthesis is in flight: kill it so it returns
            process = self._process
            if process is not None:
                process.kill()
            self._lock.acquire()
        try:
            self._discard_process()
        finally:
            self._lock.release()

    def __enter__(self) -> PiperEngine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- synthesis ---------------------------------------------------------------------------
    def synthesize(self, text: str) -> AudioClip:
        line = " ".join(text.split())
        if not _SPEAKABLE.search(line):
            raise TtsError(f"nothing to say in {text!r}")
        with self._lock:
            try:
                self._ensure_started()
                return self._synthesize_locked(line)
            except _ProcessFailure as error:
                self._recover(str(error))
                raise

    def _recover(self, reason: str) -> None:
        """Log, kill the broken process and start a fresh one (one restart attempt)."""
        logger.error("piper failed (%s); restarting it", reason)
        self._discard_process()
        self.restarts += 1
        if self._closed:
            return
        try:
            self._ensure_started()
        except TtsError as error:
            logger.error("piper restart failed: %s (the next sentence will try again)", error)

    def _synthesize_locked(self, line: str) -> AudioClip:
        process = self._process
        assert process is not None and process.stdin is not None
        timeout = self.synth_timeout_s + (self.start_timeout_s if self._first_line_pending else 0)
        try:
            process.stdin.write(line.encode("utf-8") + b"\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise _ProcessFailure(
                f"Piper is not running ({self._stderr_tail() or error})"
            ) from error
        path_text = self._read_line(process, time.monotonic() + timeout, timeout)
        self._first_line_pending = False
        path = Path(path_text)
        try:
            clip = read_wav(path)
        except (OSError, EOFError, wave.Error) as error:
            raise TtsError(f"Piper wrote no usable audio for {line!r}: {error}") from error
        finally:
            path.unlink(missing_ok=True)
        if len(clip.samples) == 0:
            raise TtsError(f"Piper produced no audio for {line!r}")
        return clip

    def _read_line(self, process: subprocess.Popen[bytes], deadline: float, timeout: float) -> str:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _ProcessFailure(
                    f"Piper did not answer within {timeout:.0f} s (hung); restarting it"
                )
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if not ready:
                if process.poll() is not None:
                    raise _ProcessFailure(self._died_message(process))
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                process.wait()
                raise _ProcessFailure(self._died_message(process))
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8", errors="replace").strip()

    def _died_message(self, process: subprocess.Popen[bytes]) -> str:
        detail = self._stderr_tail()
        return f"Piper exited unexpectedly (code {process.poll()}){': ' + detail if detail else ''}"
