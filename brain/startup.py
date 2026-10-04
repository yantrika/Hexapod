"""Startup checks: everything the robot needs, each failure ONE clear line with the fix.

``run_checks`` returns the list of problems (empty = go). It never raises and starts nothing:
no model is loaded and no process is spawned, so a missing file is reported in a moment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckOptions:
    model: str | None = None
    no_speak: bool = False
    no_mic: bool = False  # no microphone needed (an audio file, or audio from the phone page)
    chat: str = "ollama"  # "ollama" | "fake" | "off"
    ollama_model: str | None = None
    mic_device: int | None = None
    speaker_device: int | None = None


def check_vosk(model: str | None) -> str | None:
    from voice.stt import resolve_model_path

    path = resolve_model_path(model)
    if not path.is_dir():
        return f"Vosk model not found: {path} (run scripts/fetch_models.sh)"
    return None


def check_piper() -> str | None:
    from voice.tts import PiperEngine, TtsError

    try:
        PiperEngine(binary=config.PIPER_BINARY, model=config.PIPER_MODEL_PATH).check_installed()
    except TtsError as error:
        return str(error)
    return None


def check_phrases(directory: Path | None = None) -> str | None:
    from voice.tts import phrase_path

    missing = [name for name in config.TTS_PHRASES if not phrase_path(name, directory).is_file()]
    if missing:
        return (f"{len(missing)} pre-rendered phrase(s) missing (e.g. {missing[0]}); "
                "run scripts/prerender_phrases.py")
    return None


def check_input_device(device: int | None) -> str | None:
    try:
        from voice.audio import list_input_devices

        devices = list_input_devices()
    except Exception as error:  # noqa: BLE001 - no PortAudio is a startup problem, not a crash
        return f"cannot list audio input devices ({error}); use --no-mic or fix the sound setup"
    if not devices:
        return "no microphone found (plug one in, or use --no-mic)"
    if device is not None and device not in {entry[0] for entry in devices}:
        return f"input device {device} not found; see scripts/mic_check.py (or use --no-mic)"
    return None


def check_output_device(device: int | None) -> str | None:
    try:
        import sounddevice

        info = sounddevice.query_devices(device, "output")
    except Exception as error:  # noqa: BLE001
        return f"no audio output device found ({error}); use --no-speak or fix the sound setup"
    if int(info["max_output_channels"]) <= 0:
        return "the audio output device has no output channels (use --no-speak)"
    return None


def check_ollama(model: str, url: str = config.OLLAMA_URL, timeout_s: float = 3.0) -> str | None:
    try:
        import requests

        reply = requests.get(f"{url}/api/tags", timeout=timeout_s)
        reply.raise_for_status()
        names = {str(item.get("name", "")) for item in reply.json().get("models", [])}
    except Exception as error:  # noqa: BLE001
        return (f"Ollama is not reachable at {url} ({type(error).__name__}); start it "
                "(docker start ollama) or use --chat fake")
    if model not in names and f"{model}:latest" not in names:
        return f"Ollama has no model {model}; run: docker exec ollama ollama pull {model}"
    return None


def run_checks(options: CheckOptions) -> list[str]:
    """Every problem found, one line each."""
    results = [check_vosk(options.model)]
    if not options.no_speak:
        results += [check_piper(), check_phrases(), check_output_device(options.speaker_device)]
    if not options.no_mic:
        results.append(check_input_device(options.mic_device))
    if options.chat == "ollama":
        results.append(check_ollama(options.ollama_model or config.OLLAMA_MODEL))
    return [problem for problem in results if problem]
