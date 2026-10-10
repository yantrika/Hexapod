"""Step 13 (P1): the persona evaluation harness, run with FakeChat."""

from __future__ import annotations

import json
from pathlib import Path

import config
from brain.chat import FakeChat
from scripts import persona_eval
from scripts.persona_eval import BAIT, CASES, FAKE_REPLIES, HELP, NORMAL, SERIOUS, evaluate


def test_the_fixed_script_covers_every_kind_of_conversation() -> None:
    kinds = {case.kind for case in CASES}
    assert kinds == {NORMAL, SERIOUS, HELP, BAIT}
    bait = " ".join(case.text.lower() for case in CASES if case.kind == BAIT)
    for topic in ("ugly", "religion", "caste", "skin", "mother", "gay", "disabled", "fat", "sick"):
        assert topic in bait, topic
    assert sum(case.kind == SERIOUS for case in CASES) >= 2
    assert sum(case.kind == HELP for case in CASES) >= 2


def test_every_mode_runs_every_case_and_blocked_sentences_are_counted() -> None:
    records: list[dict] = []
    results = evaluate(FakeChat(FAKE_REPLIES), on_record=records.append)
    assert [r.mode for r in results] == list(config.PERSONA_MODES)
    assert len(records) == len(CASES) * 3
    for result in results:
        assert result.cases == len(CASES) and result.errors == 0
        assert result.sentences > result.blocked > 0
        assert result.bait_cases == sum(case.kind == BAIT for case in CASES)
        assert set(result.categories) >= {"family", "looks", "third_party"}
        assert sum(result.categories.values()) == result.blocked
    blocked_in_records = sum(1 for r in records for s in r["sentences"] if s["blocked"])
    assert blocked_in_records == sum(r.blocked for r in results)


def test_a_harmless_model_blocks_nothing() -> None:
    results = evaluate(FakeChat(("I would rather not. Ask me something else.",)))
    assert all(r.blocked == 0 and r.bait_with_block == 0 for r in results)


def test_a_help_answer_is_not_blocked_but_the_same_words_in_a_joke_are() -> None:
    cases = [persona_eval.Case(HELP, "I feel sick, what should I do?"),
             persona_eval.Case(NORMAL, "tell me a joke")]
    results = evaluate(FakeChat(("If you feel sick, drink water and see a doctor.",)),
                       modes=["roast"], cases=cases)
    assert results[0].blocked == 1  # only the joke


def test_a_backend_that_fails_is_counted_not_fatal() -> None:
    results = evaluate(FakeChat(fail_after=0), modes=["calm"], cases=CASES[:3])
    assert results[0].errors == 3 and results[0].sentences == 0


def test_the_prompt_the_model_sees_is_the_real_persona_prompt() -> None:
    backend = FakeChat(("Fine.",))
    evaluate(backend, modes=["roast"], cases=CASES[:1])
    system = backend.last_messages[0]["content"]
    assert "Mode: roast" in system and "talking to Tester" in system and "Likes cricket." in system


def test_main_writes_the_jsonl_and_prints_the_table(tmp_path: Path,
                                                   capsys: object) -> None:
    out = tmp_path / "eval.jsonl"
    assert persona_eval.main(["--out", str(out), "--modes", "calm,roast"]) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == len(CASES) * 2 + 1
    assert lines[-1]["summary"][0]["mode"] == "calm"
    printed = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "calm" in printed and "roast" in printed and "blocked" in printed
    assert persona_eval.main(["--modes", "shouting"]) == 2


def test_the_default_replies_include_lines_the_check_must_catch() -> None:
    assert len(FAKE_REPLIES) >= 8
