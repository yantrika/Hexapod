#!/usr/bin/env python3
"""Measure what the voice costs: Piper speed, body tick time with and without Piper, clear().

    nice -n 19 python scripts/measure_voice.py --body            headless body sim
    nice -n 19 python scripts/measure_voice.py --body --gui      with the PyBullet window
    nice -n 19 python scripts/measure_voice.py --clear           clear() latency (speaker, quiet)

``--body`` runs four windows (stand, stand + Piper, walk, walk + Piper) and prints the body's mean
and worst control-tick work time, the tick rate, and while Piper runs: its real-time factor, time
to first audio, CPU share, peak RAM and thread count. Piper is kept 100 % busy (sentences back
to back), the worst case; real speech is mostly idle. One process at a time; no sound is played.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import config  # noqa: E402
from body.process import BodyProbe, BodyProcess  # noqa: E402
from brain.brain_loop import BrainLoop  # noqa: E402
from brain.status_hub import StatusHub  # noqa: E402
from brain.voice_loop import VoiceEvent, VoiceLoop  # noqa: E402
from bridge import make_bridge, new_command  # noqa: E402
from voice.audio import FileSource  # noqa: E402
from voice.playback import Playback, SoundDeviceSink  # noqa: E402
from voice.stt import VoskStt  # noqa: E402
from voice.tts import AudioClip, PiperEngine, TtsError  # noqa: E402

SENTENCES = [
    "Okay, I am standing up.",
    "I can't do that right now.",
    "Hello, I am hexa, a six legged robot.",
    "One moment, let me think about that.",
]
WINDOW_S = 8.0


def proc_cpu_seconds(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def proc_status(pid: int, key: str) -> str:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(key):
            return line.split(":", 1)[1].strip()
    return "?"


class PiperLoad(threading.Thread):
    """Synthesizes sentences back to back and records speed."""

    def __init__(self, engine: PiperEngine) -> None:
        super().__init__(daemon=True)
        self.engine = engine
        self.stop_flag = threading.Event()
        self.took: list[float] = []
        self.audio: list[float] = []

    def run(self) -> None:
        index = 0
        while not self.stop_flag.is_set():
            started = time.perf_counter()
            clip = self.engine.synthesize(SENTENCES[index % len(SENTENCES)])
            self.took.append(time.perf_counter() - started)
            self.audio.append(clip.duration_s)
            index += 1


def piper_summary(took: list[float], audio: list[float]) -> str:
    if not took:
        return "no sentence finished"
    rtf = sum(took) / sum(audio)
    return (f"{len(took)} sentences, time to first audio median {statistics.median(took):.2f} s "
            f"(worst {max(took):.2f} s), real-time factor {rtf:.2f}")


def run_window(
    body: BodyProcess, probe: BodyProbe, engine: PiperEngine | None, walking: bool
) -> None:
    load = PiperLoad(engine) if engine is not None else None
    bridge = body.bridge
    probe.reset_window()
    cpu_before = proc_cpu_seconds(engine.pid) if engine and engine.pid else 0.0
    started = time.monotonic()
    if load:
        load.start()
    next_heartbeat = 0.0
    while time.monotonic() - started < WINDOW_S:
        if walking and time.monotonic() >= next_heartbeat:
            bridge.send(new_command("heartbeat", {}))
            next_heartbeat = time.monotonic() + 1.0 / config.HEARTBEAT_HZ
        bridge.receive_all()
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    ticks, mean, worst = probe.window()
    line = (f"body tick work: mean {mean * 1000:.2f} ms, worst {worst * 1000:.1f} ms, "
            f"{ticks / elapsed:.1f} ticks/s (nominal {config.CONTROL_HZ:.0f})")
    print(f"  {line}")
    if load and engine and engine.pid:
        load.stop_flag.set()
        load.join(30)
        cpu = (proc_cpu_seconds(engine.pid) - cpu_before) / elapsed * 100
        print(f"  piper: {piper_summary(load.took, load.audio)}")
        print(f"  piper CPU {cpu:.0f} % of one core; RAM peak {proc_status(engine.pid, 'VmHWM')}; "
              f"threads {proc_status(engine.pid, 'Threads')}")


def measure_body(gui: bool, nice: int, cpu_list: str | None, body_cpus: set[int] | None) -> int:
    engine = PiperEngine(nice=nice, cpu_list=cpu_list)
    try:
        engine.check_installed()
    except TtsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    bridge = make_bridge()
    probe = BodyProbe()
    body = BodyProcess(bridge, headless=not gui, probe=probe)
    body.start()
    try:
        if not body.wait_ready():
            print("error: the body did not become ready", file=sys.stderr)
            return 1
        if body_cpus and body.pid:
            os.sched_setaffinity(body.pid, body_cpus)
        print(f"body CPUs: {sorted(body_cpus) if body_cpus else 'any'}")
        print(f"mode: {'GUI viewer' if gui else 'headless'}; this process nice {os.nice(0)}, "
              f"Piper extra nice {nice}, Piper CPUs {cpu_list or 'any'}")
        engine.start()
        engine.synthesize("Warm up.")
        bridge.send(new_command("stand", {}))
        time.sleep(3.0)
        print("A. body standing, no Piper")
        run_window(body, probe, None, walking=False)
        print("B. body standing, Piper busy")
        run_window(body, probe, engine, walking=False)
        bridge.send(new_command("walk", {"direction": "fwd", "speed": 0.5}))
        time.sleep(1.0)
        print("C. body walking, no Piper")
        run_window(body, probe, None, walking=True)
        print("D. body walking, Piper busy")
        run_window(body, probe, engine, walking=True)
        bridge.send(new_command("stop", {}))
    finally:
        engine.close()
        body.shutdown()
    return 0


class QuietSink(SoundDeviceSink):
    """The real speaker at a low volume, for latency runs."""

    def start(self, clip: AudioClip) -> None:
        quiet = (clip.samples.astype("float32") * 0.1).astype(clip.samples.dtype)
        super().start(AudioClip(quiet, clip.sample_rate))


def measure_clear(trials: int) -> int:
    engine = PiperEngine()
    sink = QuietSink()
    started = threading.Event()
    playback = Playback(engine, sink, on_start=lambda _utterance: started.set())
    playback.start()
    latencies: list[float] = []
    try:
        for _ in range(trials):
            started.clear()
            playback.say_phrase("standing_up")
            if not started.wait(5.0):
                print("error: playback did not start (no audio device?)", file=sys.stderr)
                return 1
            time.sleep(0.3)
            began = time.perf_counter()
            playback.clear()
            latencies.append(time.perf_counter() - began)
            playback.wait_idle(2.0)
            time.sleep(0.1)
        stream = getattr(sink, "_stream", None)
        print(f"clear() over {trials} trials: median {statistics.median(latencies) * 1000:.1f} ms, "
              f"worst {max(latencies) * 1000:.1f} ms "
              f"(bound {config.TTS_CLEAR_MAX_S * 1000:.0f} ms)")
        if stream is not None:
            print(f"output stream latency (audio already in the device): "
                  f"{stream.latency * 1000:.0f} ms")
    finally:
        playback.shutdown()
    return 0


STT_PHRASES = ["walk forward", "sit down", "I sat down for lunch", "stop", "stand up"]


def render_speech(engine: PiperEngine) -> dict[str, np.ndarray]:
    return {text: engine.synthesize(text).samples for text in STT_PHRASES}


def speech_mix(clips: dict[str, np.ndarray], seconds: float) -> np.ndarray:
    """The phrases one after another with 0.6 s gaps, repeated to last *seconds*."""
    gap = np.zeros(int(0.6 * config.AUDIO_SAMPLE_RATE), dtype=np.int16)
    parts: list[np.ndarray] = []
    total = 0
    while total < seconds * config.AUDIO_SAMPLE_RATE:
        for clip in clips.values():
            parts += [clip, gap]
            total += len(clip) + len(gap)
    return np.concatenate(parts)[: int(seconds * config.AUDIO_SAMPLE_RATE)]


def vosk_cpu(stt: VoskStt, samples: np.ndarray, label: str) -> None:
    """Vosk alone (no body): CPU share fed in real time, and the decode real-time factor."""
    source = FileSource(samples, realtime=True, pad_silence_s=0.0)
    source.start()
    cpu0, wall0, audio = time.process_time(), time.perf_counter(), 0.0
    while True:
        try:
            block = source.read(0.5)
        except Exception:  # EndOfAudio
            break
        if block is not None:
            stt.feed(block)
            audio += len(block) / config.AUDIO_SAMPLE_RATE
    cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
    print(f"  {label}: {audio:.1f} s of audio in real time, Vosk CPU {cpu / wall * 100:.0f} % "
          f"of one core")
    stt.reset()
    fast = FileSource(samples, realtime=False, pad_silence_s=0.0)
    fast.start()
    started, count = time.process_time(), 0.0
    while True:
        try:
            block = fast.read(0.5)
        except Exception:
            break
        if block is not None:
            stt.feed(block)
            count += len(block) / config.AUDIO_SAMPLE_RATE
    took = time.process_time() - started
    print(f"  {label}: decode as fast as possible: {took:.2f} s CPU for {count:.1f} s of audio, "
          f"real-time factor {took / count:.2f}")
    stt.reset()


def proc_rss_mb() -> float:
    return int(proc_status(os.getpid(), "VmRSS").split()[0]) / 1024


def measure_ram(grammar: bool) -> int:
    """Resident memory after loading the model and the recognizers (run once per setting)."""
    base = proc_rss_mb()
    stt = VoskStt(grammar=grammar)
    print(f"  grammar {'on ' if grammar else 'off'}: RSS {proc_rss_mb():.0f} MB "
          f"(+{proc_rss_mb() - base:.0f} MB over an idle interpreter), "
          f"{'2' if grammar else '1'} recognizer(s) on one shared model")
    del stt
    return 0


def measure_stt(gui: bool, latency: bool, with_piper: bool) -> int:
    """One recognizer vs two: CPU alone, then the body's tick time. One model at a time."""
    import gc
    import subprocess

    engine = PiperEngine()
    try:
        engine.check_installed()
    except TtsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    engine.start()
    clips = render_speech(engine)
    if not with_piper:
        engine.close()  # Piper stays out of the way unless asked: the laptop is fragile
    print(f"this process nice {os.nice(0)}")
    print("RAM, one fresh process per setting")
    for flag in ("off", "on"):
        subprocess.run([sys.executable, __file__, "--ram", "--grammar", flag], check=False)

    silence10 = np.zeros(10 * config.AUDIO_SAMPLE_RATE, dtype=np.int16)
    mix10 = speech_mix(clips, 10.0)
    for grammar in (False, True):
        stt = VoskStt(grammar=grammar)
        print(f"a/d. Vosk alone, {'2 recognizers (free + grammar)' if grammar else '1 recognizer'}")
        vosk_cpu(stt, silence10, "silence")
        vosk_cpu(stt, mix10, "speech ")
        del stt
        gc.collect()
        time.sleep(8.0)  # let the CPU cool between runs

    bridge = make_bridge()
    probe = BodyProbe()
    body = BodyProcess(bridge, headless=not gui, probe=probe)
    body.start()
    hub = StatusHub(bridge)
    hub.start()
    brain = BrainLoop(bridge, hub=hub)
    try:
        if not body.wait_ready():
            print("error: the body did not become ready", file=sys.stderr)
            return 1
        print(f"mode: {'GUI viewer' if gui else 'headless'}")
        bridge.send(new_command("stand", {}))
        time.sleep(3.0)
        bridge.send(new_command("walk", {"direction": "fwd", "speed": 0.5}))
        time.sleep(1.0)
        walking = Window(body, probe, brain)
        silence = np.zeros(int((WINDOW_S + 4) * config.AUDIO_SAMPLE_RATE), dtype=np.int16)
        print("b1. body walking, no voice")
        walking.run(None, None, engine=None)
        for grammar in (False, True):
            stt = VoskStt(grammar=grammar)
            print(f"b{2 if not grammar else 3}. body walking, Vosk on silence, "
                  f"{'2 recognizers' if grammar else '1 recognizer'}")
            walking.run(stt, silence, engine=None)
            if grammar and with_piper:
                print("b4. body walking, 2 recognizers on silence + Piper busy")
                walking.run(stt, silence, engine=engine)
            del stt
            gc.collect()
            time.sleep(8.0)  # cool down: the dev laptop shuts down at 87 C
        if latency:
            stt = VoskStt()
            bridge.send(new_command("stop", {}))
            time.sleep(1.5)
            print("c. end of speech -> command sent -> first foot-target change (\"walk forward\")")
            results = []
            for trial in range(3):
                results.append(latency_trial(stt, clips["walk forward"], bridge, probe, brain))
                bridge.send(new_command("stop", {}))
                time.sleep(2.0)
                print(f"  trial {trial + 1}: " + ", ".join(
                    f"{name} {value * 1000:.0f} ms" for name, value in results[-1].items()))
    finally:
        bridge.send(new_command("stop", {}))
        brain.close()
        hub.stop()
        engine.close()
        body.shutdown()
    return 0


