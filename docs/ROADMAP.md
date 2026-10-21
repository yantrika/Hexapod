# Roadmap

What is next and what is open. History is in git (`git log`, tags `v0.1-sim`, `v0.2-web-a`,
`v0.2-web-b`, `v0.3-pre-cleanup`). Each item below is approved on its own before it is built.

## Open

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
- Install Ollama on the Pi (`HARDWARE.md`), time `qwen2.5:0.5b` and `qwen2.5:1.5b` with
  `measure_chat.py` and `pytest -m llm` (bar: 3 tokens/s, first token under 5 s); otherwise chat
  stays on `FakeChat` and the reason is written here.
- Re-measure Piper's and Vosk's load on the body with real audio (`measure_voice.py`).
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

### Step 13: Arya, P2-P6 (P1, P4 wiring and P5 are built; checked on the laptop; P2 and P6 on the Pi remain)
Decisions (owner): English only; female Piper voice; pretrained models only, no training; Pi first
(an Ollama "brain server" on an RTX 3060 over the LAN only if Pi numbers say it is needed); USB
webcam facing forward.
- **P2 chat model on the Pi** (laptop part done 2026-10-21, `persona_eval.py` on the four installed
  models, hard-limit blocks over 60 cases: qwen2.5:1.5b 9, llama3.2:1b 8, gemma3:1b 6, llama3.2:3b 1.
  Blocked sentences are not spoken. Speed and RAM are the Pi's job, so
  `config.OLLAMA_MODEL` is unchanged): install Ollama, test 2-3 small models for speed, RAM,
  temperature and persona quality with `persona_eval.py` (candidates `qwen2.5:1.5b`,
  `llama3.2:1b`, `gemma3:1b`; references `qwen2.5:0.5b`, `llama3.2:3b`), pick one, and try the
  extractor prompt on the real model (today a rule-based stand-in plays it with `--chat fake`).
- **P3 vision process, events only**: webcam, YuNet face detection, tracker, the events
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
- Repo cleanup (tag `v0.3-pre-cleanup` is the state before it).

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
