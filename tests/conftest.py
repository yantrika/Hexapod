"""Shared pytest plugins: the real-Vosk / Piper-rendered-speech fixtures (skip if no models).

Tests marked ``pybullet`` (they build the simulated body, directly or in the body process) skip
cleanly when pybullet is not installed (the Raspberry Pi has no pybullet); tests marked ``llm``
skip on a machine with no Ollama reachable is handled by the test itself.
"""

import os
import tempfile
from importlib.util import find_spec
from pathlib import Path

import pytest

# The tests never touch the real memory database (data/memory/arya.db): processes the tests start
# inherit this variable, and the fixture below gives every in-process test a fresh file.
_MEMORY_DIR = tempfile.mkdtemp(prefix="hexa-memory-")
os.environ["HEXA_MEMORY_DB"] = str(Path(_MEMORY_DIR) / "arya.db")

if find_spec("pybullet") is None:  # no simulator: the body processes the tests start are dry-run
    os.environ.setdefault("HEXA_BACKEND", "dryrun")

pytest_plugins = ["tests.voice_fixtures"]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if find_spec("pybullet") is not None:
        return
    skip = pytest.mark.skip(reason="pybullet is not installed (the simulator is not available)")
    for item in items:
        if "pybullet" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _private_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    monkeypatch.setattr(config, "MEMORY_DB_PATH", tmp_path / "memory" / "arya.db")
