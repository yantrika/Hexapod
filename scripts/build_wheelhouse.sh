#!/usr/bin/env bash
# Builds the wheels the Pi cannot get from PyPI as wheels, ON THIS MACHINE (never on the Pi):
# srt 3.5.3 (a vosk dependency published only as a source archive). Pure Python, so the
# py3-none-any wheel works on aarch64. wheelhouse/ is gitignored; this regenerates it when missing.
# Usage: scripts/build_wheelhouse.sh      (prints the sha256 of every wheel)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "${root}/.venv/bin/python" ]]; then python="${root}/.venv/bin/python"; else python=python3; fi
mkdir -p "${root}/wheelhouse"
if ls "${root}"/wheelhouse/srt-3.5.3-py3-none-any.whl >/dev/null 2>&1; then
  echo "skip: srt wheel already in wheelhouse/"
else
  "${python}" -m pip wheel srt==3.5.3 --no-deps -w "${root}/wheelhouse"
fi
[[ -f "${root}/wheelhouse/srt-3.5.3-py3-none-any.whl" ]] || { echo "no py3-none-any srt wheel" >&2; exit 1; }
(cd "${root}/wheelhouse" && sha256sum ./*.whl)
