#!/usr/bin/env bash
# Install adc-model from Git and ti-adc-model package (editable).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
elif command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
elif command -v python3.10 >/dev/null 2>&1; then
  PY=python3.10
else
  PY=python3
fi
echo "Using interpreter: $PY"

VENV="${ROOT}/.venv"
if [[ -d "${VENV}" ]]; then
  pip_launcher="${VENV}/bin/pip"
  if [[ -f "${pip_launcher}" ]]; then
    pip_shebang="$(head -1 "${pip_launcher}")"
    if [[ "${pip_shebang}" != "#!${VENV}/"* ]]; then
      echo "Removing stale virtual environment (pip points outside ${VENV})"
      rm -rf "${VENV}"
    fi
  elif ! "${VENV}/bin/python" -c 'pass' >/dev/null 2>&1; then
    echo "Removing broken virtual environment at ${VENV}"
    rm -rf "${VENV}"
  fi
fi

"$PY" -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -m pip install -U pip
ADC_MODEL_GIT="git+ssh://git@github.com/SJTU-YONGFU-RESEARCH-GRP/adc-model.git@8e071dfaafeb2958b770c1d2369845bf9ba820b9"
python -m pip install -e "${ADC_MODEL_GIT}#egg=adc-model"
python -m pip install -e "${VENV}/src/adc-model[dev]"
python -m pip install -e ".[dev]" --no-deps
python -m pip install pytest ruff
echo "Installed adc-model and ti-adc-model into ${ROOT}/.venv"
