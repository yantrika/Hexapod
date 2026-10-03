"""Pure command arbitration: what the body does with one tick's drained batch.

Rules (plan section 4): stale messages are dropped (a stale ``stop`` is still
honoured, a stale heartbeat vanishes silently); invalid ones are rejected and never
compete; ``stop`` beats every motion command in the batch (each gets
``rejected(superseded)``); otherwise the newest motion command wins.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from bridge import MOTION_ACTIONS, Command, validate_command


@dataclass(frozen=True)
class Arbitration:
    stops: tuple[Command, ...]  # every stop, in order (each is answered; stop is idempotent)
    motion: Command | None  # the winning motion command
    heartbeats: int  # fresh heartbeats (they only refresh the watchdog)
    rejected: tuple[tuple[Command, str], ...]  # (command, reason), in batch order


def arbitrate(
    batch: list[Command], now: float, max_age_s: float = config.MAX_MESSAGE_AGE_S
) -> Arbitration:
    stops: list[Command] = []
    contenders: list[Command] = []
    rejected: list[tuple[Command, str]] = []
    heartbeats = 0
    for command in batch:
        problem = validate_command(command)
        if problem == "unknown_action":
            rejected.append((command, problem))
            continue
        stale = now - command.timestamp > max_age_s
        if command.action == "stop":
            stops.append(command)  # a stale stop still runs: stopping late beats not stopping
        elif stale:
            if command.action != "heartbeat":
                rejected.append((command, "stale"))
        elif problem is not None:
            rejected.append((command, problem))
        elif command.action == "heartbeat":
            heartbeats += 1
        else:
            assert command.action in MOTION_ACTIONS
            contenders.append(command)

    winner: Command | None = None
    if contenders:
        winner = contenders[-1]
        losers = contenders if stops else contenders[:-1]
        if stops:
            winner = None
        rejected.extend((command, "superseded") for command in losers)
    rejected.sort(key=lambda item: batch.index(item[0]))
    return Arbitration(tuple(stops), winner, heartbeats, tuple(rejected))