class Window:
    """Body tick statistics over a window while a voice loop (and Piper) run."""

    def __init__(self, body: BodyProcess, probe: BodyProbe, brain: BrainLoop) -> None:
        self.body, self.probe, self.brain = body, probe, brain

    def run(
        self, stt: VoskStt | None, audio: np.ndarray | None, engine: PiperEngine | None
    ) -> None:
        bridge = self.body.bridge
        loop = None
        if stt is not None and audio is not None:
            stt.reset()
            loop = VoiceLoop(FileSource(audio, realtime=True, pad_silence_s=0.0), stt, self.brain,
                             threading.Event(), None)
            loop.start()
        load = PiperLoad(engine) if engine is not None else None
        cpu0 = time.process_time()
        piper0 = proc_cpu_seconds(engine.pid) if engine and engine.pid else 0.0
        self.probe.reset_window()
        started = time.monotonic()
        if load:
            load.start()
        next_beat = 0.0
        while time.monotonic() - started < WINDOW_S:
            if time.monotonic() >= next_beat:
                bridge.send(new_command("heartbeat", {}))
                next_beat = time.monotonic() + 1.0 / config.HEARTBEAT_HZ
            self.brain.pump()
            time.sleep(0.05)
        elapsed = time.monotonic() - started
        ticks, mean, worst = self.probe.window()
        cpu = time.process_time() - cpu0
        print(f"  body tick work: mean {mean * 1000:.2f} ms, worst {worst * 1000:.1f} ms, "
              f"{ticks / elapsed:.1f} ticks/s (nominal {config.CONTROL_HZ:.0f}); "
              f"this process CPU {cpu / elapsed * 100:.0f} % of one core")
        if load and engine and engine.pid:
            load.stop_flag.set()
            load.join(30)
            print(f"  piper: {piper_summary(load.took, load.audio)}; "
                  f"CPU {(proc_cpu_seconds(engine.pid) - piper0) / elapsed * 100:.0f} %")
        if loop is not None:
            loop.shutdown()


