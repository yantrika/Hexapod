#!/usr/bin/env python3
"""Download the two face models into assets/vision/ (gitignored). Standard library only.

    python scripts/fetch_vision_models.py

Idempotent: a file that is already there with the right SHA-256 is skipped and reported; a file
with the wrong one is downloaded again. A download is checked before it is moved into place, so a
broken download never leaves a half file behind.

The names, sizes and hashes are the Git LFS pointers of the OpenCV Zoo repository
(https://github.com/opencv/opencv_zoo: models/face_detection_yunet and
models/face_recognition_sface), read from its API, not guessed.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
# file name -> (URL, size in bytes, sha256); the file names must match config.VISION_*_FILE
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx", 232_589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": (
        f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx", 38_696_353,
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, url: str, size: int, sha256: str, target_dir: Path) -> bool:
    target = target_dir / name
    if target.is_file():
        if sha256_of(target) == sha256:
            print(f"ok       {name} (already there, checksum matches)")
            return True
        print(f"replace  {name}: checksum differs")
    print(f"download {name} ({size / 1e6:.1f} MB) from {url}")
    part = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
    except OSError as error:
        print(f"FAILED   {name}: {error}", file=sys.stderr)
        part.unlink(missing_ok=True)
        return False
    if sha256_of(part) != sha256:
        print(f"FAILED   {name}: downloaded file has the wrong SHA-256 (got {sha256_of(part)}); "
              "the Zoo file may have changed: not saved", file=sys.stderr)
        part.unlink(missing_ok=True)
        return False
    part.replace(target)
    print(f"saved    {target}")
    return True


def main() -> int:
    target_dir = Path(config.VISION_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    expected = {config.VISION_YUNET_FILE.name, config.VISION_SFACE_FILE.name}
    if set(MODELS) != expected:
        print(f"config.py names {sorted(expected)} but this script fetches {sorted(MODELS)}",
              file=sys.stderr)
        return 2
    results = [fetch(name, *entry, target_dir) for name, entry in MODELS.items()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
