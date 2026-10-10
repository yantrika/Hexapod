"""Step 13 (P1): the context builder and its trimming (oldest first, under the budget)."""

from __future__ import annotations

import config
from brain.context import ContextBuilder, ContextParts, approx_tokens, trim_context

SYSTEM = "You are Arya."


def chars(messages: list[dict[str, str]]) -> int:
    return sum(len(m["content"]) for m in messages)


def test_the_messages_are_system_turns_then_the_user() -> None:
    parts = ContextParts(SYSTEM, "Anna", ["Likes tea."], "robots", [("hi", "hello")])
    messages = trim_context(parts, "what now")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "what now"
    system = messages[0]["content"]
    assert system.startswith(SYSTEM) and "talking to Anna" in system
    assert "- Likes tea." in system and "Earlier you talked about: robots" in system


def test_a_guest_is_called_a_guest() -> None:
    system = trim_context(ContextParts(SYSTEM, config.GUEST_NAME), "hi")[0]["content"]
    assert "guest" in system and "do not know" in system


def test_nothing_is_trimmed_when_it_all_fits() -> None:
    history = [(f"q{n}", f"a{n}") for n in range(4)]
    messages = trim_context(ContextParts(SYSTEM, "Anna", ["f1", "f2"], None, history), "now")
    assert len(messages) == 1 + 8 + 1
    assert "- f1" in messages[0]["content"] and "- f2" in messages[0]["content"]


def test_the_budget_is_never_exceeded_and_the_oldest_turns_go_first() -> None:
    history = [(f"question number {n} " + "x" * 80, f"answer number {n} " + "y" * 80)
               for n in range(6)]
    budget_tokens = 140
    messages = trim_context(ContextParts(SYSTEM, "Anna", [], None, history), "latest words",
                            max_tokens=budget_tokens, max_turns=6)
    assert chars(messages) <= budget_tokens * config.CONTEXT_CHARS_PER_TOKEN
    kept = [m["content"] for m in messages if m["role"] == "user"][:-1]
    assert kept, "some history must survive"
    numbers = [int(c.split()[2]) for c in kept]
    assert numbers == sorted(numbers) and numbers[-1] == 5  # the newest turn is the one kept
    assert 0 not in numbers  # the oldest went first
    assert messages[-1]["content"] == "latest words"


def test_the_persona_block_and_the_users_words_are_never_cut() -> None:
    long_system = "S" * 3000
    long_user = "U" * 500
    messages = trim_context(ContextParts(long_system, "Anna", ["fact"], "sum",
                                         [("a", "b")]), long_user, max_tokens=10)
    assert messages[0]["content"].startswith(long_system)
    assert messages[-1]["content"] == long_user
    assert len(messages) == 2  # everything optional was dropped, nothing was cut in half


def test_a_fact_that_does_not_fit_is_left_out_whole() -> None:
    parts = ContextParts(SYSTEM, "Anna", ["short one", "F" * 4000, "after the long one"])
    system = trim_context(parts, "hi", max_tokens=200)[0]["content"]
    assert "short one" in system
    assert "F" * 10 not in system and "after the long one" not in system


def test_at_most_max_facts_and_max_turns_are_used() -> None:
    facts = [f"fact {n}" for n in range(20)]
    history = [(f"q{n}", f"a{n}") for n in range(20)]
    messages = trim_context(ContextParts(SYSTEM, "Anna", facts, None, history), "x",
                            max_facts=3, max_turns=2)
    assert messages[0]["content"].count("\n- ") == 3
    assert [m["content"] for m in messages[1:-1]] == ["q18", "a18", "q19", "a19"]


def test_the_summary_goes_before_nothing_else_when_space_is_tight() -> None:
    history = [("q" * 100, "a" * 100)]
    tight = len(SYSTEM) + 40 + 200 + 2  # room for the turn, not the summary
    parts = ContextParts(SYSTEM, "Anna", [], "S" * 150, history)
    messages = trim_context(parts, "hi", max_tokens=-(-tight // config.CONTEXT_CHARS_PER_TOKEN))
    assert "Earlier you talked about" not in messages[0]["content"]
    assert len(messages) == 4  # the turn stayed


def test_the_default_budget_comes_from_config() -> None:
    builder = ContextBuilder(lambda text: ContextParts(SYSTEM, "Anna"))
    assert builder.max_tokens == config.CHAT_CONTEXT_MAX_TOKENS
    assert approx_tokens("a" * 8) == 2 and approx_tokens("a" * 9) == 3


def test_the_builder_uses_the_responders_history() -> None:
    builder = ContextBuilder(lambda text: ContextParts(SYSTEM, "Anna", ["Likes tea."]))
    messages = builder.build("and now?", [("hello", "hi there")])
    assert [m["content"] for m in messages[1:]] == ["hello", "hi there", "and now?"]


def test_a_realistic_prompt_fits_the_default_budget() -> None:
    from brain.persona import Persona

    system = Persona.from_file().system_prompt("roast", "happy")
    facts = [f"Fact about the person number {n}, a short sentence." for n in range(10)]
    history = [("how are you today", "I am fine thank you for asking") for _ in range(6)]
    messages = trim_context(ContextParts(system, "Anna", facts, "talked about robots", history),
                            "what is a hexapod")
    assert chars(messages) <= config.CHAT_CONTEXT_MAX_TOKENS * config.CONTEXT_CHARS_PER_TOKEN
