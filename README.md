# Arya Controller

Phone app (Flutter) that drives the Arya hexapod over the local network.
This branch (`app/arya-flutter`) has no shared history with `main`, which holds the robot code.

The app speaks the robot's phone-page protocol (JSON over WebSocket, PIN-paired), defined in
`web/protocol.py` on `main`. It sends `walk`, `stop`, `stand`, `sit`, `wave`, `ptt_press`,
`ptt_release`, `say`, `set_mode`, `set_speaker`, `forget_person` and `set_roast_ok`. It never sends joint data.

Design: the "Arya Controller" canvas (Drive, Pair, Voice, Body health, Settings, Landscape).
