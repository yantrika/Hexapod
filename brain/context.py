"""Build the prompt for one reply and keep it under a token budget (Step 13).

A small model on a Pi is slow in proportion to its prompt, so the prompt is short and capped:

    system:  persona + mode + mood (+ "straight answer" when asked) + who is speaking
             + facts about that person + a short summary of earlier talks
    turns:   the last few (user, reply) pairs
    user:    what the person just said

``ContextBuilder.build`` returns the chat messages. The budget is ``CHAT_CONTEXT_MAX_TOKENS``,
counted by characters (``CONTEXT_CHARS_PER_TOKEN`` per token: close enough for a limit). What is
never trimmed: the persona block and the person's current words. What is added while it fits, in
this order: facts (newest first, at most ``CONTEXT_MAX_FACTS``), the recent turns (newest first, at
most ``CONTEXT_MAX_HISTORY_TURNS``, so the OLDEST turn is the first to go), then the summary. A
fact or turn that does not fit is left out whole, never cut in the middle.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import config

Messages = list[dict[str, str]]


def approx_tokens(text: str) -> int:
    """About how many tokens *text* is (characters / ``CONTEXT_CHARS_PER_TOKEN``, rounded up)."""
    return -(-len(text) // config.CONTEXT_CHARS_PER_TOKEN)


@dataclass(frozen=True)
class ContextParts:
    """Everything one reply's prompt may hold, before trimming."""

    system: str  # the persona block: never trimmed
    speaker: str  # a name, or "guest"
    facts: Sequence[str] = ()  # newest first
    summary: str | None = None
    history: Sequence[tuple[str, str]] = ()  # oldest first, (user, reply)


def trim_context(parts: ContextParts, user_text: str,
                 max_tokens: int = config.CHAT_CONTEXT_MAX_TOKENS,
                 max_facts: int = config.CONTEXT_MAX_FACTS,
                 max_turns: int = config.CONTEXT_MAX_HISTORY_TURNS) -> Messages:
    """The messages for one reply, under *max_tokens* (see the module docstring)."""
    budget = max_tokens * config.CONTEXT_CHARS_PER_TOKEN
    who = ("You are talking to a guest whose name you do not know."
           if parts.speaker == config.GUEST_NAME else f"You are talking to {parts.speaker}.")
    head = f"{parts.system}\n{who}"
    remaining = budget - len(head) - len(user_text)

    kept_facts: list[str] = []
    for fact in list(parts.facts)[:max(0, max_facts)]:
        cost = len(fact) + 3  # "- " and a newline
        if cost > remaining:
            break
        kept_facts.append(fact)
        remaining -= cost
    if kept_facts:
        remaining -= len("What you remember about them:\n")

    kept_turns: list[tuple[str, str]] = []  # newest first while collecting
    for user, reply in reversed(list(parts.history)[-max(0, max_turns):]):
        cost = len(user) + len(reply)
        if cost > remaining:
            break  # an older turn is not worth more than the newer one that did not fit
        kept_turns.append((user, reply))
        remaining -= cost
    kept_turns.reverse()

    summary = ""
    if parts.summary:
        line = f"Earlier you talked about: {parts.summary}"
        if len(line) + 1 <= remaining:
            summary = line

    system = head
    if kept_facts:
        system += "\nWhat you remember about them:\n" + "\n".join(f"- {f}" for f in kept_facts)
    if summary:
        system += "\n" + summary
    messages: Messages = [{"role": "system", "content": system}]
    for user, reply in kept_turns:
        messages += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
    messages.append({"role": "user", "content": user_text})
    return messages


class ContextBuilder:
    """Gathers the parts through callables (so this module knows no memory, no persona object) and
    trims them. ``source(user_text)`` returns the ``ContextParts`` for this reply."""

    def __init__(self, source: Callable[[str], ContextParts],
                 max_tokens: int = config.CHAT_CONTEXT_MAX_TOKENS) -> None:
        self._source = source
        self.max_tokens = max_tokens

    def build(self, user_text: str, history: Sequence[tuple[str, str]]) -> Messages:
        """*history* is the chat responder's own rolling history (oldest first)."""
        parts = self._source(user_text)
        parts = ContextParts(parts.system, parts.speaker, parts.facts, parts.summary, history)
        return trim_context(parts, user_text, self.max_tokens)
