# hexa: the simple plan

This is the easy version. The full details are in `../plan.md`. If the two ever disagree, `plan.md` wins.

## What we are building

A robot with six legs that you can talk to.

- You say "walk forward". It walks.
- You say "stop". It stops.
- You ask a question. It talks back.
- It works with no internet. Nothing goes to the cloud. Nothing costs money.

We build and test it in a computer simulation first. Then we put the same code on a Raspberry Pi 5 with real motors.

## The big idea: two programs

```
  BRAIN program                          BODY program
  ----------------                       ----------------
  listens (microphone)                   moves the legs
  understands words        --commands--> checks every command
  talks (speaker)          <--status----- says what it is doing
```

- The **brain** listens, thinks and talks. It never moves a leg itself.
- The **body** moves the legs. It never hears or speaks.
- They only talk through two small message queues. That is the only link.
- The body can run in the simulation (PyBullet) or on the real robot. The rest of the code does not know which one it is.

## What happens when you speak

1. The microphone hears you.
2. **Vosk** turns your voice into text.
3. The **router** reads the text.
   - A stop word ("stop", "halt", "freeze") always works. It wins over everything.
   - A short command ("sit down", "turn left") becomes a command for the body.
   - Anything else is a chat. A small local AI (Ollama) answers.
4. The body does the move and sends back a status ("done", "busy", "rejected").
5. The robot speaks, based on that status. It never guesses.
6. **Piper** turns the reply into sound. It runs as one program that stays open, never one per sentence.

## Safety (the most important part)

- **Stop always wins.** It jumps the queue and holds the robot still.
- **Old commands are thrown away.** A command that arrives late is ignored. A late stop still counts.
- **Heartbeat.** While walking, the brain sends a "still here" message again and again. If they stop coming, the body stops by itself. So if the brain crashes, the robot stops.
- **Limits.** Every angle and speed is clamped. The body checks every command, even from the brain.
- **Falls.** If the robot tips over, it stops and refuses to move until reset.
- **No self-hearing.** The robot ignores the microphone while it talks (and a little after). So it does not obey its own voice.
- **Real robot power.** The motors get their own power supply. The Pi must never power the motors.

## The steps

We do one step at a time. Each step ends with all tests passing. We wait for approval before the next one.

| Step | What | State |
|---|---|---|
| 0 | Settings file and tools | done |
| 1 | Leg maths (where is the foot?) | done |
| 2 | Robot model, stand and sit in the simulation | done |
| 3 | Walking (three legs move while three stay down) | done |
| 4 | The controller and timing | done |
| 5 | The link between brain and body, stop, heartbeat | done |
| 6 | The router (words to commands) | done |
| 7 | Speech out (Piper) with a playback queue you can cancel | done |
| 8 | Speech in (Vosk), no self-hearing, command grammar | done (small US model chosen) |
| 9 | Talking from status, chat with Ollama | built (real model too slow on the old laptop; tested with a fake one) |
| 10 | Interrupt the robot while it talks (barge-in), push-to-talk | later |
| 11 | Real robot on the Pi 5 | later |
| 12 | Phone web page to drive and talk to the robot | later |

### Step 8 extras
- Audio goes through a small `AudioSource` piece. Today it is the microphone. Later it can be audio from a phone.
- One reader takes statuses from the body and shares them with many listeners (speech, command line, control window, later the web page).

### Step 10 extra
- Push-to-talk: the robot listens only while you hold a button.

### Step 12: the phone page (12a buttons now, 12b talking later)
- 12a (built): hold-to-move buttons, stand, sit, wave, STOP, a PIN, plain HTTP on a trusted network. 12b adds the push-to-talk button over HTTPS.
- Runs on the Pi. You open it on your phone on the same Wi-Fi.
- Buttons: hold to move, stand, sit, wave, stop. A push-to-talk button. A live status line.
- Safety rules for it:
  - Secure link (HTTPS with a self-made certificate), because phone browsers need it for the microphone.
  - Speech is recognised by Vosk on the Pi, never by the browser or the cloud.
  - A PIN to connect.
  - Only one person controls at a time.
  - If the phone disconnects, the robot stops.
  - Holding a button sends heartbeats. Letting go sends stop.
  - Optional: the Pi can make its own Wi-Fi for demos.

## Things we learned about Piper on the dev laptop

- The old laptop has no AVX, but Piper still works.
- Starting Piper takes about 1 to 2.6 seconds, once. So it stays open.
- After that it speaks about as fast as, or faster than, real time.
- While Piper works, it uses about two cores. The body's timing gets about twice as slow. Pre-recorded phrases ("okay", "I can't do that") avoid this. We will measure again on the Pi.
- Cancelling speech takes about 1 millisecond.

## Rules we follow

- Work on one step at a time. Do not code ahead.
- No number is hard-coded. Everything lives in `config.py`.
- Only `voice/playback.py` touches the speaker.
- Every new thing gets tests. Tests use fake clocks, not real waiting.
- Keep `pytest`, `ruff` and `mypy` clean before every commit.
- On the old laptop: run one thing at a time, and use `nice -n 19` and `timeout`.

## What we are not doing

Cameras, mapping, walking on rough ground, wake words, cloud services, and other languages.
