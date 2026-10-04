"""Logging for the running robot: one rotating file (``config.LOG_FILE``) plus the console.

The file rotates by size (``LOG_MAX_BYTES``, ``LOG_BACKUP_COUNT``), so a robot running for weeks
on the Pi's SD card never fills it. Each status and each route result is one INFO line.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import config

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_hexa_handler"


def parse_level(name: str) -> int:
    level = logging.getLevelName(name.upper())
    if not isinstance(level, int):
        raise ValueError(f"unknown log level {name!r} (use DEBUG, INFO, WARNING or ERROR)")
    return level


def setup_logging(level: str = config.LOG_LEVEL, log_file: Path | None = None,
                  console: bool = True) -> Path:
    """Configure the root logger (idempotent). Returns the log file path."""
    numeric = parse_level(level)
    path = Path(log_file) if log_file is not None else Path(config.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(numeric)
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        path, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(logging.Formatter(FORMAT))
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    return path
