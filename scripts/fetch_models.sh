#!/usr/bin/env bash
# Downloads the models hexa needs into assets/ (idempotent: whatever already exists is skipped
# and reported). Models stay gitignored.
#
# Usage: scripts/fetch_models.sh [-h|--help]     no argument = fetch everything known so far
#   fetches: the Piper binary and the en_US-amy-low voice into assets/piper/, then renders the
#   fixed phrases and fillers to assets/phrases/ (scripts/prerender_phrases.py; skips existing)
#   and the small Vosk models (US English and Indian English) into assets/vosk/
set -euo pipefail

case "${1:-}" in
  "") ;;
  -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument '$1' (no argument fetches everything; see --help)" >&2; exit 2 ;;
esac

PIPER_RELEASE="2023.11.14-2"
PIPER_ARCHIVE="piper_linux_x86_64.tar.gz"
VOICE_PATH="en/en_US/amy/low/en_US-amy-low"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${VOICE_PATH}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
piper_dir="${root}/assets/piper"
voice_file="$(basename "${VOICE_PATH}")"

case "$(uname -m)" in
  x86_64) ;;
  aarch64) PIPER_ARCHIVE="piper_linux_aarch64.tar.gz" ;;
  *) echo "unsupported machine $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "${piper_dir}"

if [[ -x "${piper_dir}/piper/piper" ]]; then
  echo "skip: piper binary already present"
else
  echo "piper binary: downloading ${PIPER_ARCHIVE} (${PIPER_RELEASE})"
  curl -fsSL --retry 3 -o "${piper_dir}/${PIPER_ARCHIVE}" \
    "https://github.com/rhasspy/piper/releases/download/${PIPER_RELEASE}/${PIPER_ARCHIVE}"
  tar -xzf "${piper_dir}/${PIPER_ARCHIVE}" -C "${piper_dir}"
  rm -f "${piper_dir}/${PIPER_ARCHIVE}"
fi

for suffix in .onnx .onnx.json; do
  if [[ -s "${piper_dir}/${voice_file}${suffix}" ]]; then
    echo "skip: voice ${voice_file}${suffix} already present"
  else
    echo "voice: downloading ${voice_file}${suffix}"
    curl -fsSL --retry 3 -o "${piper_dir}/${voice_file}${suffix}" "${VOICE_BASE}${suffix}?download=true"
  fi
done
vosk_dir="${root}/assets/vosk"
mkdir -p "${vosk_dir}"
for model in vosk-model-small-en-us-0.15 vosk-model-small-en-in-0.4; do
  if [[ -d "${vosk_dir}/${model}" ]]; then
    echo "skip: ${model} already present"
  else
    echo "vosk: downloading ${model}"
    curl -fsSL --retry 3 -o "${vosk_dir}/${model}.zip" "https://alphacephei.com/vosk/models/${model}.zip"
    unzip -q -o "${vosk_dir}/${model}.zip" -d "${vosk_dir}"
    rm -f "${vosk_dir}/${model}.zip"
  fi
done

if [[ -x "${root}/.venv/bin/python" ]]; then python="${root}/.venv/bin/python"; else python=python3; fi
"${python}" "${root}/scripts/prerender_phrases.py"
echo "done"
