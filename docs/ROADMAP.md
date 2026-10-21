# Roadmap

What is next and what is open. History is in git (`git log`, tags `v0.1-sim`, `v0.2-web-a`,
`v0.2-web-b`, `v0.3-pre-cleanup`). Each item below is approved on its own before it is built.

## Decisions (owner, 2026-10-22)

- Branch `arya` is the main line. The old `main` is frozen for reference.
- `llama3.2:3b` is the locked chat model (it had 1 hard-limit block in 60 cases, the others 6-9).
- Compute split: the Pi runs everything safety- and latency-critical. A PC "brain server" (i5 12th
  gen desktop, CPU first, GPU optional later) runs the heavy models when it is reachable on the
  LAN. Commands never depend on the PC. When the PC is unreachable Arya falls back to the Pi
  models automatically and says so once.
- A front VL53L1X time-of-flight distance sensor (I2C, 3.3 V) will be added for follow-me.
- The Hailo AI HAT is an optional future upgrade; plan for CPU now, with swappable detector
  interfaces.
- Licences: no AGPL models or libraries (for example Ultralytics YOLO) in the project.
- A React dashboard with a new design is a separate track (replaces the plain web page, admin
  features in the same app behind the same PIN, the phone keeps a simple view). Not part of the
  phases below.

## Order

A -> B -> C -> D, with E in parallel -> F -> G. Separate tracks: dashboard, hardware assembly,
research paper. Model choices in the phases are candidates to benchmark (table in
`ARCHITECTURE.md`).

## Phases

### A. Brain server and failover
Goal: one PC service exposing chat (Ollama), re-transcription, TTS and scene description over the
LAN, with the Pi switching to its own models when the PC is not there.
- The service uses the same PIN/auth model as the robot and runs on Windows and Linux PCs.
- The Pi health-checks the PC, switches automatically, says so once, and logs which model
  answered each request.
- Done when: unplugging the PC mid-chat gives a Pi answer without a crash or a stuck robot;
  commands work identically with the PC on, off or slow.
- Measure: first-token and total chat time (PC vs Pi), health-check cost, switch-over time.

### B. Speech recognition
Goal: commands and long questions understood far better than Vosk small (61% US, 67% IN).
- Record a benchmark set per teammate (short commands including "hi" and "wave", 20-30 long
  questions) on the phone app and on the USB mic.
- WER table: Vosk vs Moonshine sizes vs Parakeet TDT 0.6B v3.
- New pipeline: Moonshine streaming on the Pi with command/name hints; utterances over about 5
  words are re-transcribed on the PC; optional LLM cleanup for chat text only.
- Commands always come from the raw transcript through the router, never from an LLM rewrite.
  "hi"/"hello" greets and waves.
- Fine-tune Moonshine on team recordings (GPU) only if WER is still poor.
- Done when: the WER table is written down and the new pipeline beats Vosk on the benchmark set.
- Measure: WER per speaker and mic, end-of-speech to command sent, Pi CPU.

### C. Voice
Goal: a better voice with a Pi fallback that is always available.
- Kokoro-82M on the PC with a chosen voice/accent (listening test); Piper medium English voice
  on the Pi as fallback; test Kokoro on the Pi and keep it there if real-time.
- Done when: the voice is chosen by listening test and the fallback speaks when the PC is gone.
- Measure: real-time factor and first-audio time on PC and Pi.

### D. Intelligence
Goal: Arya answers about herself and the world without inventing facts.
- Robot-status Q&A from live status. General Q&A in calm mode on the PC model. Time and date.
  Memory across days (exists).
- "What do you see": one camera frame sent to the PC vision model, used once, never saved or
  logged.
- LLM tools are read-only (status, time, describe scene, memory lookup). The LLM never moves the
  robot.
- Done when: status answers match the body's STATUS in tests, and no frame is written anywhere.
- Measure: reply latency for each kind of question, PC vs Pi.

### E. Moves library (parallel with D)
Goal: dance, bow, nod, wiggle, stretch, look around, push-up, wave with two legs.
- Keyframe sequences through the same clamp and slew limit, sim-tested first.
- Router phrases ("Arya, dance"); allowed only when idle; STOP interrupts any move.
- Each move is checked against MG995 torque before it runs on hardware.
- Done when: every move runs in the sim, STOP interrupts each one, and the torque check is
  written down.
- Measure: peak joint torque per move, duration, tilt in the sim.

### F. Vision speed on CPU
Goal: a smoother camera pipeline and a body detector.
- Capture thread; 10-15 fps target on the Pi (higher when chat runs on the PC); person (body)
  detector added; detector interface swappable for Hailo later.
