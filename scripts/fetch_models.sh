#!/usr/bin/env bash
# Downloads the models hexa needs into assets/ (idempotent: whatever already exists is skipped
# and reported). Models stay gitignored. WHICH models comes from config.py, so to replace one
# you change a name there (VOSK_MODELS / VOSK_MODEL_DEFAULT / PIPER_VOICE) and run this again.
#
# Usage: scripts/fetch_models.sh [--all-models] [-h|--help]
#   no argument   the Piper binary, the PIPER_VOICE voice, the DEFAULT Vosk model, then the
#                 fixed phrases (scripts/prerender_phrases.py; skips existing)
#   --all-models  also every other Vosk model listed in config.VOSK_MODELS (Indian English)
set -euo pipefail

all_models=0
case "${1:-}" in
  "") ;;
  --all-models) all_models=1 ;;
  -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument '$1' (see --help)" >&2; exit 2 ;;
esac

PIPER_RELEASE="2023.11.14-2"
PIPER_ARCHIVE="piper_linux_x86_64.tar.gz"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_value() { (cd "${root}" && python3 -c "import config; $1"); }
VOICE_PATH="$(config_value "print(config.PIPER_VOICE)")"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${VOICE_PATH}"
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
if [[ "${all_models}" == 1 ]]; then
  models="$(config_value "print(' '.join(config.VOSK_MODELS.values()))")"
else
  models="$(config_value "print(config.VOSK_MODELS[config.VOSK_MODEL_DEFAULT])")"
fi
for model in ${models}; do
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
