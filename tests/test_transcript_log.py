"""Step 8b: the transcript log never raises and writes one JSON line per record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.transcript_log import TranscriptLog, hypothesis_record
from voice.stt import Hypothesis


def test_one_json_line_per_record(tmp_path: Path) -> None:
    log = TranscriptLog(tmp_path / "logs" / "t.jsonl", model="us")
    log.append({"kind": "final", "path": "free"})
    log.append({"kind": "final", "path": "grammar-command"})
    lines = (tmp_path / "logs" / "t.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["path"] for r in records] == ["free", "grammar-command"]
    assert all(r["model"] == "us" and r["time"] > 0 for r in records)


def test_a_write_error_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    log = TranscriptLog(blocker / "t.jsonl")  # a "directory" that is a file
    log.append({"kind": "final"})
    assert "could not write the transcript log" in caplog.text


def test_hypothesis_record_keeps_words_and_confidences() -> None:
    record = hypothesis_record(Hypothesis("sit down", (("sit", 0.9), ("down", 0.7))))
    assert record == {"text": "sit down", "conf": 0.8, "words": [["sit", 0.9], ["down", 0.7]]}
    assert hypothesis_record(None) is None
