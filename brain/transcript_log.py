"""Append-only JSON-lines log of every final recognition result (``logs/transcripts.jsonl``).

One line per final: both recognizers' texts and confidences, which path decided, and what the
router did. It is the dataset for tuning the thresholds now and for Step 9b later. Writing never
raises: a full disk must not stop the robot from listening.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import config
from voice.stt import Hypothesis

logger = logging.getLogger(__name__)


def hypothesis_record(hypothesis: Hypothesis | None) -> dict[str, Any] | None:
    if hypothesis is None:
        return None
    return {
        "text": hypothesis.text,
        "conf": round(hypothesis.mean_conf, 3),
        "words": [[word, round(conf, 3)] for word, conf in hypothesis.words],
    }


class TranscriptLog:
    def __init__(self, path: Path = config.TRANSCRIPT_LOG, model: str = "") -> None:
        self.path = path
        self.model = model
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps({"time": round(time.time(), 3), "model": self.model, **record})
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as error:
            logger.error("could not write the transcript log %s: %s", self.path, error)
