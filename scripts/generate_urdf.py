#!/usr/bin/env python3
"""Write assets/urdf/hexapod.urdf from config.py (``--check`` only verifies it)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body.urdf import build_urdf  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = parser.parse_args()

    text = build_urdf()
    if args.check:
        current = config.URDF_PATH.read_text() if config.URDF_PATH.exists() else None
        if current != text:
            print(f"{config.URDF_PATH} is stale; run scripts/generate_urdf.py")
            return 1
        print(f"{config.URDF_PATH} is up to date")
        return 0
    config.URDF_DIR.mkdir(parents=True, exist_ok=True)
    config.URDF_PATH.write_text(text)
    print(f"wrote {config.URDF_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
