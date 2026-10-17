"""The docs stay small and true: only the four doc pages exist, and README or DEVELOPMENT
mentions every top-level folder."""

from __future__ import annotations

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
