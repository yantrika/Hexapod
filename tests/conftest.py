"""Shared pytest plugins: the real-Vosk / Piper-rendered-speech fixtures (skip if no models).

Tests marked ``pybullet`` (they build the simulated body, directly or in the body process) skip
cleanly when pybullet is not installed (the Raspberry Pi has no pybullet); tests marked ``llm``
skip on a machine with no Ollama reachable is handled by the test itself.
"""

import os
from importlib.util import find_spec

import pytest

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
