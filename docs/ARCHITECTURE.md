# How the pieces fit together

Short version. The full design is in `../plan.md` (message formats, safety rules, every decision).

## Two programs

```
 BRAIN program (one process, several threads)         BODY program (its own process)
 ┌──────────────────────────────────────────┐        ┌────────────────────────────┐
 │ microphone -> STT thread (Vosk)          │        │ control loop, 50 times/sec │
 │            -> router worker thread       │ command│  - read commands           │
 │               (router, motion keeper)    │ ──────▶│  - drop old ones           │
 │ speaker  <- playback thread (Piper)      │ queue  │  - stop always wins        │
 │ status hub thread <─────────────────────────────── │  - walk / turn / sit ...   │
 │   (shares statuses with all listeners)   │ status │  - gait + leg maths        │
 └──────────────────────────────────────────┘ queue  │  - backend: simulation OR  │
                                                      │    real servos             │
                                                      └────────────────────────────┘
```

- The **brain** hears, understands and speaks. It never touches a joint angle.
- The **body** moves the legs. It never touches audio or text.
- The only link is `bridge.py`: a small command queue (brain to body), a status queue (body to brain), and a shared `stop_event` that makes "stop" fast.
- The body runs the simulation today; real servos replace one class (`servo_backend.py`) at Step 11. Nothing else knows which one is loaded.

## What happens when you say "walk forward"

1. `voice/audio.py` (`MicSource`) hands 16 kHz audio blocks to the STT thread.
2. `voice/stt.py`: the self-hearing gate drops audio while hexa is speaking. Vosk runs twice on the rest: free text, and a grammar of the known commands.
3. A partial result with a stop word stops the robot at once. Otherwise, when the sentence ends, `brain/stt_decision.py` picks the text to trust.
4. `brain/router.py` turns it into stop / command / chat.
5. `brain/brain_loop.py` sends the command through the Bridge; `brain/motion_keeper.py` then sends heartbeats so the walk continues.
6. The body replies with statuses (`accepted`, `done`, `rejected`...). `brain/status_hub.py` shows them to every listener.
7. `voice/playback.py` says "okay" (pre-rendered, no waiting). While it speaks, step 2 ignores the microphone.

Chat (anything that is not a command) is Step 9: `brain/chat.py` and Ollama are not built yet.

## Safety rules that matter

| Rule | Where |
|---|---|
| **Stop always wins**, even over old messages | `body/arbitration.py`, `bridge.py` |
| Messages older than `MAX_MESSAGE_AGE_S` are dropped (a stale stop still runs) | `body/process.py` |
| **Watchdog**: walking with no command or heartbeat for `WATCHDOG_TIMEOUT_S` stops the robot | `body/controller.py` |
| Every angle is clamped once, in one place | `body/backend.py` |
| Every command is checked on arrival | `bridge.py` |
| Fallen robot refuses motion except stop | `body/process.py`, `body/controller.py` |
| Robot ignores the microphone while it speaks | `voice/stt.py` (gate), `voice/playback.py` (`speaking`) |
| A grammar match is not trusted unless it is an exact known phrase | `brain/stt_decision.py` |
| Only `voice/audio.py` touches the mic; only `voice/playback.py` touches the speaker | `AGENTS.md` |
| Piper is always one long-lived process | `voice/tts.py` |

## Threads and who talks to whom

| Thread | Reads | Writes |
|---|---|---|
| `voice-stt` | microphone blocks | recognised results |
| `voice-worker` | recognised results, statuses | commands to the body, spoken "okay" |
| `tts-synth` | sentences to say | audio clips |
| `tts-playback` | audio clips | speaker, `speaking` flag |
| `status-hub` | the body's status queue | every listener's own queue |

Threads talk only through `queue.Queue` and one shared `speaking` flag. Each queue is bounded and drops the oldest item when full, so nothing can block the robot.

## Where the pieces are (by job)

| Job | Files |
|---|---|
| Messages between programs | `bridge.py`, `commandline.py` |
| Moving legs | `body/controller.py`, `gait.py`, `kinematics.py`, `poses.py` |
| Simulation vs real robot | `body/backend.py` (contract), `sim_backend.py`, `servo_backend.py` (stub) |
| Understanding speech | `voice/stt.py`, `brain/stt_decision.py`, `brain/router.py` |
| Speaking | `voice/tts.py`, `voice/playback.py` |
| Connecting speech to the body | `brain/voice_loop.py`, `brain/brain_loop.py`, `brain/motion_keeper.py`, `brain/status_hub.py` |
| All numbers | `config.py` |
