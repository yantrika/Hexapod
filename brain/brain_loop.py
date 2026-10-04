"""Route a result, send it to the body, keep a held walk alive: shared by the typed-text and
voice front ends. Statuses come from a ``StatusHub`` subscription, never from the bridge queue.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import config
from brain.motion_keeper import MotionKeeper
from brain.router import RouteResult, route
from brain.status_hub import StatusHub
from bridge import Bridge, Command, Status, new_command

_TRANSITIONS = {  # actions that keep the body busy for a while without a held walk
    "sit": config.SIT_STAND_TRANSITION_S + config.SETTLE_S,
    "stand": config.SIT_STAND_TRANSITION_S + config.SETTLE_S,
    "wave": config.WAVE_DURATION_S + 2 * config.WAVE_BLEND_S,
}


class BrainLoop:
    """Send routed commands, feed the motion keeper, send due heartbeats. Thread-safe."""

    def __init__(
        self,
        bridge: Bridge,
        clock: Callable[[], float] = time.monotonic,
        max_walk_s: float = config.VOICE_WALK_MAX_S,
        hub: StatusHub | None = None,
    ) -> None:
        self.bridge = bridge
        self._clock = clock
        self.keeper = MotionKeeper(clock, max_walk_s)
        self._sent_listeners: list[Callable[[Command], None]] = []
        self._busy_until = 0.0  # a sit/stand/wave transition is running until this time
        self._busy_ref: int | None = None
        self._lock = threading.Lock()
        self._owns_hub = hub is None
        self.hub = hub if hub is not None else StatusHub(bridge)
        self._subscription = self.hub.subscribe("brain-loop")
        if self._owns_hub:
            self.hub.start()

    def add_sent_listener(self, listener: Callable[[Command], None]) -> None:
        """Call *listener* with every command this loop sends (not heartbeats)."""
        self._sent_listeners.append(listener)

    def body_idle(self) -> bool:
        """True when no walk or turn is held and no sit/stand/wave transition is running. Chat only
        runs then: the LLM is slow, and the microphone must stay free for "stop" while moving."""
        with self._lock:
            return self.keeper.held_seq is None and self._clock() >= self._busy_until

    def handle_text(self, text: str) -> RouteResult:
        """Route *text* and send the command. A stop is sent first and never waits."""
        return self.handle_route(route(text))

    def handle_route(self, result: RouteResult) -> RouteResult:
        """Send what an already routed utterance asks for (chat sends nothing)."""
        if result.kind == "chat":
            return result
        command = new_command(result.action or "", result.params)
        self.bridge.send(command)  # immediately; the keeper only observes afterwards
        with self._lock:
            self.keeper.on_sent(command)
            if command.action in _TRANSITIONS:
                self._busy_until = self._clock() + _TRANSITIONS[command.action]
                self._busy_ref = command.seq
        for listener in self._sent_listeners:
            listener(command)
        return result

    def pump(self) -> list[Status]:
        """Read this loop's statuses, feed the keeper and send the heartbeats that are due."""
        statuses = self._subscription.get_all()
        with self._lock:
            for status in statuses:
                self.keeper.on_status(status)
                if status.ref_seq == self._busy_ref and status.status in (
                    "done", "rejected", "busy", "error"
                ):
                    self._busy_until, self._busy_ref = 0.0, None  # the transition is over
            beats = self.keeper.tick()
        for beat in beats:
            self.bridge.send(beat)
        return statuses

    def close(self) -> None:
        self.hub.unsubscribe(self._subscription)
        if self._owns_hub:
            self.hub.stop()
