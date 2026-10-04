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

## The data flow (start to finish)

```
 mic ──▶ AudioSource ──▶ [push-to-talk gate] ──▶ [self-hearing gate] ──▶ Vosk (free + grammar)
 (voice/audio.py)        (voice/ptt.py:          (voice/stt.py)            │ partial: stop word → STOP now
                          idle = dropped)                                   ▼ final
                                                              brain/stt_decision.py ──▶ brain/router.py
                                                                                          │
                      ┌──────────────── stop / command ───────────────────────────────────┤
                      ▼                                                                   │ chat
              brain/brain_loop.py ──▶ bridge.py ──▶ BODY process                          ▼
              (+ motion_keeper heartbeats)           control loop 50 Hz        only if the body is idle:
                      ▲                              controller / gait          brain/chat.py ──▶ Ollama
                      │ statuses                     backend (sim | servos)          │ sentences
              brain/status_hub.py ◀── status queue ◀──┘                              ▼
                      │                                                       voice/playback.py ──▶ speaker
                      ├──▶ brain/dialogue.py ── pre-rendered phrases ───────────────▲  (Piper, one process)
                      └──▶ status log (logs/hexa.log, one line per status)
```

`main.py` builds all of this (`brain/app.py`, class `HexaApp`) and owns the start and stop order. Press = barge-in: it cancels chat and silences the speaker.

## What happens when you say "walk forward"

1. `voice/audio.py` (`MicSource`) hands 16 kHz audio blocks to the STT thread.
2. `voice/stt.py`: the self-hearing gate drops audio while hexa is speaking. Vosk runs twice on the rest: free text, and a grammar of the known commands.
3. A partial result with a stop word stops the robot at once. Otherwise, when the sentence ends, `brain/stt_decision.py` picks the text to trust.
4. `brain/router.py` turns it into stop / command / chat.
5. `brain/brain_loop.py` sends the command through the Bridge; `brain/motion_keeper.py` then sends heartbeats so the walk continues.
6. The body replies with statuses (`accepted`, `done`, `rejected`...). `brain/status_hub.py` shows them to every listener.
7. `brain/dialogue.py` hears the body's `accepted` and `voice/playback.py` says "okay" (pre-rendered, no waiting). While it speaks, step 2 ignores the microphone.

## What happens when you just talk (chat)

Something that is not a command is chat (`brain/chat.py`, a local LLM through Ollama):

1. Only if the body is **idle** (no walk, turn or posture change). Otherwise hexa says "tell me after I stop" and the LLM is not called.
2. The reply streams in; each finished sentence (`brain/sentences.py`) goes straight to the speaker queue, so hexa starts talking before the LLM has finished.
3. A command, a stop, or any motion cancels the reply and clears the speech, so the microphone is free for "stop".
4. If the LLM is down or too slow, hexa says "I can't think right now". The LLM is never used to decide a command.

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
| Chat runs only while the body is idle; motion cancels chat speech | `brain/voice_loop.py`, `brain/brain_loop.py` |
| The LLM is never in the command path (the router decides) | `brain/router.py`, `brain/voice_loop.py` |
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
| `dialogue` | its status subscription | phrases to the speaker |
| `chat-reply` | the LLM's streamed reply | sentences to the speaker queue |

Threads talk only through `queue.Queue` and one shared `speaking` flag. Each queue is bounded and drops the oldest item when full, so nothing can block the robot.

## The rules (plan.md section 4, in short)

1. Clamps live in one place (`config.clamp`, the backend template method). The body validates every command on arrival and does not trust the brain.
2. Messages older than `MAX_MESSAGE_AGE_S` are dropped. A stale `stop` still runs.
3. Every tick: check `stop_event` first, drain the queue, a `stop` in the batch wins and holds the pose, motion commands are latest-wins, a running sit/stand/wave answers `busy`.
4. Watchdog: walking or turning with no command or heartbeat for `WATCHDOG_TIMEOUT_S` stops the robot.
5. A fallen robot refuses all motion except `stop`.
6. Queues are small and bounded; when full the oldest item is dropped and `stop` cannot be lost.
7. The LLM is never in the command path; chat only while the body is idle.
8. Push-to-talk: a voice "stop" works only while listening. The control window STOP button and Space are the always-available stop.

