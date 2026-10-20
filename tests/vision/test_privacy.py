"""Privacy: no frame or face crop is written to disk or logs, ever."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

import config
from tests.vision_fakes import FACE, FakeCamera, FakeClock, FakeDetector, run_loop

ROOT = Path(config.PROJECT_ROOT)
FORBIDDEN = re.compile(r"imwrite|imencode|VideoWriter|\.tofile\(|np\.save|numpy\.save|pickle|"
                       r"savez|\.write_bytes\(|tempfile|mkstemp|open\([^)]*['\"][wax]b?\+?['\"]")
CHECKED = [*sorted((ROOT / "vision").glob("*.py")), ROOT / "scripts" / "vision_check.py",
           ROOT / "scripts" / "vision_bench.py"]


@pytest.mark.parametrize("path", CHECKED, ids=lambda p: p.name)
def test_the_vision_code_has_no_way_to_save_an_image(path: Path) -> None:
    code = "\n".join(line.split("#")[0] for line in path.read_text().splitlines())
    assert not FORBIDDEN.search(code), f"{path.name} contains a file-writing call"


def test_running_the_loop_writes_no_file_and_logs_no_pixels(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.chdir(tmp_path)
    clock = FakeClock()
    camera = FakeCamera(clock, stop_at=6.0, open_failures=1)
    with caplog.at_level(logging.DEBUG):
        run_loop(camera, clock, FakeDetector(lambda i: [FACE]))
    assert list(tmp_path.rglob("*")) == []
    assert "array(" not in caplog.text and "uint8" not in caplog.text
