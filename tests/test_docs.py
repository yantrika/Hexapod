"""The docs stay true: every code file is listed in docs/FILES.md, and every listed file exists."""

from __future__ import annotations

import re
from pathlib import Path

import config

ROOT = Path(config.PROJECT_ROOT)
FILES_DOC = ROOT / "docs" / "FILES.md"
CODE_FOLDERS = ("body", "brain", "voice", "scripts", "tests")
ROOT_FILES = ("config.py", "bridge.py", "commandline.py", "main.py")


def code_files() -> list[str]:
    found = [name for name in ROOT_FILES if (ROOT / name).is_file()]
    for folder in CODE_FOLDERS:
        found += sorted(f"{folder}/{p.name}" for p in (ROOT / folder).glob("*.py"))
        found += sorted(f"{folder}/{p.name}" for p in (ROOT / folder).glob("*.sh"))
    return found


def test_every_code_file_is_listed_in_the_files_doc() -> None:
    text = FILES_DOC.read_text()
    missing = [name for name in code_files() if f"`{name}`" not in text]
    assert not missing, f"add these to docs/FILES.md: {missing}"


def test_every_file_named_in_the_files_doc_exists() -> None:
    text = FILES_DOC.read_text()
    named = set(re.findall(r"`((?:body|brain|voice|scripts|tests|docs|assets)/[\w./-]+)`", text))
    gone = sorted(name for name in named if not (ROOT / name).exists() and not name.endswith("/"))
    assert not gone, f"docs/FILES.md names files that do not exist: {gone}"


def test_the_docs_folder_has_every_page_the_index_links_to() -> None:
    index = (ROOT / "docs" / "README.md").read_text()
    for target in re.findall(r"\]\(([\w./-]+\.md)\)", index):
        assert (ROOT / "docs" / target).resolve().is_file(), f"docs/README.md links to {target}"
