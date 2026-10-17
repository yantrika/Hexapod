"""The docs stay small and true: only the four doc pages exist, README or DEVELOPMENT mentions
every top-level folder, and every repo path the docs name exists."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import config

ROOT = Path(config.PROJECT_ROOT)
DOC_PAGES = {"ARCHITECTURE.md", "DEVELOPMENT.md", "HARDWARE.md", "ROADMAP.md"}


def tracked_top_level_folders() -> set[str]:
    try:
        listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                                 check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return {line.split("/")[0] for line in listing.splitlines() if "/" in line}


def test_docs_folder_holds_only_the_four_pages() -> None:
    found = {path.name for path in (ROOT / "docs").iterdir() if path.is_file()}
    assert found == DOC_PAGES, f"docs/ must hold exactly {sorted(DOC_PAGES)}, found {sorted(found)}"


def test_every_top_level_folder_is_mentioned_in_readme_or_development() -> None:
    text = (ROOT / "README.md").read_text() + (ROOT / "docs" / "DEVELOPMENT.md").read_text()
    missing = sorted(name for name in tracked_top_level_folders() if f"`{name}/`" not in text)
    assert not missing, f"mention these as `name/` in README.md or DEVELOPMENT.md: {missing}"


PATH_PATTERN = re.compile(
    r"`((?:body|brain|voice|web|scripts|tests|docs|data|deploy)/[\w./-]+|"
    r"(?:main|config|bridge)\.py|AGENTS\.md|README\.md|requirements[\w-]*\.txt|pyproject\.toml)`"
)
RUNTIME_PATHS = {"data/memory/", "data/memory/arya.db"}  # made when the robot runs; git-ignored
PLANNED_PATHS = {"brain/behaviour.py"}  # named by the docs as future work (docs/ROADMAP.md, P5)


def test_every_repo_path_named_in_the_docs_exists() -> None:
    pages = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
    missing = []
    for page in pages:
        for path in PATH_PATTERN.findall(page.read_text()):
            if path not in RUNTIME_PATHS | PLANNED_PATHS and not (ROOT / path).exists():
                missing.append(f"{page.name}: {path}")
    assert not missing, f"the docs name paths that do not exist: {missing}"
