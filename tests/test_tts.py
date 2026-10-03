"""Step 7 checks: the Piper wrapper against a fake ``piper`` executable (no model, no audio).

The fake speaks Piper's protocol: ``-m MODEL --output_dir DIR -q``, one text line on stdin, the
path of a WAV it wrote on stdout. Special lines make it crash (CRASH) or hang (HANG).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

import config
from tests.fakes import wait_until
from voice.tts import AudioClip, PiperEngine, TtsError, load_phrase, split_sentences, write_wav

FAKE_PIPER = textwrap.dedent(
    """\
    #!{python}
    import os, sys, time, wave
    args = sys.argv[1:]
    out = args[args.index("--output_dir") + 1]
    with open(os.path.join(out, "env.txt"), "w") as handle:
        handle.write(os.environ["OMP_NUM_THREADS"] + " " + os.environ["OPENBLAS_NUM_THREADS"])
    count = 0
    for line in sys.stdin:
        text = line.strip()
        if text == "CRASH":
            sys.stderr.write("fake piper exploded")
            sys.stderr.flush()
            os._exit(3)
        if text == "HANG":
            time.sleep(1000)
        count += 1
        path = os.path.join(out, f"{{count}}.wav")
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\\x01\\x00" * (1600 * len(text.split())))
        print(path, flush=True)
    """
)


@pytest.fixture
def fake_piper(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "piper"
    binary.write_text(FAKE_PIPER.format(python=sys.executable))
    binary.chmod(0o755)
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"not a real model")
    return binary, model


@pytest.fixture
def engine(fake_piper: tuple[Path, Path]) -> Iterator[PiperEngine]:
    binary, model = fake_piper
    made = PiperEngine(binary, model, synth_timeout_s=0.6, start_timeout_s=5.0, stop_timeout_s=1.0)
    yield made
    made.close()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"  # a zombie is dead, just not reaped yet


# --- text -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello there.", ["Hello there."]),
        ("One. Two! Three? Four", ["One.", "Two!", "Three?", "Four"]),
        ("  spaced   out.   Next one.  ", ["spaced   out.", "Next one."]),
        ("line one\nline two", ["line one", "line two"]),
        ("A. Longer sentence here.", ["A. Longer sentence here."]),  # a stray "A." is glued on
        ("...  !!!", []),
        ("", []),
        ("3.5 is a number. Yes.", ["3.5 is a number.", "Yes."]),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


# --- engine ---------------------------------------------------------------------------------
def test_synthesize_returns_int16_audio_at_the_voice_rate(engine: PiperEngine) -> None:
    clip = engine.synthesize("hello there world")
    assert clip.samples.dtype == np.int16 and clip.sample_rate == 16000
    assert clip.duration_s == pytest.approx(0.3)  # the fake: 0.1 s per word


def test_one_process_serves_every_sentence(engine: PiperEngine) -> None:
    engine.synthesize("first")
    pid = engine.pid
    assert pid is not None
    for text in ("second", "third", "fourth"):
        engine.synthesize(text)
    assert engine.pid == pid and engine.restarts == 0  # never one process per sentence


def test_the_engine_starts_lazily(engine: PiperEngine) -> None:
    assert engine.pid is None
    engine.synthesize("now")
    assert engine.pid is not None


def test_scratch_wavs_are_deleted_and_threads_are_pinned(engine: PiperEngine) -> None:
    engine.synthesize("a b c")
    scratch = engine._scratch
    assert scratch is not None
    assert sorted(path.name for path in scratch.glob("*.wav")) == []
    assert (scratch / "env.txt").read_text().split() == ["1", "1"]


def test_unspeakable_text_is_a_clear_error(engine: PiperEngine) -> None:
    with pytest.raises(TtsError, match="nothing to say"):
        engine.synthesize("... !!")


def test_missing_binary_is_a_clear_error_not_a_traceback(tmp_path: Path) -> None:
    missing = PiperEngine(tmp_path / "nope" / "piper", tmp_path / "voice.onnx")
    with pytest.raises(TtsError, match=r"Piper binary not found.*fetch_models\.sh"):
        missing.synthesize("hello")
    with pytest.raises(TtsError, match="Piper binary not found"):
        missing.check_installed()


def test_missing_voice_is_a_clear_error(fake_piper: tuple[Path, Path], tmp_path: Path) -> None:
    binary, _ = fake_piper
    engine = PiperEngine(binary, tmp_path / "gone.onnx")
    with pytest.raises(TtsError, match=r"voice model not found.*fetch_models\.sh"):
        engine.synthesize("hello")
    assert engine.pid is None  # nothing was started


def test_a_crash_fails_that_sentence_restarts_once_and_the_next_works(engine: PiperEngine) -> None:
    engine.synthesize("warm up")
    old_pid = engine.pid
    with pytest.raises(TtsError, match=r"exited unexpectedly.*code 3.*exploded"):
        engine.synthesize("CRASH")
    assert engine.restarts == 1
    assert engine.pid is not None and engine.pid != old_pid  # a fresh process is already up
    assert old_pid is not None and not alive(old_pid)
    assert engine.synthesize("back again").duration_s > 0


def test_a_hang_times_out_kills_the_process_and_recovers(engine: PiperEngine) -> None:
    engine.synthesize("warm up")  # the first line also pays the start allowance
    old_pid = engine.pid
    started = time.monotonic()
    with pytest.raises(TtsError, match="did not answer within"):
        engine.synthesize("HANG")
    assert time.monotonic() - started < 3.0
    assert engine.restarts == 1
    assert old_pid is not None and not alive(old_pid)  # no stuck process left behind
    assert engine.synthesize("fine now").duration_s > 0


def test_a_failed_restart_does_not_raise_and_the_next_call_tries_again(
    fake_piper: tuple[Path, Path], tmp_path: Path
) -> None:
    binary, model = fake_piper
    engine = PiperEngine(binary, model, synth_timeout_s=0.6, start_timeout_s=5.0)
    try:
        engine.synthesize("warm up")
        moved = tmp_path / "piper.moved"
        binary.rename(moved)
        with pytest.raises(TtsError, match="exited unexpectedly"):
            engine.synthesize("CRASH")  # restart fails: the binary is gone
        assert engine.pid is None
        with pytest.raises(TtsError, match="Piper binary not found"):
            engine.synthesize("still gone")
        moved.rename(binary)
        assert engine.synthesize("restored").duration_s > 0
    finally:
        engine.close()


def test_close_leaves_no_process_and_is_idempotent(engine: PiperEngine) -> None:
    engine.synthesize("hello")
    pid = engine.pid
    assert pid is not None and alive(pid)
    engine.close()
    engine.close()
    assert not alive(pid)
    assert engine._scratch is None  # the scratch directory is gone
    with pytest.raises(TtsError, match="closed"):
        engine.synthesize("after close")


def test_close_while_a_sentence_hangs_returns_promptly(fake_piper: tuple[Path, Path]) -> None:
    import threading

    binary, model = fake_piper
    engine = PiperEngine(binary, model, synth_timeout_s=30.0, start_timeout_s=30.0)
    engine.synthesize("warm up")
    pid = engine.pid
    assert pid is not None
    outcome: list[BaseException] = []

    def hang() -> None:
        try:
            engine.synthesize("HANG")
        except TtsError as error:
            outcome.append(error)

    thread = threading.Thread(target=hang)
    thread.start()
    time.sleep(0.3)
    started = time.monotonic()
    engine.close()
    thread.join(5.0)
    assert time.monotonic() - started < 3.0 and not thread.is_alive()
    assert outcome and not alive(pid)


def test_piper_exits_when_its_parent_is_killed(fake_piper: tuple[Path, Path]) -> None:
    """The orphan check: SIGKILL the parent (no cleanup runs); Piper must still go away."""
    binary, model = fake_piper
    code = (
        "import sys, time\n"
        "from voice.tts import PiperEngine\n"
        f"engine = PiperEngine({str(binary)!r}, {str(model)!r})\n"
        "engine.synthesize('hello')\n"
        "print(engine.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, cwd=config.PROJECT_ROOT
    )
    assert parent.stdout is not None
    piper_pid = int(parent.stdout.readline())
    try:
        assert alive(piper_pid)
        parent.send_signal(signal.SIGKILL)
        parent.wait(5)
        wait_until(lambda: not alive(piper_pid), timeout=5.0, what="piper to exit with its parent")
    finally:
        if alive(piper_pid):
            os.kill(piper_pid, signal.SIGKILL)


# --- pre-rendered phrases ----------------------------------------------------------------------
def test_load_phrase_roundtrips_a_wav(tmp_path: Path) -> None:
    clip = AudioClip(np.arange(800, dtype=np.int16), 16000)
    write_wav(tmp_path / "okay.wav", clip)
    loaded = load_phrase("okay", tmp_path)
    assert loaded.sample_rate == 16000 and np.array_equal(loaded.samples, clip.samples)


def test_every_config_phrase_is_a_speakable_sentence() -> None:
    for name, text in config.TTS_PHRASES.items():
        assert name.isidentifier() and split_sentences(text) == [text], name
    for needed in ("okay", "cant_do_that", "already_sitting", "hmm", "one_moment", "let_me_think"):
        assert needed in config.TTS_PHRASES


def test_nice_and_cpu_list_wrap_the_command_and_keep_piper_the_pid(
    fake_piper: tuple[Path, Path],
) -> None:
    binary, model = fake_piper
    plain = PiperEngine(binary, model)
    assert plain._wrapper() == []  # the defaults change nothing
    wrapped = PiperEngine(binary, model, nice=5, cpu_list="1")
    try:
        assert wrapped._wrapper()[-3:] == [wrapped._wrapper()[-3], "-n", "5"]
        assert wrapped.synthesize("hello world").duration_s == pytest.approx(0.2)
        pid = wrapped.pid
        assert pid is not None and "piper" in Path(f"/proc/{pid}/cmdline").read_text()  # exec'd
    finally:
        wrapped.close()
