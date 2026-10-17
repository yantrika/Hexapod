"""The persona evaluation harness, run with FakeChat."""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

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
    assert len(lines) == len(CASES) * 2 + 2  # a header, the cases, a summary
    assert lines[0]["models"] == ["fake"] and lines[0]["modes"] == ["calm", "roast"]
    assert lines[-1]["summary"][0]["mode"] == "calm"
    printed = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "calm" in printed and "roast" in printed and "blocked" in printed
    assert persona_eval.main(["--modes", "shouting"]) == 2


def test_the_default_replies_include_lines_the_check_must_catch() -> None:
    assert len(FAKE_REPLIES) >= 8


# -- --ollama-url and several models in one file ---------------------------------------------------
class TinyOllama:
    """/api/tags lists ``pulled``; /api/chat answers every request with one harmless line and
    remembers which model it was asked for."""

    def __init__(self, pulled: list[str]) -> None:
        self.asked: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = json.dumps({"models": [{"name": name} for name in pulled]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.asked.append(json.loads(self.rfile.read(length))["model"])
                body = (json.dumps({"message": {"content": "Fine, thanks."}, "done": False})
                        + "\n" + json.dumps({"message": {"content": ""}, "done": True}) + "\n"
                        ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama() -> Iterator[TinyOllama]:
    made = TinyOllama(["alpha:1b", "beta:2b", "gamma:latest"])
    yield made
    made.close()


def test_several_models_on_another_machine_write_one_file(tmp_path: Path, ollama: TinyOllama,
                                                           capsys: object) -> None:
    out = tmp_path / "multi.jsonl"
    code = persona_eval.main(["--backend", "ollama", "--ollama-url", ollama.url,
                              "--model", "alpha:1b, beta:2b", "--modes", "calm",
                              "--out", str(out)])
    assert code == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[0]["models"] == ["alpha:1b", "beta:2b"] and lines[0]["run"]["url"] == ollama.url
    cases = [line for line in lines if "user" in line]
    assert len(cases) == len(CASES) * 2
    assert {line["model"] for line in cases} == {"alpha:1b", "beta:2b"}
    assert all(line["seconds"] >= 0 for line in cases)
    assert [line["model"] for line in lines if "summary" in line] == ["alpha:1b", "beta:2b"]
    assert set(ollama.asked) == {"alpha:1b", "beta:2b"}
    printed = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "== alpha:1b" in printed and "== beta:2b" in printed


def test_a_model_that_was_not_pulled_is_reported_before_anything_runs(
        tmp_path: Path, ollama: TinyOllama, capsys: object) -> None:
    out = tmp_path / "x.jsonl"
    code = persona_eval.main(["--backend", "ollama", "--ollama-url", ollama.url,
                              "--model", "alpha:1b,delta:3b", "--out", str(out)])
    assert code == 2 and not out.exists() and ollama.asked == []
    assert "ollama pull delta:3b" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_a_latest_tag_counts_as_pulled(ollama: TinyOllama) -> None:
    assert persona_eval.missing_models(ollama.url, ["gamma"]) == []


def test_an_unreachable_ollama_is_a_clear_error(tmp_path: Path, capsys: object) -> None:
    code = persona_eval.main(["--backend", "ollama", "--ollama-url", "http://127.0.0.1:9",
                              "--out", str(tmp_path / "y.jsonl")])
    assert code == 2
    assert "cannot reach Ollama" in capsys.readouterr().err  # type: ignore[attr-defined]
