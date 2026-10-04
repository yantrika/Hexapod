#!/usr/bin/env python3
"""What the phone page costs: press-to-motion and release-to-command latency, idle CPU, peak temp.

    python scripts/cool_run.py -- nice -n 19 python scripts/measure_web.py

A real ``HexaApp`` (headless body process, fake STT and chat, no audio) with the web server on
127.0.0.1 and a ``WebClient`` in this process:
  latency   20 times: the client sends a combined walk and we wait for the body's FIRST joint-target
            change (``BodyProbe.change_time``: the joint targets are the inverse kinematics of the
            foot targets, so this is the first foot-target change), median and p95 (nearest rank).
            The robot is stopped and still before each press. The probe clock is
            ``time.monotonic()`` in both processes, so the two times compare directly.
  release   20 times (Step 12b): hold to talk, one audio block, then the client sends
            `ptt_release`;
            the time from that send to the `sit` command being sent to the bridge (the recognizer
            is a fake whose flush returns "sit down", so this is the pipeline's own cost: the
            push-to-talk tail ``PTT_TAIL_S``, the mic-poll granularity, the router and the send;
            real Vosk decoding time comes on top and is measured by measure_voice.py).
  idle CPU  the web-server thread's CPU (from /proc, user + system) over 10 s with a client
            connected and idle, and over 10 s with nobody connected; the whole brain process too.
  peak      the hottest sensor, sampled every second (the same sensors ``cool_run.py`` guards).
"""

from __future__ import annotations

import math
import os
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import config  # noqa: E402
from body.process import BodyProbe  # noqa: E402
from brain.app import AppOptions, HexaApp  # noqa: E402
from brain.chat import FakeChat  # noqa: E402
from scripts.cool_run import read_temperature_c  # noqa: E402
from scripts.web_check import WebClient  # noqa: E402
from tests.fakes import FakeStt, marker_block  # noqa: E402
from voice.audio import QueueSource  # noqa: E402

TRIALS = 20
IDLE_S = 10.0
PIN = "424242"


def thread_cpu_s(native_id: int) -> float:
    """User + system CPU seconds of one thread of this process."""
    with open(f"/proc/self/task/{native_id}/stat") as handle:
        fields = handle.read().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def measure_latency(client: WebClient, probe: BodyProbe) -> list[float]:
    samples = []
    for _ in range(TRIALS):
        client.send({"action": "stop"})
        time.sleep(0.6)  # still and holding
        start = time.monotonic()
        client.walk(forward=1.0, yaw=1.0)  # the combined press
        while probe.get("change_time") <= start:
            if time.monotonic() - start > 2.0:
                raise RuntimeError("the body did not move within 2 s")
            time.sleep(0.0005)
        samples.append((probe.get("change_time") - start) * 1000.0)
    client.send({"action": "stop"})
    return samples


def measure_release(app: HexaApp, client: WebClient, source: QueueSource) -> list[float]:
    """Release (client send) to the `sit` command sent to the bridge, in ms."""
    assert app.brain is not None
    stamps: list[float] = []
    app.brain.add_sent_listener(
        lambda command: stamps.append(time.monotonic()) if command.action == "sit" else None)
    samples = []
    for _ in range(TRIALS):
        before = len(stamps)
        client.press()
        time.sleep(0.2)
        source.push(marker_block(1))  # some audio while listening
        time.sleep(0.3)
        start = time.monotonic()
        client.release()
        while len(stamps) == before:
            if time.monotonic() - start > 3.0:
                raise RuntimeError("no command within 3 s of the release")
            time.sleep(0.0005)
        samples.append((stamps[before] - start) * 1000.0)
        time.sleep(1.0)
    return samples


def idle_cpu(app: HexaApp, seconds: float) -> tuple[float, float]:
    """(server thread CPU %, whole brain process CPU %) over *seconds*."""
    assert app.web is not None and app.web.native_id is not None
    tid = app.web.native_id
    thread0, process0, wall0 = thread_cpu_s(tid), time.process_time(), time.monotonic()
    time.sleep(seconds)
    wall = time.monotonic() - wall0
    return ((thread_cpu_s(tid) - thread0) / wall * 100.0,
            (time.process_time() - process0) / wall * 100.0)


def main() -> int:
    peak = [read_temperature_c() or 0.0]
    done = threading.Event()

    def sample() -> None:
        while not done.wait(1.0):
            peak[0] = max(peak[0], read_temperature_c() or 0.0)

    threading.Thread(target=sample, daemon=True).start()
    probe = BodyProbe()
    source = QueueSource()
    app = HexaApp(AppOptions(listen="ptt", chat="fake", no_speak=True, web=True,
                             web_port=0, web_pin=PIN),
                  probe=probe, stt=FakeStt({}, flush_events=[("final", "sit down")]),
                  source=source, chat_backend=FakeChat(("ok",)))
    app.start()
    try:
        assert app.web is not None
        url = f"ws://127.0.0.1:{app.web.port}"
        time.sleep(2.0)
        alone_thread, alone_process = idle_cpu(app, IDLE_S)
        client = WebClient(url, PIN)
        client.recv(2)

        def drain() -> None:  # a page reads its statuses; an unread queue stalls the pings
            while not done.is_set():
                client.recv(0.2)

        threading.Thread(target=drain, daemon=True).start()
        connected_thread, connected_process = idle_cpu(app, IDLE_S)
        samples = measure_latency(client, probe)
        release_samples = measure_release(app, client, source)
        client.close()
    finally:
        app.shutdown()
        done.set()
    print(f"press to first joint-target change over localhost, {TRIALS} presses:")
    print(f"  median {statistics.median(samples):.1f} ms   p95 {percentile(samples, 0.95):.1f} ms"
          f"   min {min(samples):.1f}   max {max(samples):.1f}")
    print("  all (ms): " + " ".join(f"{value:.0f}" for value in samples))
    print(f"release (ptt_release sent) to the command sent, {TRIALS} releases "
          f"(PTT_TAIL_S = {config.PTT_TAIL_S} s of it is the tail):")
    print(f"  median {statistics.median(release_samples):.0f} ms   "
          f"p95 {percentile(release_samples, 0.95):.0f} ms   min {min(release_samples):.0f}   "
          f"max {max(release_samples):.0f}")
    print(f"server idle CPU, nobody connected   : web thread {alone_thread:.2f} %   "
          f"brain process {alone_process:.1f} %")
    print(f"server idle CPU, one idle client    : web thread {connected_thread:.2f} %   "
          f"brain process {connected_process:.1f} %")
    print(f"peak temperature: {peak[0]:.0f} C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
