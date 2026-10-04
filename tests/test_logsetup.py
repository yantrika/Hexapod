"""Step 10b: the rotating log file."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import config
from brain.logsetup import parse_level, setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_the_log_file_rotates_by_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOG_MAX_BYTES", 2000)
    monkeypatch.setattr(config, "LOG_BACKUP_COUNT", 2)
    path = setup_logging("INFO", tmp_path / "hexa.log", console=False)
    for number in range(400):
        logging.getLogger("test").info("status line number %d with some padding text", number)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["hexa.log", "hexa.log.1", "hexa.log.2"]  # old ones kept, the rest dropped
    assert all(p.stat().st_size <= 2100 for p in tmp_path.iterdir())
    assert "status line number 399" in path.read_text()


def test_setup_is_idempotent(tmp_path: Path) -> None:
    for _ in range(3):
        setup_logging("INFO", tmp_path / "hexa.log", console=True)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_hexa_handler", False)]
    assert len(ours) == 2  # one file + one console, not three of each


def test_the_level_is_respected(tmp_path: Path) -> None:
    path = setup_logging("WARNING", tmp_path / "hexa.log", console=False)
    logging.getLogger("x").info("quiet")
    logging.getLogger("x").warning("loud")
    text = path.read_text()
    assert "loud" in text and "quiet" not in text


def test_an_unknown_level_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse_level("chatty")
    assert parse_level("debug") == logging.DEBUG
