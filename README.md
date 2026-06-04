# ti-adc-model

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-green?logo=creativecommons&logoColor=white)](https://creativecommons.org/licenses/by/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776ab.svg)](https://www.python.org/downloads/)

**ti-adc-model** is a time-interleaved (TI) ADC **behavioral model** with Python, Cadence Spectre, and (planned) ngspice testbenches. It builds on the single-channel [`adc-model`](https://github.com/SJTU-YONGFU-RESEARCH-GRP/adc-model) package (installed from Git).

**Repository:** [SJTU-YONGFU-RESEARCH-GRP/ti-adc-model](https://github.com/SJTU-YONGFU-RESEARCH-GRP/ti-adc-model)

## Features

| Area | Description |
| --- | --- |
| **TI array model** | `M` interleaved channels with per-channel gain, offset, and timing skew |
| **Mismatch presets** | Ideal, gain, offset, clock skew, and combined impairment suites |
| **Static / dynamic TB** | INL/DNL ramp and coherent-sine FFT (SNDR, SFDR, spurs at `k·fs/M`) |
| **Spectre** | `veriloga/ti_adc_array.va` + `testbench/spectre/` netlists |
| **Engine parity** | `scripts/compare_ti_engines.py` — Python vs Spectre |

## Requirements

- Python 3.10+
- NumPy, Matplotlib (installed with the package)
- Optional: Cadence Spectre with Verilog-A (AHDL) for `--spectre` runs

## Installation

`adc-model` is installed from `git@github.com:SJTU-YONGFU-RESEARCH-GRP/adc-model.git` (editable checkout under `.venv/src/adc-model`). No git submodule is required.

```bash
./scripts/install_python.sh
source .venv/bin/activate
```

Override the Verilog-A tree with `ADC_MODEL_ROOT` if needed.

## Quick start

```bash
# Ideal dynamic spectrum (Python)
python scripts/run_ti_dynamic.py --ideal --impairment ideal \
  --output-dir outputs/python/ideal

# Full Python suite (ideal + gain + offset + clock + combined)
./scripts/run_all_ti_simulations.sh

# Spectre (when `spectre` is on PATH)
python scripts/run_ti_dynamic.py --spectre --ideal --impairment ideal \
  --output-dir outputs/spectre/ideal

# Compare Python vs Spectre on one case
python scripts/compare_ti_engines.py --impairment combined
```

## Project layout

```text
ti-adc-model/
├── src/ti_adc/               # TI array model and analysis
├── veriloga/                 # ti_channel_adc.va, ti_adc_array.va
├── testbench/spectre/        # TI Spectre testbenches
├── scripts/                  # run_ti_*.py, compare_ti_engines.py
├── tests/
└── PLAN.md                   # Model roadmap (Phases 0–1)
```

## Related work

| Repo | Role |
| --- | --- |
| [adc-model](https://github.com/SJTU-YONGFU-RESEARCH-GRP/adc-model) | Single-channel `configurable_adc` and engines |

## Development

```bash
source .venv/bin/activate
pytest
ruff check src tests scripts
```

See [PLAN.md](PLAN.md) for phase milestones and parity criteria.

## License

CC BY 4.0 — see [LICENSE](LICENSE) when present, or the adc-model package license.
