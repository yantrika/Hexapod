"""Step 10b: startup checks give one clear line per problem, and main exits 1 on them."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import config
import main
from brain import startup
from brain.startup import CheckOptions, run_checks


def test_a_missing_vosk_model_is_one_line_with_the_fix(tmp_path: Path) -> None:
    line = startup.check_vosk(str(tmp_path / "nope"))
    assert line and "Vosk model not found" in line and "fetch_models.sh" in line
    assert "\n" not in line


def test_missing_piper_binary_and_voice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "PIPER_BINARY", tmp_path / "piper")
    line = startup.check_piper()
    assert line and "Piper" in line and "fetch_models.sh" in line


def test_missing_phrases_are_counted(tmp_path: Path) -> None:
    line = startup.check_phrases(tmp_path)
    assert line and str(len(config.TTS_PHRASES)) in line and "prerender_phrases.py" in line
    for name in config.TTS_PHRASES:
        from voice.tts import phrase_path

        phrase_path(name, tmp_path).write_bytes(b"x")
    assert startup.check_phrases(tmp_path) is None


def test_no_microphone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("voice.audio.list_input_devices", lambda: [])
    assert "no microphone" in (startup.check_input_device(None) or "")
    monkeypatch.setattr("voice.audio.list_input_devices",
                        lambda: [(2, "mic", 1, 16000.0, True)])
    assert startup.check_input_device(None) is None
    assert "input device 9 not found" in (startup.check_input_device(9) or "")


def test_no_output_device(monkeypatch: pytest.MonkeyPatch) -> None:
    import sounddevice

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no device")

    monkeypatch.setattr(sounddevice, "query_devices", boom)
    assert "--no-speak" in (startup.check_output_device(None) or "")


class _OllamaStub(http.server.BaseHTTPRequestHandler):
    models: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"models": [{"name": name} for name in self.models]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def ollama_url() -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _OllamaStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_ollama_unreachable_and_model_missing(ollama_url: str) -> None:
    assert "not reachable" in (startup.check_ollama("m", "http://127.0.0.1:9", 0.5) or "")
    _OllamaStub.models = ["other:latest"]
    assert "pull qwen2.5:0.5b" in (startup.check_ollama("qwen2.5:0.5b", ollama_url) or "")
    _OllamaStub.models = ["qwen2.5:0.5b"]
    assert startup.check_ollama("qwen2.5:0.5b", ollama_url) is None


def test_run_checks_skips_what_is_switched_off(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    called: list[str] = []
    for name in ("check_piper", "check_phrases", "check_output_device", "check_input_device",
                 "check_ollama"):
        monkeypatch.setattr(startup, name, lambda *a, _n=name, **k: called.append(_n))
    monkeypatch.setattr(startup, "check_vosk", lambda model: None)
    assert run_checks(CheckOptions(no_speak=True, no_mic=True, chat="fake")) == []
    assert called == []
    run_checks(CheckOptions(chat="ollama"))
    assert set(called) == {"check_piper", "check_phrases", "check_output_device",
                           "check_input_device", "check_ollama"}


def test_run_checks_reports_every_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(startup, "check_vosk", lambda model: "problem one")
    monkeypatch.setattr(startup, "check_piper", lambda: "problem two")
    monkeypatch.setattr(startup, "check_phrases", lambda directory=None: None)
    monkeypatch.setattr(startup, "check_output_device", lambda device: None)
    assert run_checks(CheckOptions(no_mic=True, chat="fake")) == ["problem one", "problem two"]


def test_main_exits_1_with_one_line_per_problem(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main.main(["--model", str(tmp_path / "nope"), "--no-speak", "--no-mic", "--chat",
                      "fake", "--log-file", str(tmp_path / "x.log")])
    err = capsys.readouterr().err.strip().splitlines()
    errors = [line for line in err if line.startswith("error:")]
    assert code == 1 and len(errors) == 1 and "Vosk model not found" in errors[0]


def test_main_rejects_a_bad_log_level_and_a_missing_audio_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main.main(["--log-level", "chatty", "--log-file", str(tmp_path / "x.log")]) == 1
    assert "unknown log level" in capsys.readouterr().err
    code = main.main(["--no-speak", "--chat", "fake", "--audio-file", str(tmp_path / "a.wav"),
                      "--log-file", str(tmp_path / "x.log")])
    assert code == 1  # a missing file (or a missing model) both end in a clean exit 1


def test_the_defaults_and_flag_exclusion() -> None:
    args = main.build_parser().parse_args([])
    assert args.listen == config.LISTEN_MODE and not args.gui and args.chat == "ollama"
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["--gui", "--headless"])
