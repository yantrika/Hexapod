#!/usr/bin/env python3
"""The whole voice loop: microphone -> Vosk -> router -> body, status-driven speech and chat.

    python scripts/voice_cli.py                  headless body, small US model, Ollama chat
    python scripts/voice_cli.py --chat fake      scripted chat: use THIS on the slow dev laptop
    python scripts/voice_cli.py --chat off       no chat (anything that is not a command is ignored)
    python scripts/voice_cli.py --no-speak       no Piper: replies are printed as "[hexa] ..."
    python scripts/voice_cli.py --gui            with the PyBullet window (needs the Mesa override)
    python scripts/voice_cli.py --listen always  listen all the time (default: config.LISTEN_MODE)

Push-to-talk (the default): press Enter to start listening, speak, press Enter again to stop (a
terminal has no key-release events; true hold-to-talk comes with the phone page, Step 12). Pressing
Enter while hexa talks interrupts it. The terminal shows LISTENING / IDLE. SAFETY: in ptt mode a
voice "stop" only works while LISTENING; type `stop` + Enter, or use the control window STOP
button / Space, for a stop that always works.

Prints each partial and final text, the route result, every body status, the chat reply and,
per chat utterance, the time from the final result to the first token, the first sentence and
the first audio. Say "walk forward", "sit down", "stand up", "turn left", "wave", "stop". After a
command hexa answers from the body's STATUS ("okay", "I'm already sitting"). Anything else is
chat, but only while the body is idle: while walking hexa says "tell me after I stop" and the
LLM is not called. A stop word in a partial result stops the robot at once. While hexa speaks (and
a moment after) the microphone is ignored. Ctrl-C quits. Use a headless body on the dev laptop.
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
from brain.chat import ChatBackend, ChatResponder, ChatTiming, FakeChat, OllamaChat  # noqa: E402
from brain.dialogue import Dialogue  # noqa: E402
from brain.status_hub import StatusHub  # noqa: E402
from brain.voice_loop import VoiceEvent, VoiceLoop  # noqa: E402
from bridge import make_bridge, new_command  # noqa: E402
from commandline import format_status  # noqa: E402
from scripts.brain_cli import describe  # noqa: E402
from voice.audio import MicSource  # noqa: E402
from voice.playback import Playback, SoundDeviceSink  # noqa: E402
from voice.ptt import PushToTalk, make_ptt  # noqa: E402
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


class ConsolePlayback:
    """Stands in for ``Playback`` with ``--no-speak``: speech is printed, nothing is heard."""

    pending = 0

    def say(self, text: str) -> int:
        print(f"[hexa] {text}", flush=True)
        return 0

    def say_phrase(self, name: str) -> int:
        print(f"[hexa] {config.TTS_PHRASES[name]}", flush=True)
        return 0

    def clear(self, skip_tail: bool = False) -> None:
        pass


class PrintingPlayback:
    """Wraps the real ``Playback`` and also prints what is said (the chat text, the phrases)."""

    def __init__(self, playback: Playback) -> None:
        self._playback = playback

    @property
    def pending(self) -> int:
        return self._playback.pending

    def say(self, text: str) -> int:
        print(f"[hexa] {text}", flush=True)
        return self._playback.say(text)

    def say_phrase(self, name: str) -> int:
        print(f"[hexa] {config.TTS_PHRASES.get(name, name)}", flush=True)
        return self._playback.say_phrase(name)

    def clear(self, skip_tail: bool = False) -> None:
        self._playback.clear(skip_tail)


def print_timing(timing: ChatTiming) -> None:
    def show(name: str) -> str:
        value = timing.seconds(name)
        return "-" if value is None else f"{value:.2f} s"

    flag = " (failed)" if timing.failed else ""
    print(f"chat timing from the final result: first token {show('first_token')}, first sentence "
          f"{show('first_sentence')}, first audio {show('first_audio')}{flag}", flush=True)


def read_keys(ptt: PushToTalk, bridge: object, done: threading.Event) -> None:
    """Terminal keys for push-to-talk: Enter toggles listening; `stop` sends stop at once."""
    for line in sys.stdin:
        if done.is_set():
            return
        if line.strip().lower() in ("stop", "s"):
            bridge.send(new_command("stop"))  # type: ignore[attr-defined]
            print("stop sent", flush=True)
        else:
            ptt.toggle()


def print_indicator(state: str) -> None:
    print("\n>>> LISTENING (press Enter when done)" if state == "listening"
          else ">>> IDLE (press Enter to talk)", flush=True)


def make_backend(kind: str, model: str | None) -> ChatBackend | None:
    if kind == "off":
        return None
    if kind == "fake":
        return FakeChat(first_token_s=0.3, tokens_per_s=8.0)
    backend = OllamaChat(model=model or config.OLLAMA_MODEL)
    try:
        import requests

        requests.get(f"{backend.url}/api/version", timeout=3).raise_for_status()
    except Exception as error:  # noqa: BLE001 - only a warning: chat then says "can't think"
        print(f"warning: no Ollama at {backend.url} ({error}); chat will say it cannot think",
              file=sys.stderr)
        return backend
    # load the model now, in the background, so the first answer does not pay a cold load
    threading.Thread(target=backend.warm_up, name="chat-warmup", daemon=True).start()
    return backend


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
    parser.add_argument("--no-speak", action="store_true",
                        help="no Piper: speech is printed as [hexa] ...")
    parser.add_argument("--device", type=int, default=config.MIC_DEVICE, help="input device index")
    parser.add_argument("--chat", choices=("ollama", "fake", "off"), default="ollama",
                        help="chat backend (fake = scripted, for the slow dev laptop)")
    parser.add_argument("--listen", choices=("ptt", "always"), default=config.LISTEN_MODE,
                        help="ptt = push-to-talk (Enter toggles), always = listen all the time")
    parser.add_argument("--ollama-model", default=None, help="override config.OLLAMA_MODEL")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")

    try:
        stt = VoskStt(args.model)
    except SttError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    real_playback: Playback | None = None
    speaking = threading.Event()
    if not args.no_speak:
        engine = PiperEngine()
        try:
            engine.check_installed()
            engine.start()  # pay the model load now, not at the first answer
        except TtsError as error:
            print(f"error: {error} (or use --no-speak)", file=sys.stderr)
            return 1
        real_playback = Playback(engine, SoundDeviceSink(), speaking=speaking)
        real_playback.start()
    voice_out = PrintingPlayback(real_playback) if real_playback else ConsolePlayback()

    bridge = make_bridge()
    body = BodyProcess(bridge, headless=not args.gui)
    body.start()
    hub = StatusHub(bridge)
    hub.start()
    brain = BrainLoop(bridge, hub=hub)
    dialogue = Dialogue(hub.subscribe("dialogue"), voice_out)
    brain.add_sent_listener(dialogue.note_sent)
    dialogue.start()
    backend = make_backend(args.chat, args.ollama_model)
    chat = (ChatResponder(backend, voice_out, on_timing=print_timing)
            if backend is not None else None)
    if real_playback is not None and chat is not None:
        real_playback.on_start = lambda utterance: chat.note_audio_start(utterance.group)
    ptt = make_ptt(args.listen)
    voice = VoiceLoop(MicSource(device=args.device), stt, brain, speaking, voice_out,  # type: ignore[arg-type]
                      print_event, chat=chat, ptt=ptt)
    done = threading.Event()
    threading.Thread(target=print_statuses, args=(hub, done), daemon=True).start()
    try:
        if not body.wait_ready():
            print("the body process did not start", file=sys.stderr)
            return 1
        voice.start()
        commands = "'walk forward', 'sit down', 'stand up', 'turn left', 'wave', 'stop'"
        if ptt is not None:
            ptt.add_change_listener(print_indicator)
            threading.Thread(target=read_keys, args=(ptt, bridge, done), daemon=True).start()
            print(f"PUSH-TO-TALK (model {stt.model_path.name}, chat {args.chat}). Press Enter, say "
                  f"{commands} or just talk, press Enter again. Enter while hexa talks "
                  "interrupts it.\nSAFETY: a voice 'stop' works ONLY while LISTENING. Type "
                  "`stop` + Enter, or use the control window STOP button / Space, for a stop "
                  "that always works. Ctrl-C quits.\n>>> IDLE (press Enter to talk)", flush=True)
        else:
            print(f"listening ALWAYS (model {stt.model_path.name}, chat {args.chat}). Say "
                  f"{commands}, or just talk. Deaf while hexa speaks. Ctrl-C quits.", flush=True)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        done.set()
        bridge.send(new_command("stop"))
        voice.shutdown()
        if chat is not None:
            chat.shutdown()
        dialogue.shutdown()
        if real_playback is not None:
            real_playback.shutdown()
        brain.close()
        hub.stop()
        body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