- Done when: fps, CPU and temperature are measured on the Pi with the detector on and off.

### G. Follow-me
Goal: Arya follows one person, slowly, and stops for any reason.
- Starts only on an explicit voice/app command; never starts on its own.
- Ends on "stop", STOP, person lost for more than about 1 s, too close (body size and the
  VL53L1X), a time limit, or the watchdog. Slow speed cap.
- Needs the VL53L1X fitted (`HARDWARE.md`).
- Done when: every end condition is tested in the sim and with a fake sensor, then on hardware
  with someone at the kill switch.
- Measure: stop distance, loss-to-stop time, speed.

## Open (hardware and Pi checks that exist already)

### Servo bring-up (stages 11e and 11f), hardware needed
Stages 11a-11d are done (software on the Pi against the simulated body, readiness checklist,
servo backend written and reviewed 2026-10-17). Still open:
- **11e, bench**: owner-run Pi setup (I2C, PWM overlay, udev rule), power rail checked, then
  centre / sign / range of each of the 18 joints with `servo_jog.py`, horns fitted with
  `servo_center.py`, the `kill -9` test, keyboard drive held off the ground. Procedure and
  checklists: `HARDWARE.md`.
- **11f, ground tests**: stand, sit, walk, stop behave like the simulation; the kill switch removes
  servo power while the Pi runs; killing the brain stops the robot through the watchdog.
- Optional OE wire on the PCA9685 (`config.PCA9685_OE_GPIO`), decided at 11e.
- Weigh the real parts and replace the placeholder masses in `config.py`; re-run
  `scripts/torque_report.py` and size the servos and supply before cutting a frame.

### Pi voice and chat (stage 11b), needs a USB microphone and speaker
- Run `mic_check.py`, `stt_check.py` and `main.py` with the real devices in push-to-talk and
  always mode; judge barge-in and the self-hearing gate with the real speaker/microphone positions.
- Install Ollama on the Pi (`HARDWARE.md`) and time the locked model `llama3.2:3b` with
  `measure_chat.py` and `pytest -m llm` (bar: 3 tokens/s, first token under 5 s). This is the
  failover model of phase A; if it is too slow the Pi falls back to `FakeChat` and the reason is
  written here.
- Re-measure Piper's and Vosk's load on the body with real audio (`measure_voice.py`); phases B
  and C replace Vosk and Piper only if they win the benchmarks.
- Wake word (OpenWakeWord) as a third listening mode.

### Phone page on a real phone (stage 11c)
Check on a phone on the Pi's Wi-Fi: multi-touch, `pointercancel`, long-press menu, screen lock,
Wi-Fi loss, hold-to-talk with the robot's microphone. Write the result into `ARCHITECTURE.md`.

