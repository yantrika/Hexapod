#!/usr/bin/env bash
# Copies the project to ~/hexa on the Pi (rsync over ssh). Only ~/hexa is touched.
#
# Usage: scripts/sync_to_pi.sh [--dry-run] [--assets] [--host NAME] [-h|--help]
#   --dry-run   show what would be copied, change nothing
#   --assets    ALSO copy the architecture-independent assets from this machine over the LAN
#               (the default Vosk model, the Piper voice .onnx/.json, assets/phrases, assets/urdf), so
#               the Pi does not download them. The x86 Piper binary is NEVER copied: the Pi gets
#               its own aarch64 build from scripts/fetch_models.sh.
#   --host NAME ssh host (default hexa-pi)
# Always sends wheelhouse/*.whl (scripts/build_wheelhouse.sh builds it here when missing).
# Never uses --delete, so nothing on the Pi (.venv, models, logs) is removed.
set -euo pipefail

host="hexa-pi"; dry=(); assets=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry=(--dry-run) ;;
    --assets) assets=1 ;;
    --host) host="${2:?--host needs a name}"; shift ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument '$1' (see --help)" >&2; exit 2 ;;
  esac
  shift
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rsync_opts=(-az --human-readable --itemize-changes "${dry[@]}")

# 1. the code: no .git, no virtualenv, no assets (models are handled below), no logs, no caches
rsync "${rsync_opts[@]}" \
  --exclude='.git/' --exclude='.venv/' --exclude='/assets/' --exclude='/logs/' \
  --exclude='/wheelhouse/' --exclude='/.tmp/' \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' --exclude='*.wav' --exclude='*.log' --exclude='*.jsonl' \
  "${root}/" "${host}:hexa/"

# 1b. the wheels PyPI has no wheel for (built here, never on the Pi): wheelhouse/*.whl
"${root}/scripts/build_wheelhouse.sh" >/dev/null
[[ ${#dry[@]} -gt 0 ]] || ssh "${host}" 'mkdir -p ~/hexa/wheelhouse ~/hexa/.tmp'  # .tmp: TMPDIR for pip
rsync "${rsync_opts[@]}" --include='*.whl' --exclude='*' "${root}/wheelhouse/" "${host}:hexa/wheelhouse/"

# 2. optional: architecture-independent assets, copied only when missing or changed
if [[ "${assets}" == 1 ]]; then
  [[ ${#dry[@]} -gt 0 ]] || ssh "${host}" 'mkdir -p ~/hexa/assets/piper ~/hexa/assets/vosk ~/hexa/assets/phrases ~/hexa/assets/urdf'
  # only the DEFAULT Vosk model (config.VOSK_MODEL_DEFAULT), not every model on this machine
  vosk_model="$(cd "${root}" && python3 -c "import config; print(config.VOSK_MODELS[config.VOSK_MODEL_DEFAULT])")"
  [[ -d "${root}/assets/vosk/${vosk_model}" ]] && \
    rsync "${rsync_opts[@]}" "${root}/assets/vosk/${vosk_model}" "${host}:hexa/assets/vosk/"
  [[ -d "${root}/assets/phrases" ]] && rsync "${rsync_opts[@]}" "${root}/assets/phrases/" "${host}:hexa/assets/phrases/"
  [[ -d "${root}/assets/urdf" ]] && rsync "${rsync_opts[@]}" "${root}/assets/urdf/" "${host}:hexa/assets/urdf/"
  # only the voice files: *.onnx and *.onnx.json, never the piper/ binary folder
  rsync "${rsync_opts[@]}" --include='*.onnx' --include='*.onnx.json' --exclude='*' \
    "${root}/assets/piper/" "${host}:hexa/assets/piper/"
fi
echo "done${dry[*]:+ (dry run: nothing was changed)}"
