#!/usr/bin/env python3
"""Calibrate ONE joint from the keyboard (Raspberry Pi) and write data/calibration/servo_cal.json.

    python scripts/servo_jog.py --joint RF_coxa

Run it twice per joint (docs/HARDWARE.md has the exact procedure):
  1. horn REMOVED: find the mid-travel pulse and note it as the CENTRE (n), then write (w).
  2. horn fitted at the centre (scripts/servo_center.py), leg attached, robot held off the ground:
     note the direction SIGN (+ or -), then the safe MIN (m) and MAX (M) pulse, then write (w).
The joint is calibrated once centre, sign, min and max were all noted (in any session).

    a / d    pulse -/+ fine step         z / c    pulse -/+ coarse step
    0        back to the pulse the session started at (the stored centre)
    n        note this pulse as the CENTRE (servo at mid travel / horn neutral)
    m / M    note this pulse as the safe MIN / MAX (no stall, no touching the frame)
    +  / -   note the SIGN: the pulse is INCREASING when you press +/-; did the joint move in the
             clean POSITIVE direction?  + = yes (sign +1), - = no, it moved negative (sign -1)
             coxa positive: foot swings counter-clockwise seen from above
             femur positive: the femur rises above horizontal (the knee goes up)
             tibia positive: the leg straightens (the knee opens)
    w        write the file (validated; sets calibrated once all four are noted)
    q / Q    quit (q refuses while notes are unsaved, Q quits anyway); the servo is released

The one joint is driven through ServoBackend (slew limit, absolute pulse window, staggered attach
at its stored centre); this script never talks to a driver. SIGINT/SIGTERM/SIGHUP release it.
"""

from __future__ import annotations

import argparse
import datetime
import select
import sys
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body import servo_calibration  # noqa: E402
from body.servo_backend import ServoBackend  # noqa: E402
from body.servo_calibration import Calibration, CalibrationError, JointCal  # noqa: E402
from body.servo_safety import confirm_hardware, guarded  # noqa: E402

KEY_HELP = "a d z c 0 n m M + - w q Q"


@dataclass(frozen=True)
class JogState:
    """The tool's whole state: where the pulse is aimed and what was noted this session."""

    pulse: float
    start: float
    centre: float | None = None
    min_us: float | None = None
    max_us: float | None = None
    sign: int | None = None

    @property
    def unsaved(self) -> bool:
        return any(v is not None for v in (self.centre, self.min_us, self.max_us, self.sign))


def clamp_pulse(pulse: float) -> float:
    return min(max(pulse, float(config.SERVO_PULSE_ABS_MIN_US)),
               float(config.SERVO_PULSE_ABS_MAX_US))


def apply_key(state: JogState, key: str) -> tuple[JogState, str | None]:
    """The state after one key, and an action for the shell: ``write``, ``quit`` or ``force_quit``.

    Pure: no hardware, no file. Unknown keys change nothing.
    """
    moves = {"a": -config.SERVO_JOG_STEP_US, "d": +config.SERVO_JOG_STEP_US,
             "z": -config.SERVO_JOG_COARSE_US, "c": +config.SERVO_JOG_COARSE_US}
    if key in moves:
        return replace(state, pulse=clamp_pulse(state.pulse + moves[key])), None
    if key == "0":
        return replace(state, pulse=clamp_pulse(state.start)), None
    if key == "n":
        return replace(state, centre=state.pulse), None
    if key == "m":
        return replace(state, min_us=state.pulse), None
    if key == "M":
        return replace(state, max_us=state.pulse), None
    if key in ("+", "="):
        return replace(state, sign=1), None
    if key in ("-", "_"):
        return replace(state, sign=-1), None
    if key == "w":
        return state, "write"
    if key == "q":
        return state, "quit"
    if key == "Q":
        return state, "force_quit"
    return state, None


def quit_allowed(state: JogState) -> bool:
    """``q`` quits only when nothing noted is waiting to be written."""
    return not state.unsaved


def apply_notes(joint: JointCal, state: JogState, today: str) -> JointCal:
    """The joint with this session's notes recorded (calibrated once all four are measured)."""
    return servo_calibration.record(
        joint, centre_us=state.centre, sign=state.sign, min_us=state.min_us,
        max_us=state.max_us, today=today)


def after_write(state: JogState) -> JogState:
    """Notes are saved: forget them (the stored values are the new starting point)."""
    return replace(state, centre=None, min_us=None, max_us=None, sign=None)


def status_line(name: str, state: JogState, actual: float) -> str:
    notes = []
    for label, value in (("centre", state.centre), ("min", state.min_us), ("max", state.max_us),
                         ("sign", state.sign)):
        if value is not None:
            notes.append(f"{label}={value:+d}" if label == "sign" else f"{label}={value:.0f}")
    return (f"{name} aim {state.pulse:5.0f} us (now {actual:5.0f})  "
            f"noted: {' '.join(notes) or '-'}")


def jog(backend: ServoBackend, calibration: Calibration, name: str, path: Path,
        today: str) -> None:
    import termios
    import tty

    joint = calibration[name]
    state = JogState(pulse=joint.centre_us, start=joint.centre_us)
    saved_tty = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        backend.attach()  # staggered attach: one joint, at its stored centre
        print(f"{name}: {joint.driver} channel {joint.channel}, started at {joint.centre_us:.0f} "
              f"us. Keys: {KEY_HELP}")
        while True:
            backend.refresh()
            print(f"\r{status_line(name, state, backend.pulse_of(name))}   ", end="", flush=True)
            if not select.select([sys.stdin], [], [], 0.02)[0]:
                continue
            state, action = apply_key(state, sys.stdin.read(1))
            backend.set_raw_pulse(name, state.pulse)
            if action == "write":
                try:
                    updated = apply_notes(joint, state, today)
                    changed = calibration.with_joint(name, updated, today)
                    servo_calibration.save(changed, path)
                except CalibrationError as error:
                    print(f"\n  NOT written: {error}")
                    continue
                joint, calibration = updated, changed
                state = after_write(state)
                done = "CALIBRATED" if joint.calibrated else f"measured so far: {joint.measured}"
                print(f"\n  written {path} ({done})")
            elif action == "quit" and not quit_allowed(state):
                print("\n  unsaved notes: w writes them, Q quits without saving")
            elif action in ("quit", "force_quit"):
                return
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved_tty)
        print()


def main() -> int:
    if sys.platform == "win32":
        print("error: Raspberry Pi only (needs a Linux terminal)", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--joint", required=True, choices=config.JOINT_NAMES)
    parser.add_argument("--cal-file", type=Path, default=config.SERVO_CAL_FILE,
                        help="calibration file (default %(default)s)")
    args = parser.parse_args()
    try:
        calibration = servo_calibration.load(args.cal_file)
    except CalibrationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    joint = calibration[args.joint]
    print(f"{args.joint}: {joint.driver} channel {joint.channel}, centre {joint.centre_us:.0f} us, "
          f"calibrated={joint.calibrated}, measured={list(joint.measured)}")
    if not confirm_hardware(f"  - ONE servo ({args.joint}) moves, starting at "
                            f"{joint.centre_us:.0f} us; for the CENTRE pass the horn\n"
                            "    must be OFF\n"):
        print("aborted: nothing was moved", file=sys.stderr)
        return 1
    backend = ServoBackend(calibration, calibration_joint=args.joint)
    today = datetime.date.today().isoformat()
    return guarded(lambda: jog(backend, calibration, args.joint, args.cal_file, today),
                   backend.close)


if __name__ == "__main__":
    raise SystemExit(main())
