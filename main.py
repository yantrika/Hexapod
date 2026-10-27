#!/usr/bin/env python3
"""hexa: the single entrypoint. Starts the body process and the voice/brain loop.

    python main.py                          headless body, push-to-talk, Ollama chat, speech on
    python main.py --gui --chat fake        PyBullet window, scripted chat (the slow dev laptop)
    python main.py --backend dryrun         no PyBullet, no hardware: logs the joint targets
    python main.py --backend servo          REAL servos (Pi): asks you to type yes first
    python main.py --listen always          listen all the time instead of push-to-talk
    python main.py --no-speak --no-mic      no audio devices at all (tests, systemd without sound)
    python main.py --web                    phone control page on 127.0.0.1 (PIN printed at start)
    python main.py --lan                    the same, reachable from a phone on the Wi-Fi
    python main.py --lan --https --no-mic   the same over HTTPS, the phone is the microphone
    python main.py --lan --https --no-mic --phone-voice   Arya's voice plays on the phone too
    python main.py --vision                 the camera: names, greets, turns toward people
    python main.py --audio-file x.wav      recognise a recording in real time, then exit 0

SIGINT, SIGTERM and SIGHUP stop the robot, silence it, cancel chat, join the threads and end the
body process (real servos: sit down first, then let go), then exit 0. A startup problem exits 1
with one clear line per problem on stderr.
No TTY is needed (systemd): push-to-talk is then driven through ``HexaApp.set_listening`` (the
phone page). On a terminal, Enter toggles listening and `stop` + Enter sends stop.
In push-to-talk mode a voice "stop" only works while listening; the control window STOP button
and Space (``scripts/control_window.py``) are the always-available stop.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

EXIT_OK, EXIT_STARTUP, EXIT_RUNTIME = 0, 1, 2
logger = logging.getLogger("hexa")


def build_parser() -> argparse.ArgumentParser:
    import config

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--gui", action="store_true", help="PyBullet window (needs a display)")
    view.add_argument("--headless", action="store_true", help="no window (the default)")
    parser.add_argument("--backend", choices=config.BACKENDS, default=None,
                        help="sim = PyBullet (needs pybullet), dryrun = no physics, no hardware: "
                             "logs the joint targets (the Pi before the servos), servo = the REAL "
                             "servos: only by this flag (HEXA_BACKEND=servo is refused) and after "
                             "typing yes")
    parser.add_argument("--listen", choices=("ptt", "always"), default=config.LISTEN_MODE,
                        help="ptt = push-to-talk, always = listen all the time")
    parser.add_argument("--chat", choices=("fake", "ollama", "off"), default="ollama",
                        help="chat backend (fake = scripted)")
    parser.add_argument("--no-speak", action="store_true", help="no Piper, nothing is said aloud")
    parser.add_argument("--no-mic", action="store_true",
                        help="no microphone (typing and the phone page still work)")
    parser.add_argument("--web", action="store_true",
                        help="phone control page on 127.0.0.1 (PIN printed at start)")
    parser.add_argument("--lan", action="store_true",
                        help="serve the page on every interface and print the phone URL(s) "
                             "(implies --web; plain HTTP: trusted networks only)")
    parser.add_argument("--https", action="store_true",
                        help="serve the page over TLS with the self-signed certificate from "
                             "scripts/make_cert.py (implies --web; the phone microphone needs it)")
    parser.add_argument("--tls-cert", type=Path, default=None,
                        help="certificate (default config.WEB_TLS_CERT)")
    parser.add_argument("--tls-key", type=Path, default=None,
                        help="private key (default config.WEB_TLS_KEY)")
    parser.add_argument("--phone-voice", action="store_true",
                        help="Arya's voice plays on the phone page, not on a speaker (Step 12c-2; "
                             "needs --web/--lan/--https)")
    parser.add_argument("--web-port", type=int, default=config.WEB_PORT,
                        help="port of the page (default %(default)s)")
    default_pin = os.environ.get("HEXA_WEB_PIN")
    if default_pin is None and "PYTEST_CURRENT_TEST" not in os.environ:
        default_pin = "2560"
    parser.add_argument("--web-pin", "--pin", default=default_pin,
                        help="fixed web password/PIN (default: 2560 or HEXA_WEB_PIN env var)")
    parser.add_argument("--mode", choices=config.PERSONA_MODES, default=None,
                        help="start in this persona mode (default: the saved one, else calm)")
    parser.add_argument("--no-memory", action="store_true",
                        help="remember nothing: no database is opened (the persona still works)")
    parser.add_argument("--memory-db", type=Path, default=None,
                        help="memory database (default config.MEMORY_DB_PATH, data/memory/arya.db)")
    parser.add_argument("--vision", action="store_true",
                        help="the camera process: recognise people who agreed to it, greet them, "
                             "ask strangers their name, turn 10-20 degrees toward faces (needs "
                             "requirements-vision.txt and scripts/fetch_vision_models.py)")
    parser.add_argument("--vision-preview", action="store_true",
                        help="with --vision: a window with the camera picture, face boxes and "
                             "track ids (q or Esc closes it); nothing is saved")
    parser.add_argument("--camera", default=str(config.VISION_CAMERA_INDEX),
                        help="camera index, or a video file played as a camera (default "
                             "%(default)s)")
    parser.add_argument("--no-track", action="store_true",
                        help="with --vision: do not turn toward people (\"track me\" turns it on)")
    parser.add_argument("--audio-file", type=Path, default=None,
                        help="recognise this WAV, then exit")
    parser.add_argument("--model", default=None, help="Vosk model: us, in, a name or a path")
    parser.add_argument("--ollama-model", default=None, help="override config.OLLAMA_MODEL")
    parser.add_argument("--device", type=int, default=config.MIC_DEVICE, help="input device index")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="log file (default config.LOG_FILE, rotated by size)")
    parser.add_argument("--log-level", default=config.LOG_LEVEL,
                        help="DEBUG, INFO, WARNING or ERROR (default %(default)s)")
    return parser


def read_keys(app: object, done: threading.Event) -> None:
    """Terminal keys (only when stdin is a terminal): Enter toggles listening, `stop` stops."""
    for line in sys.stdin:
        if done.is_set():
            return
        if line.strip().lower() in ("stop", "s"):
            app.send_stop()  # type: ignore[attr-defined]
            logger.info("stop sent from the keyboard")
        else:
            listening = app.toggle_listening()  # type: ignore[attr-defined]
            logger.info(">>> %s", "LISTENING" if listening else "IDLE")


def resolve_backend(args: argparse.Namespace) -> str | None:
    """The backend to run, or None (after printing why) when the environment asks for servos."""
    import config

    if args.backend is not None:
        return str(args.backend)
    if config.ENV_REQUESTS_SERVO:
        print("error: HEXA_BACKEND=servo is refused: real servos need --backend servo on the "
              "command line", file=sys.stderr)
        return None
    return config.BACKEND_DEFAULT


def servo_problems() -> list[str]:
    """What stops the real servos from starting: the calibration file and its contents."""
    from body import servo_calibration

    try:
        servo_calibration.load().require_calibrated()
    except servo_calibration.CalibrationError as error:
        return [str(error)]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.web_pin and args.web_pin.lower() in ("random", "none"):
        args.web_pin = None
    backend = resolve_backend(args)
    if backend is None:
        return EXIT_STARTUP
    args.backend = backend

    from brain.logsetup import setup_logging

    try:
        log_path = setup_logging(args.log_level, args.log_file)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_STARTUP

    from brain.app import AppOptions, HexaApp, StartupError
    from brain.startup import CheckOptions, run_checks

    problems = run_checks(CheckOptions(
        model=args.model, no_speak=args.no_speak, no_mic=args.no_mic or args.audio_file is not None,
        chat=args.chat, ollama_model=args.ollama_model, mic_device=args.device,
        phone_voice=args.phone_voice, vision=args.vision))
    if args.backend == "dryrun" and args.gui:
        problems.append("--gui needs the simulator: drop --gui or use --backend sim")
    if args.backend == "sim":
        from importlib.util import find_spec

        if find_spec("pybullet") is None:
            problems.append("pybullet is not installed: use --backend dryrun, or "
                            "pip install -r requirements-sim.txt")
    if args.backend == "servo":
        if args.gui:
            problems.append("--gui needs the simulator: drop --gui or use --backend sim")
        problems += servo_problems()
    if args.audio_file is not None and not args.audio_file.is_file():
        problems.append(f"audio file not found: {args.audio_file}")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return EXIT_STARTUP

    if args.backend == "servo":
        from body.servo_safety import confirm_hardware

        if not confirm_hardware("  - this starts the body with all 18 servos attached\n"):
            print("aborted: nothing was moved", file=sys.stderr)
            return EXIT_STARTUP

    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        logger.info("signal %s: shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGHUP"):  # closed terminal / dropped SSH (not on Windows)
        signal.signal(signal.SIGHUP, on_signal)

    app = HexaApp(AppOptions(
        gui=args.gui, backend=args.backend, listen=args.listen, chat=args.chat,
        no_speak=args.no_speak, no_mic=args.no_mic, audio_file=args.audio_file, model=args.model,
        ollama_model=args.ollama_model, mic_device=args.device,
        web=args.web or args.lan or args.https, lan=args.lan,
        https=args.https, tls_cert=args.tls_cert, tls_key=args.tls_key,
        phone_voice=args.phone_voice,
        web_port=args.web_port, web_pin=args.web_pin, mode=args.mode,
        memory=not args.no_memory, memory_path=args.memory_db,
        vision=args.vision, camera=int(args.camera) if args.camera.isdigit() else args.camera,
        tracking=not args.no_track, vision_preview=args.vision_preview))
    code = EXIT_OK
    try:
        app.start()
        logger.info("log file: %s", log_path)
        if sys.stdin is not None and sys.stdin.isatty() and app.ptt is not None:
            threading.Thread(target=read_keys, args=(app, stop), name="keys", daemon=True).start()
            logger.info("push-to-talk: Enter toggles listening, `stop` + Enter stops the robot "
                        "(a voice stop works only while listening)")
        app.wait(stop)
    except StartupError as error:
        print(f"error: {error}", file=sys.stderr)
        code = EXIT_STARTUP
    except Exception as error:  # noqa: BLE001 - the last line of defence: report, then clean up
        logger.exception("hexa failed")
        print(f"error: {error}", file=sys.stderr)
        code = EXIT_RUNTIME
    finally:
        app.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