### Step 12c and 12c-2: phone microphone and Arya's voice on the phone (built; checked on a real phone, 2026-10-21)
Built: `--https` with a self-signed certificate (`scripts/make_cert.py`), hold-to-talk audio from the
phone page into Vosk on the Pi, and `--phone-voice` (Arya's answers play on the phone). The owner
checked the microphone path on an Android phone (PIN, wrong PIN, "walk forward", "sit down"); the
voice playback on a real phone is not confirmed yet. Still open: time release to command-sent
(median, p95) and the CPU of the server path while streaming, on the laptop and the Pi, and write
the numbers into `ARCHITECTURE.md`. A native app would need its own audio path (the plain-`ws://`
`--audio-source phone` design was dropped in favour of this one when the branches were merged).

### Step 13: Arya, P2-P6 (P1, P3, P4, P5 done; P2 decided; only the Pi timing of P2 and P6 remain)
Decisions (owner): English only; female Piper voice; pretrained models only, no training (phase B
may fine-tune Moonshine only if WER stays poor); USB webcam facing forward. The brain-server idea
is now phase A (a PC on the LAN, no longer an RTX 3060 only).
- **P2 chat model, decided: `llama3.2:3b`** (Pi timing open; laptop part done 2026-10-21, `persona_eval.py` on the four installed
  models, hard-limit blocks over 60 cases: qwen2.5:1.5b 9, llama3.2:1b 8, gemma3:1b 6, llama3.2:3b 1.
  Blocked sentences are not spoken. Speed and RAM are the Pi's job, so
  `config.OLLAMA_MODEL` is still to be set to `llama3.2:3b`, a code change outside this docs
  commit): time it on the Pi for speed, RAM and temperature, and try the extractor prompt on the
  real model (today a rule-based stand-in plays it with `--chat fake`).
- **P3 vision process, events only (done; measured on the Pi 5, `ARCHITECTURE.md`)**: webcam, YuNet face detection, tracker, the events
  `person_appeared`, `person_left`, `face_bearing`; no recognition. Done when events are right on a
  live camera and a recorded clip, an unplugged camera never stalls speech or the body, and
  CPU/temperature on the Pi are measured.
- **P4 recognition, consent-based enrolment, forget me** (P4a, the vision side with
  `scripts/face_check.py`, is written; the brain wiring and the spoken yes/name flow are built: `brain/vision_link.py`,
  `brain/face_flow.py`; checked on the laptop with a webcam): SFace
  on new tracks only; threshold
  from our own pairs; enrolment only after "yes"; names are confirmed ("Did you say Priya?")
  before they are stored, because a name heard once is often another word; the phone page can
  always type the name.
- **P5 behaviour layer, built** (`brain/behaviour.py`, rules, not the LLM; auto-roast is NOT built): greet by name, ask an unknown
  person their name once, wave, turn toward a person, only `stand`/`wave`/10-20 degree turns, only
  when idle, with cooldowns, STOP winning, a tracking toggle; auto-roast only for people whose
  `roast_ok` is true. Tested with a fake clock.
- **P6 everything together on the Pi, measured**: persona, memory, vision, chat, voice and body;
  report body tick, reply latency and peak temperature.

## Done (one line each)

- Step 0-2: config and tooling; kinematics; backend contract, URDF, PyBullet stand/sit.
- Step 3-5: tripod gait; controller and body timing; Bridge, arbitration, watchdog, body process.
- Step 6-8b: router; Piper TTS and playback; Vosk STT with the self-hearing gate and the command grammar.
- Step 9-10: status-driven speech and chat; push-to-talk, barge-in, instant filler; `main.py`, startup checks, log (tag `v0.1-sim`).
- Step 10c: servo and power sizing from the simulation.
- Step 11a: software on the Pi 5 against the dry-run body (tests, sync, tmux runner, measurements).
- Step 11d/11-backend: hardware checklist; servo backend with calibration file, safe start, staggered attach, idle release, signals, slew (approved 2026-10-17).
- Step 12a-12b: phone page, PIN and deadman; hold-to-talk with the robot's microphone (tags `v0.2-web-a`, `v0.2-web-b`).
- Step 12c (server): phone microphone over the WebSocket into a network audio source (measurements open).
- Step 13 P1: persona modes, memory, extractor, output check, router additions, web persona section.
- Step 13 P4 wiring and P5: `--vision`, spoken enrolment, behaviour layer (laptop only; not timed on the Pi).
- Step 13 P3 and P4: vision process (YuNet, tracker), SFace recognition, consent-based enrolment, measured on the Pi 5; `--vision-preview`.
- Step 13 P2 (decision): `llama3.2:3b` locked as the chat model (2026-10-22).
- Repo cleanup (tag `v0.3-pre-cleanup` is the state before it).

## Separate tracks

- **Dashboard**: a React app with a new design replaces the plain web page; admin features live
  in the same app behind the same PIN; the phone keeps a simple view. Not part of phases A-G.
- **Hardware assembly**: the servo bring-up above, the VL53L1X fitting, weighing the parts.
- **Research paper**: below.

## Parked

- Branch `step-10d-mg995` (commit `4f7af4c`): MG995 planning values, a joint speed limit, a mass
  budget and a feasibility sweep. Revisit when hardware sizing starts; the owner asked not to model
  servo realism in the sim until then.
- `origin/Nick`: Arduino sketches and a single-leg calibration server (teammate's work, outside
  this code base).

## Research: the paper (kept outside the repo as `hexa_paper_plan.md`)

Idea: make a cheap hexapod (analog MG995 servos, no joint feedback, Raspberry Pi 5) walk with a
policy trained in simulation, by first measuring and modelling how cheap servos behave. Part A:
bench-measure the servo (response delay, deadband, speed under load, torque drop with voltage sag,
unit-to-unit variation; real angle from the internal potentiometer with an ADC or from camera
markers) and put a small servo model (delay, deadband, speed limit, torque curve) into the
simulator; compare the scripted tripod gait in sim with and without the model against the real
robot (distance, heading drift, tilt, foot slip). Part B: train an RL policy on the RTX 3060 and
test it on the real robot with someone at the kill switch. Part A alone is a complete small paper.
Needs from this repo: the simulator, `scripts/torque_report.py`, the servo backend and the
calibration tools. Risks: MG995 may be too weak for the leg length (check torque before cutting
the frame); RL can shake servos (limit action changes and speed).
