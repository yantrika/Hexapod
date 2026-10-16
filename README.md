# Arya Controller

Phone app (Flutter) that drives the Arya hexapod over the local network, built from the
design in [design/Arya Controller.html](design/Arya%20Controller.html).
This branch (`app/arya-flutter`) has no shared history with `main`, which holds the robot code.

## Run

```
flutter pub get
flutter run                 # a phone with USB debugging on, or -d windows
flutter test                # protocol tests and screen images (test/goldens)
flutter analyze
```

Start the phone server on the robot first (`python main.py --web` or `--lan` on `main`); it
prints a 6-digit PIN. Enter the robot's address and the PIN on the first screen.

## Screens

| Screen | File |
|---|---|
| Pair (address, PIN) | `lib/pair_screen.dart` |
| Drive: Stick / Pad / Dual layouts, postures, speed, hold-to-talk, STOP | `lib/drive_screen.dart` |
| Voice: what Arya heard, did and said; type for Arya to say | `lib/voice_screen.dart` |
| Body: leg map, battery, temperature, tilt, link | `lib/health_screen.dart` |
| Settings: Arya's mood and speaker, layout, speed cap, dead zone | `lib/settings_screen.dart` |
| Dual stick, sideways | `lib/landscape_screen.dart` |

`lib/robot_link.dart` is the only file that talks to the robot.

## Protocol

JSON over WebSocket at `ws://<robot>:8765/ws?pin=<PIN>`, defined in `web/protocol.py` on `main`.
The app sends `walk` (axes in [-1, 1], repeated at the robot's `send_hz`), `stop`, `stand`, `sit`,
`wave`, `ptt_press`, `ptt_release`, `say`, `set_mode`, `set_speaker`. It never sends joint data.
It reads `hello`, `status`, `persona`, `heard`, `route` and `said`. The robot stops a moving
walk if no move message arrives within `deadman_s`, and stops when the connection closes.
One phone can drive at a time.

## Not wired yet (needs the robot side first)

- **Always-on voice and phone microphone**: the robot only supports hold-to-talk through its own microphone.
- **Battery, Pi temperature, tilt, joint angles**: the robot does not send them, so the screens show a dash.
- **Relax legs / servos on**: no such request exists in the protocol, so those two buttons are inert.
- **Finding the robot automatically**: the address is typed.
- **Saving settings between launches**: needs `shared_preferences`, which needs Windows Developer Mode.

Fonts (Instrument Sans, JetBrains Mono) are bundled in `fonts/`, both under the SIL Open Font Licence.
