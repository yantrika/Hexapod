"""Step 5 checks: pure command arbitration."""

from __future__ import annotations

from typing import Any

import config
from body.arbitration import arbitrate
from bridge import Command

NOW = 100.0
WALK: dict[str, Any] = {"direction": "fwd", "speed": 0.5}


def cmd(action: str, seq: int, age: float = 0.0, **params: Any) -> Command:
    return Command(action, dict(params), seq, NOW - age)


def test_latest_motion_command_wins_and_earlier_are_superseded() -> None:
    result = arbitrate([cmd("walk", 1, **WALK), cmd("sit", 2), cmd("stand", 3)], NOW)
    assert result.motion is not None and result.motion.seq == 3
    assert result.rejected == (
        (cmd("walk", 1, **WALK), "superseded"), (cmd("sit", 2), "superseded"),
    )
    assert result.stops == ()


def test_stop_in_the_batch_beats_a_walk() -> None:
    result = arbitrate([cmd("walk", 1, **WALK), cmd("stop", 2)], NOW)
    assert result.motion is None
    assert [c.seq for c in result.stops] == [2]
    assert [(c.seq, why) for c, why in result.rejected] == [(1, "superseded")]


def test_stop_wins_even_when_it_came_first() -> None:
    result = arbitrate([cmd("stop", 1), cmd("walk", 2, **WALK)], NOW)
    assert result.motion is None and [c.seq for c in result.stops] == [1]


def test_stale_motion_is_rejected_stale() -> None:
    old = cmd("walk", 1, age=config.MAX_MESSAGE_AGE_S + 0.1, **WALK)
    result = arbitrate([old], NOW)
    assert result.motion is None and result.rejected == ((old, "stale"),)


def test_stale_stop_is_still_honoured() -> None:
    old = cmd("stop", 1, age=10.0)
    result = arbitrate([old], NOW)
    assert result.stops == (old,) and result.rejected == ()


def test_stale_heartbeat_is_dropped_silently_and_fresh_one_counted() -> None:
    result = arbitrate(
        [cmd("heartbeat", 1, age=5.0), cmd("heartbeat", 2), cmd("heartbeat", 3)], NOW
    )
    assert result.heartbeats == 2 and result.rejected == () and result.motion is None


def test_a_stale_command_does_not_supersede_a_fresh_one() -> None:
    old = cmd("sit", 1, age=1.0)
    fresh = cmd("walk", 2, **WALK)
    result = arbitrate([fresh, old], NOW)
    assert result.motion == fresh and result.rejected == ((old, "stale"),)


def test_invalid_and_unknown_never_compete() -> None:
    bad = cmd("walk", 2, direction="sideways")
    result = arbitrate([cmd("stand", 1), bad, cmd("dance", 3)], NOW)
    assert result.motion is not None and result.motion.seq == 1
    assert [(c.seq, why) for c, why in result.rejected] == [
        (2, "invalid_params"), (3, "unknown_action"),
    ]


def test_duplicate_stops_are_all_kept() -> None:
    result = arbitrate([cmd("stop", 1), cmd("stop", 2)], NOW)
    assert [c.seq for c in result.stops] == [1, 2]


def test_empty_batch() -> None:
    result = arbitrate([], NOW)
    assert (result.stops, result.motion, result.heartbeats, result.rejected) == ((), None, 0, ())
