#!/usr/bin/env bash
# Run TI ADC Python simulations: ideal + non-ideal mismatch suites (Phase 1.1).
#
# Layout matches adc-model: outputs/<engine>/<case>/ (default engine: python).
# Future: spectre, ngspice, veriloga under the same case names.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${OUTPUT_ROOT:-${ROOT_DIR}/outputs}"
SKIP_MISSING=0
EXTRA_ARGS=()

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] [-- EXTRA_ARGS...]

Default ADC: 10-bit, fs = 1 GHz (override with -- --bits N --fs HZ).

Default suite (10 runs = 5 cases × static + dynamic) under
  \${OUTPUT_ROOT}/\${ENGINE}/<case>/

Cases:
  ideal       no mismatch, quantizer-limited (--ideal)
  gain        gain mismatch + default noise
  offset      offset mismatch + default noise
  clock       timing-skew mismatch + default noise
  combined    gain + offset + clock + default noise

Examples:
  outputs/python/ideal/
  outputs/python/gain/
  outputs/python/offset/
  outputs/python/clock/
  outputs/python/combined/

Options:
  --output-root DIR   Base directory (default: ${ROOT_DIR}/outputs).
  --skip-missing      Skip spectre/ngspice when the binary is not on PATH.
  --preset NAME       One case only: ideal, gain, offset, clock, combined, nonideal.
  --ideal-only        Run only ideal/ (quantizer-limited).
  --time-varying      Sinusoidal mismatch drift on every case.
  -h, --help          Show this message.

Environment:
  OUTPUT_ROOT           Same as --output-root.
  VENV_DIR              Python venv (default: ${ROOT_DIR}/.venv).

Runs python/, then spectre/ and ngspice/ under \${OUTPUT_ROOT} when available.

Examples:
  $(basename "$0")
  $(basename "$0") --ideal-only
  $(basename "$0") --engine python --output-root outputs/noise
  $(basename "$0") --preset clock

Requires: scripts/install_python.sh
EOF
}

PRESET="suite"
TIME_VARYING=0
IDEAL_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --ideal-only)
            IDEAL_ONLY=1
            PRESET="ideal"
            shift
            ;;
        --output-root)
            OUTPUT_ROOT="$2"
            shift 2
            ;;
        --skip-missing)
            SKIP_MISSING=1
            shift
            ;;
        --preset)
            PRESET="$2"
            shift 2
            ;;
        --time-varying)
            TIME_VARYING=1
            shift
            ;;
        --)
            shift
            EXTRA_ARGS+=("$@")
            break
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ "${TIME_VARYING}" -eq 1 ]]; then
    EXTRA_ARGS+=(--mismatch-mode time_varying)
fi

cd "${ROOT_DIR}"

require_ngspice() {
    if command -v ngspice >/dev/null 2>&1; then
        return 0
    fi
    if [[ "${SKIP_MISSING}" -eq 1 ]]; then
        echo "warning: ngspice not found; skipping ngspice engine" >&2
        return 1
    fi
    echo "error: ngspice not on PATH (use --skip-missing to omit)" >&2
    exit 1
}

require_spectre() {
    if command -v spectre >/dev/null 2>&1; then
        return 0
    fi
    if [[ "${SKIP_MISSING}" -eq 1 ]]; then
        echo "warning: spectre not found; skipping spectre engine" >&2
        return 1
    fi
    echo "error: spectre not on PATH (use --skip-missing to omit)" >&2
    exit 1
}

VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"
if [[ -x "${VENV_DIR}/bin/python" ]]; then
    PYTHON="${VENV_DIR}/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    echo "error: python3 not found (run scripts/install_python.sh)" >&2
    exit 1
fi

if ! "${PYTHON}" -c "import ti_adc" 2>/dev/null; then
    export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
    if ! "${PYTHON}" -c "import ti_adc" 2>/dev/null; then
        echo "error: ti_adc not importable; run scripts/install_python.sh" >&2
        exit 1
    fi
fi

RUN_STATIC="${ROOT_DIR}/scripts/run_ti_static.py"
RUN_DYNAMIC="${ROOT_DIR}/scripts/run_ti_dynamic.py"

suite_cases() {
    if [[ "${PRESET}" == "suite" ]]; then
        if [[ "${IDEAL_ONLY}" -eq 1 ]]; then
            echo "ideal ideal --ideal"
            return
        fi
        echo "ideal ideal --ideal"
        echo "gain gain"
        echo "offset offset"
        echo "clock clock"
        echo "combined combined"
        return
    fi
    if [[ "${PRESET}" == "all" ]]; then
        echo "ideal ideal --ideal"
        echo "gain gain"
        echo "offset offset"
        echo "clock clock"
        echo "combined combined"
        return
    fi
    if [[ "${PRESET}" == "nonideal" ]]; then
        echo "gain gain"
        echo "offset offset"
        echo "clock clock"
        echo "combined combined"
        return
    fi
    if [[ "${PRESET}" == "ideal" ]]; then
        echo "ideal ideal --ideal"
        return
    fi
    echo "${PRESET} ${PRESET}"
}

run_case() {
    local engine="$1"
    local folder="$2"
    local impairment="$3"
    shift 3
    local case_args=("$@")
    local out_dir="${OUTPUT_ROOT}/${engine}/${folder}"
    mkdir -p "${out_dir}"

    echo ""
    echo "========== ${engine}/${folder}: TI static INL/DNL =========="
    "${PYTHON}" "${RUN_STATIC}" \
        --simulator "${engine}" \
        --impairment "${impairment}" \
        --output-dir "${out_dir}" \
        "${EXTRA_ARGS[@]}" \
        "${case_args[@]}"

    echo ""
    echo "========== ${engine}/${folder}: TI dynamic spectrum =========="
    "${PYTHON}" "${RUN_DYNAMIC}" \
        --simulator "${engine}" \
        --impairment "${impairment}" \
        --output-dir "${out_dir}" \
        "${EXTRA_ARGS[@]}" \
        "${case_args[@]}"
}

run_engine() {
    local engine="$1"
    echo ""
    echo "############################################"
    echo "# Engine: ${engine}"
    echo "############################################"
    while read -r folder impairment rest; do
        # shellcheck disable=SC2206
        case_args=(${rest})
        run_case "${engine}" "${folder}" "${impairment}" "${case_args[@]}"
    done < <(suite_cases)
}

echo "Repo root    : ${ROOT_DIR}"
echo "Output root  : ${OUTPUT_ROOT}"
echo "Preset mode  : ${PRESET}"
echo "Extra args   : ${EXTRA_ARGS[*]-<none>}"

run_engine python
if require_spectre; then
    run_engine spectre
fi
if require_ngspice; then
    run_engine ngspice
fi

echo ""
echo "TI simulations finished."
echo "  python -> ${OUTPUT_ROOT}/python/"
if command -v spectre >/dev/null 2>&1; then
    echo "  spectre -> ${OUTPUT_ROOT}/spectre/"
fi
if command -v ngspice >/dev/null 2>&1; then
    echo "  ngspice -> ${OUTPUT_ROOT}/ngspice/"
fi
case "${PRESET}" in
    suite|all)
        echo "  cases : ideal, gain, offset, clock, combined"
        ;;
    nonideal)
        echo "  cases : gain, offset, clock, combined"
        ;;
esac
