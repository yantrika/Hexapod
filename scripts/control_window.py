#!/usr/bin/env python3
"""Control window: keys, buttons and a command box for the hexapod. Tkinter, parent process.

It owns the body process (like ``bridge_cli``) and talks to it ONLY through the
Bridge: it never touches the controller or a backend. All key logic is in
``scripts/control_logic.py`` (pure, unit tested); typed lines go through the shared
``commandline.parse_line``. The PyBullet window is only the viewer; click this
window to drive. On the dev laptop the PyBullet window needs the Mesa override (README).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import font as tkfont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")  # single-threaded BLAS, before numpy loads (also in the child)

from body.process import BodyProcess  # noqa: E402
from brain.status_hub import StatusHub  # noqa: E402
from bridge import Status, make_bridge, new_command  # noqa: E402
from commandline import HELP, format_status, parse_line  # noqa: E402
from scripts.control_logic import (  # noqa: E402
    KEY_HELP,
    STATUS_COLOURS,
    ControlState,
    Send,
    next_state_label,
)

logger = logging.getLogger(__name__)

POLL_MS = 10  # key debounce and heartbeat timer
STATUS_POLL_MS = 20  # status queue poll (get_nowait, never blocks)
LOG_LINES = 100
FOCUS_CHECK_MS = 50  # wait this long after a FocusOut to see if the whole window lost focus
_KEYSYMS = {"space": " ", "plus": "+", "equal": "=", "minus": "-", "underscore": "_",
            "KP_Add": "+", "KP_Subtract": "-"}


class ControlWindow:
    def __init__(self, root: tk.Tk, headless: bool) -> None:
        self.root = root
        self.bridge = make_bridge()
        self.body = BodyProcess(self.bridge, headless=headless)
        self.hub = StatusHub(self.bridge)  # the one reader of the status queue
        self.statuses = self.hub.subscribe("control-window")
        self.hub.start()
        self.state = ControlState(time.monotonic)
        self.sent: dict[int, str] = {}  # seq -> Send.kind, to follow the body's statuses
        self.label = "standing"
        self.ready = False
        self.history: list[str] = []
        self.history_index = 0
        self.closing = False
        self._build()
        self.body.start()
        self.root.after(POLL_MS, self._poll_keys)
        self.root.after(STATUS_POLL_MS, self._poll_status)

    # --- layout ---------------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        root.title("hexa control")
        big = tkfont.Font(family="DejaVu Sans", size=16, weight="bold")
        mono = tkfont.Font(family="DejaVu Sans Mono", size=11)
        root.configure(padx=12, pady=10)

        self.state_var = tk.StringVar(value="starting the body process...")
        self.mode_var = tk.StringVar()
        self.mode_label = tk.Label(root, textvariable=self.mode_var, font=big, anchor="w")
        self.mode_label.pack(fill="x", pady=(2, 0))
        self.last_var = tk.StringVar(value="")
        self.state_label = tk.Label(root, textvariable=self.state_var, font=big, anchor="w")
        self.state_label.pack(fill="x")
        self.last_label = tk.Label(root, textvariable=self.last_var, font=mono, anchor="w")
        self.last_label.pack(fill="x")

        tk.Label(root, text=KEY_HELP, font=mono, justify="left", anchor="w").pack(
            fill="x", pady=(8, 8)
        )

        buttons = tk.Frame(root)
        buttons.pack(fill="x")
        for text, line in (("Stand", "stand"), ("Sit", "sit"), ("Wave", "wave")):
            tk.Button(buttons, text=text, font=big, width=8,
                      command=partial(self._send_text, line)).pack(side="left", padx=4)
        tk.Button(buttons, text="STOP", font=big, bg="#d11a2a", fg="white",
                  activebackground="#a30f1c", activeforeground="white", width=10, height=2,
                  command=lambda: self._send_text("stop")).pack(side="right", padx=4)

        self.entry = tk.Entry(root, font=mono)
        self.entry.pack(fill="x", pady=(10, 4))
        tk.Label(root, text=HELP, font=mono, anchor="w", fg="#555").pack(fill="x")
        self.log = tk.Text(root, height=12, width=90, font=mono, state="disabled", wrap="none")
        self.log.pack(fill="both", expand=True, pady=(4, 4))
        for kind, colour in STATUS_COLOURS.items():
            self.log.tag_configure(kind, foreground=colour)
        self.log.tag_configure("echo", foreground="#555")

        self.scale_var = tk.StringVar()
        tk.Label(root, textvariable=self.scale_var, font=mono, anchor="w").pack(fill="x")
        self._show_scale()

        root.bind("<KeyPress>", self._on_press)
        root.bind("<KeyRelease>", self._on_release)
        root.bind("<FocusOut>", self._on_focus_out)
        root.bind("<FocusIn>", lambda _e: self._show_mode())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.entry.bind("<FocusIn>", lambda _e: self._entry_focus(True))
        self.entry.bind("<FocusOut>", lambda _e: self._entry_focus(False))
        root.bind("<Button-1>", self._on_click)  # a click anywhere but the box returns to the keys
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Up>", lambda _e: self._recall(-1))
        self.entry.bind("<Down>", lambda _e: self._recall(1))
        self.entry.bind("<Escape>", lambda _e: self.root.focus_set())
        root.bind("<Return>", self._focus_entry)
        root.bind("<Tab>", self._focus_entry)
        root.focus_force()
        self._show_mode()

    # --- input ------------------------------------------------------------------
    def _key_of(self, event: tk.Event) -> str:
        keysym = str(event.keysym)
        return _KEYSYMS.get(keysym, keysym.lower() if len(keysym) == 1 else "")

    def _on_press(self, event: tk.Event) -> None:
        if self.ready:
            self._dispatch(self.state.key_press(self._key_of(event)))

    def _on_release(self, event: tk.Event) -> None:
        self.state.key_release(self._key_of(event))

    def _entry_focus(self, focused: bool) -> None:
        self._dispatch(self.state.set_entry_focus(focused))
        self._show_mode()

    def _on_click(self, event: tk.Event) -> None:
        if event.widget is not self.entry:
            self.root.focus_set()

    def _show_mode(self) -> None:
        """Say which mode the keyboard is in: typing in the box, or driving with the keys."""
        if self.state.entry_focused:
            self.mode_var.set("TYPING in the box: keys are OFF. Press Esc or click outside it.")
            self.mode_label.configure(fg="#c27a00")
        else:
            self.mode_var.set("KEYS ACTIVE: W A S D Q E move, Space stops.")
            self.mode_label.configure(fg="#1b8a3a")

    def _focus_entry(self, _event: tk.Event) -> str:
        self.entry.focus_set()
        return "break"

    def _on_focus_out(self, event: tk.Event) -> None:
        if event.widget is self.root:  # the toplevel itself, not a child changing focus
            self.root.after(FOCUS_CHECK_MS, self._check_focus)

    def _check_focus(self) -> None:
        if not self.closing and self.root.focus_displayof() is None:
            self._dispatch(self.state.focus_lost())
            self.mode_var.set("This window lost the focus (stop sent). CLICK IT to drive again.")
            self.mode_label.configure(fg="#c0182b")

    def _on_enter(self, _event: tk.Event) -> str:
        """Send the typed line and return to the keys. ``"break"`` keeps the window-level
        Enter binding (which focuses the box) from undoing that."""
        line = self.entry.get().strip()
        self.entry.delete(0, "end")
        if not line:
            self.root.focus_set()
            return "break"
        self.history.append(line)
        self.history_index = len(self.history)
        self._send_text(line)
        self.root.focus_set()  # back to the keys; Enter or Tab types the next command
        return "break"

    def _recall(self, step: int) -> str:
        if self.history:
            self.history_index = max(0, min(len(self.history), self.history_index + step))
            at_end = self.history_index >= len(self.history)
            text = "" if at_end else self.history[self.history_index]
            self.entry.delete(0, "end")
            self.entry.insert(0, text)
        return "break"

    def _send_text(self, line: str) -> None:
        parsed = parse_line(line)
        if parsed is None:
            self._log_line(f"?? {line!r}: not a command", "echo")
            return
        self._dispatch([Send(*parsed)], echo=True)

    # --- bridge ---------------------------------------------------------------------
    def _dispatch(self, sends: list[Send], echo: bool = False) -> None:
        if not self.ready:
            return
        for item in sends:
            command = new_command(item.action, item.params)
            self.bridge.send(command)
            if item.action != "heartbeat":
                self.sent[command.seq] = item.kind
                if len(self.sent) > 500:
                    self.sent.pop(next(iter(self.sent)))
            if item.action != "heartbeat":  # typed, clicked or key-driven: show what was sent
                self._log_line(f"-> {item.action} {item.params}", "echo")
        if any(item.action == "walk" for item in sends):
            self._show_scale()

    def _poll_keys(self) -> None:
        if self.closing:
            return
        self._dispatch(self.state.poll())
        self.root.after(POLL_MS, self._poll_keys)

    def _poll_status(self) -> None:
        if self.closing:
            return
        if not self.ready and self.bridge.ready_event.is_set():
            self.ready = True
            self._show_state()
        elif not self.ready and not self.body.alive:
            self.state_var.set("the body process died; see the terminal")
        for status in self.statuses.get_all():  # get_nowait: the UI never waits on the body
            self._on_status(status)
        self.root.after(STATUS_POLL_MS, self._poll_status)

    # --- display ----------------------------------------------------------------------
    def _on_status(self, status: Status) -> None:
        self.label = next_state_label(self.label, status, self.sent)
        colour = STATUS_COLOURS.get(status.status, "black")
        self.last_var.set(format_status(status))
        self.last_label.configure(fg=colour)
        self._show_state(colour)
        self._log_line(format_status(status), status.status)

    def _show_state(self, colour: str = "black") -> None:
        self.state_var.set(f"state: {self.label}")
        self.state_label.configure(fg=colour)

    def _show_scale(self) -> None:
        self.scale_var.set(f"speed scale: {self.state.scale:.0%} of max  (+ / - to change)")

    def _log_line(self, text: str, tag: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.log.delete("1.0", f"{lines - LOG_LINES + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- shutdown ------------------------------------------------------------------------
    def close(self) -> None:
        if self.closing:
            return
        if self.ready:
            self._dispatch(self.state.close())
            time.sleep(0.05)  # let the stop reach the queue before the body is told to exit
        self.closing = True
        self.hub.stop()
        self.body.shutdown()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="PyBullet viewer window (default)")
    mode.add_argument("--headless", action="store_true", help="no viewer, PyBullet DIRECT")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    root = tk.Tk()
    window = ControlWindow(root, headless=args.headless)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        window.close()
    finally:
        window.hub.stop()
        window.body.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