## Measured numbers so far (dev laptop: Core i3 M380, 2 cores, no AVX, headless body)

| What | Number | Where from |
|---|---|---|
| Body control tick (mean work time), idle | 7.3-7.9 ms (20 ms budget) | Step 8, 10 |
| Same with Vosk (2 recognizers) listening and Piper idle | 7.4-8.7 ms | Step 8, 9 |
| Same with Piper kept 100 % busy | 10.6-34 ms (walking worst) | Step 7, 8 |
| Same while the LLM generates | 15.5-16.7 ms (worst 130-205 ms) | Step 9 |
| Piper (`en_US-amy-low`), one long-lived process | start 2.6 s once, real-time factor 0.6-1.0, first audio 0.6 s (a word) to 4.2 s (a 4 s sentence) | Step 7 |
| Piper one process per sentence (ruled out) | real-time factor 1.4 | Step 7 |
| Vosk CPU, always listening | 13-25 % of a core (23 % in the last run), decode real-time factor 0.07-0.11 | Step 8, 10 |
| Vosk CPU with push-to-talk idle | 1 % (no audio decoded) | Step 10 |
| "walk forward": end of speech to command sent / to first foot move | 0.86 s / +0.26 s | Step 8 |
| Command accuracy (small US model, owner's voice, 117 attempts) | commands 72 %, stops 78 %, chat routed correctly 30/30, dangerous false positives 0, wrong motion 0 | Step 8b |
| Chat, `qwen2.5:0.5b` in Docker on this laptop | 1.5 tokens/s (bar: 3), first token 0.4-1.6 s, 500 MB, peak 90 C | Step 9 |
| Temperature | idles 56-65 C, peaks 81-90 C under load, shuts down at 87 C (acpitz) | all |

## Known limits

- **Laptop heat.** The dev laptop powers off at 87 C (acpitz) and reaches 90 C under Vosk + Piper + LLM. Run one heavy thing at a time; `scripts/cool_run.py` is the default way to run things.
- **Voice stop works only while listening in push-to-talk mode.** Use the control window STOP button or Space, or type `stop` in the terminal. In `always` mode it works except while hexa speaks.
- **Single words are unreliable on the small Vosk model** ("halt" and "freeze" were missed; "stop" is the reliable stop word; two-word commands are reliable).
- **The real LLM is untested on target hardware.** Here it is too slow (1.5 tokens/s), so chat is developed against `FakeChat`. Real timing is a Step 11 task on the Pi 5.
- No wake word yet (Step 11), no servo code (`servo_backend.py` is a stub), no phone UI (Step 12).
- Vosk has no echo cancellation: use a headset or keep the microphone away from the speaker for barge-in.

## Where the pieces are (by job)

| Job | Files |
|---|---|
| Messages between programs | `bridge.py`, `commandline.py` |
| Moving legs | `body/controller.py`, `gait.py`, `kinematics.py`, `poses.py` |
| Simulation vs real robot | `body/backend.py` (contract), `sim_backend.py`, `servo_backend.py` (stub) |
| Understanding speech | `voice/stt.py`, `brain/stt_decision.py`, `brain/router.py` |
| Speaking | `voice/tts.py`, `voice/playback.py` |
| Talking back about statuses | `brain/dialogue.py` |
| Chat | `brain/chat.py`, `brain/sentences.py` |
| Push-to-talk, barge-in | `voice/ptt.py`, `brain/voice_loop.py` |
| The whole robot, startup checks, logging | `main.py`, `brain/app.py`, `brain/startup.py`, `brain/logsetup.py` |
| Connecting speech to the body | `brain/voice_loop.py`, `brain/brain_loop.py`, `brain/motion_keeper.py`, `brain/status_hub.py` |
| All numbers | `config.py` |