def latency_trial(
    stt: VoskStt, walk: np.ndarray, bridge: object, probe: BodyProbe, brain: BrainLoop
) -> dict[str, float]:
    stt.reset()
    source = FileSource(walk, realtime=True, lead_silence_s=0.5, pad_silence_s=3.0)
    routed: list[VoiceEvent] = []
    loop = VoiceLoop(source, stt, brain, threading.Event(), None,
                     lambda e: routed.append(e) if e.kind == "route" else None)
    before = probe.get("change_time")
    loop.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (not routed or probe.get("change_time") <= before):
        brain.pump()
        time.sleep(0.005)
    loop.shutdown()
    speech_end = source.speech_end_at or 0.0
    sent = routed[0].timestamp if routed else float("nan")
    moved = probe.get("change_time")
    return {"end of speech to command sent": sent - speech_end,
            "command sent to first foot-target change": moved - sent,
            "end of speech to first foot-target change": moved - speech_end}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", action="store_true", help="body tick time with and without Piper")
    parser.add_argument("--gui", action="store_true", help="with --body: open the PyBullet window")
    parser.add_argument("--clear", action="store_true", help="clear() latency on the real speaker")
    parser.add_argument("--stt", action="store_true", help="Vosk cost: 1 vs 2 recognizers")
    parser.add_argument("--latency", action="store_true", help="with --stt: end-of-speech latency")
    parser.add_argument("--with-piper", action="store_true", help="with --stt: Piper busy too")
    parser.add_argument("--ram", action="store_true", help="(internal) RSS after loading Vosk")
    parser.add_argument("--grammar", choices=("on", "off"), default="on", help="with --ram")
    parser.add_argument("--piper-nice", type=int, default=config.PIPER_NICE)
    parser.add_argument("--piper-cpus", default=config.PIPER_CPU_LIST, help="taskset list, e.g. 3")
    parser.add_argument("--body-cpus", help="pin the body process to these CPUs, e.g. 0,2")
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()
    if args.clear:
        return measure_clear(args.trials)
    if args.ram:
        return measure_ram(args.grammar == "on")
    if args.stt:
        return measure_stt(args.gui, args.latency, args.with_piper)
    if args.body:
        cpus = {int(n) for n in args.body_cpus.split(",")} if args.body_cpus else None
        return measure_body(args.gui, args.piper_nice, args.piper_cpus, cpus)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
