# ti-adc-model

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-green?logo=creativecommons&logoColor=white)](https://creativecommons.org/licenses/by/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776ab.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue?logo=semver&logoColor=white)](https://github.com/SJTU-YONGFU-RESEARCH-GRP/ti-adc-model)

**ti-adc-model** is a time-interleaved (TI) ADC behavioral model with Python, Cadence Spectre, and ngspice testbenches for static INL/DNL and dynamic FFT characterization of the muxed stream at rate `fs`.

**Repository:** [SJTU-YONGFU-RESEARCH-GRP/ti-adc-model](https://github.com/SJTU-YONGFU-RESEARCH-GRP/ti-adc-model)

- **License:** CC BY 4.0 (see [LICENSE](LICENSE) when present)
- **Entry points:** `scripts/run_all_ti_simulations.sh`, `scripts/compare_ti_engines.py`, `scripts/run_ti_static.py`, `scripts/run_ti_dynamic.py`
- **Simulators:** Python behavioral model (default), Cadence Spectre, ngspice
- **Dependency:** single-channel [`adc-model`](https://github.com/SJTU-YONGFU-RESEARCH-GRP/adc-model) (Git pin in `pyproject.toml`)

## Table of contents

- [Model reference](#model-reference)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
  - [Run all TI simulations](#run-all-ti-simulations)
  - [Compare TI engines](#compare-ti-engines)
  - [Static INL/DNL only](#static-inldnl-only)
  - [Dynamic spectrum only](#dynamic-spectrum-only)
- [CLI reference](#cli-reference)
- [Python API](#python-api)
- [Simulation engines](#simulation-engines)
  - [Cross-engine agreement and discrepancies](#cross-engine-agreement-and-discrepancies)
- [Project layout](#project-layout)
- [Development](#development)
- [License](#license)

## Model reference

For TI mismatch presets (gain, offset, timing skew), mux timing, spur locations at `k·fs/M`, phase milestones, and parity tolerances, see **[PLAN.md](PLAN.md)**.

Per-channel ADC behavior (noise, jitter, nonlinearity, DNL spread) follows **adc-model** — see [adc-model/MODEL.md](https://github.com/SJTU-YONGFU-RESEARCH-GRP/adc-model/blob/main/MODEL.md) and `veriloga/configurable_adc.va`.

## Features

| Area | Description |
| --- | --- |
| **TI array model** | `M` interleaved channels with per-channel gain, offset, and timing skew |
| **Mismatch presets** | Ideal, gain, offset, clock skew, and combined impairment suites |
| **Static testbench** | Slow ramp on muxed output `y[n]`, histogram INL/DNL, SVG plots |
| **Dynamic testbench** | Coherent sine at `fs`, FFT metrics (SNDR, SFDR, THD, ENOB), TI spur table at `k·fs/M` |
| **Multi-engine** | Same testbenches in Python, Cadence Spectre, or ngspice |
| **Verilog-A** | `veriloga/ti_channel_adc.va`, `veriloga/ti_adc_array.va` |
| **Parity tooling** | `scripts/compare_ti_engines.py` with `--check-parity` for CI |

## Requirements

- **Python** 3.10 or newer
- **Runtime:** NumPy, Matplotlib, **adc-model** (installed automatically from Git)
- **Optional — Spectre:** Cadence Spectre with Verilog-A (AHDL) support
- **Optional — ngspice:** [ngspice](https://ngspice.sourceforge.io/) 36+ (tested with ngspice 42)

## Installation

From the repository root:

```bash
./scripts/install_python.sh
source .venv/bin/activate
```

Or install manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`adc-model` is pulled from `git@github.com:SJTU-YONGFU-RESEARCH-GRP/adc-model.git` (revision pinned in `pyproject.toml`). Override the Verilog-A tree with `ADC_MODEL_ROOT` if needed.

## Quick start

All commands assume the virtual environment is active and you are in the repository root.

### Run all TI simulations

`scripts/run_all_ti_simulations.sh` runs every engine × impairment case × testbench (30 runs when all simulators are available):

| Engine | Static INL/DNL | Dynamic FFT |
| --- | --- | --- |
| Python | `run_ti_static.py` | `run_ti_dynamic.py` |
| ngspice | same | same |
| Spectre | same | same |

Default ADC: **10-bit**, **fs = 1 GHz**, **M = 4** channels. Default output layout:

```text
outputs/
├── python/
│   ├── ideal/
│   │   ├── ti_static_waveform.csv, ti_inl_dnl.svg
│   │   ├── ti_dynamic_waveform.csv, ti_spectrum.svg
│   │   └── logs/          # optional per-run logs
│   ├── gain/
│   ├── offset/
│   ├── clock/
│   └── combined/
├── ngspice/               # same case names + logs/netlists/*.cir
└── spectre/               # same + logs/*.nutascii, logs/*.ahdlSimDB/
```

**Cases:**

| Case | Description |
| --- | --- |
| `ideal` | No mismatch, quantizer-limited (`--ideal`) |
| `gain` | Gain mismatch + default channel noise |
| `offset` | Offset mismatch + default noise |
| `clock` | Timing-skew mismatch + default noise |
| `combined` | Gain + offset + clock + default noise |

```bash
./scripts/run_all_ti_simulations.sh
```

Ideal-only batch:

```bash
./scripts/run_all_ti_simulations.sh --ideal-only
```

One impairment preset:

```bash
./scripts/run_all_ti_simulations.sh --preset clock
```

Skip external simulators when `ngspice` or `spectre` is not on `PATH`:

```bash
./scripts/run_all_ti_simulations.sh --skip-missing
```

Forward shared CLI options to every run (must follow `--`):

```bash
./scripts/run_all_ti_simulations.sh -- --bits 12 --fs 2e9 --channels 4
```

| Option | Description |
| --- | --- |
| `--output-root DIR` | Base directory for `python/`, `ngspice/`, `spectre/` (default: `outputs/`) |
| `--preset NAME` | One case: `ideal`, `gain`, `offset`, `clock`, `combined` |
| `--ideal-only` | Run only the `ideal/` case |
| `--skip-missing` | Run Python only if ngspice/Spectre are absent |
| `--time-varying` | Sinusoidal mismatch drift on every case |
| `-h`, `--help` | Show usage |

Environment: `OUTPUT_ROOT` (same as `--output-root`), `VENV_DIR` (default: `.venv`).

### Compare TI engines

After `run_all_ti_simulations.sh`, use `scripts/compare_ti_engines.py` for a side-by-side table of dynamic and static metrics per engine under `outputs/<engine>/<case>/`.

```bash
# Ideal case (default folder: outputs/ideal → use --case ideal)
python scripts/compare_ti_engines.py --case ideal \
  --engines python spectre ngspice

# Combined impairment
python scripts/compare_ti_engines.py --case combined \
  --engines python spectre ngspice

# CI parity gate (SNDR + optional static)
for case in ideal gain offset clock combined; do
  python scripts/compare_ti_engines.py --case "$case" \
    --engines python spectre ngspice --check-parity --check-static
done
```

| Option | Description |
| --- | --- |
| `--output-root DIR` | Parent with `<engine>/<case>/` subfolders (default: `outputs`) |
| `--case NAME` | Impairment folder: `ideal`, `gain`, `offset`, `clock`, `combined` |
| `--engines` | Space-separated: `python`, `spectre`, `ngspice` |
| `--check-parity` | Exit 1 if SNDR (and THD with `--check-thd`) exceeds tolerance vs Python |
| `--check-static` | Also compare max \|DNL\| and \|INL\| when CSVs exist |
| `--sndr-tolerance-db` | Override SNDR tolerance (ideal capped at 0.5 dB) |

Example (ideal dynamic, representative batch):

```text
Case: ideal  Fin: 121.7041 MHz  fs: 1 GHz
Engine      SNDR (dB)   THD (dB)     ENOB   Max|DNL|   Max|INL|
--------------------------------------------------------------
python          61.53      86.56     9.93     0.2479     0.6227
spectre         61.53      85.43     9.93     0.2476     0.6222
ngspice         61.53      86.56     9.93     0.2479     0.6225
PARITY PASS: all engines within 0.50 dB SNDR of python
```

See [Cross-engine agreement and discrepancies](#cross-engine-agreement-and-discrepancies) for tolerances and interpretation.

### Static INL/DNL only

```bash
python scripts/run_ti_static.py --impairment combined \
  --output-dir outputs/python/combined
```

Analyze an existing waveform CSV (skip simulation):

```bash
python scripts/run_ti_static.py --input outputs/python/ideal/ti_static_waveform.csv \
  --output-dir outputs/python/ideal
```

### Dynamic spectrum only

```bash
python scripts/run_ti_dynamic.py --impairment ideal --ideal \
  --output-dir outputs/python/ideal
```

Spectre or ngspice:

```bash
python scripts/run_ti_dynamic.py --spectre --impairment ideal --ideal \
  --output-dir outputs/spectre/ideal

python scripts/run_ti_dynamic.py --ngspice --impairment combined \
  --output-dir outputs/ngspice/combined
```

Override tone or capture length:

```bash
python scripts/run_ti_dynamic.py --fin 121704102 --num-samples 8192 \
  --output-dir outputs/python/dynamic
```

## CLI reference

Shared TI ADC and noise options are on `run_ti_static.py` and `run_ti_dynamic.py` (via `ti_adc.cli_helpers`).

| Option | Default | Description |
| --- | --- | --- |
| `--channels` | `4` | Number of interleaved channels `M` |
| `--bits` | `10` | ADC resolution |
| `--vrefp`, `--vrefn` | `1.0`, `0.0` | Full-scale reference (V) |
| `--fs` | `1e9` | Muxed output sample rate (Hz) |
| `--impairment` | `combined` | `ideal`, `gain`, `offset`, `clock`, `combined` |
| `--ideal` | off | Disable all noise and nonlinearity |
| `--gain`, `--offset-v` | adc-model defaults | Passed through to per-channel ADC |
| `--sigma-thermal-v`, `--jitter-rms-s`, etc. | adc-model defaults | Per-channel noise (see adc-model README) |
| `--simulator` | `python` | `python`, `spectre`, or `ngspice` |
| `--spectre` / `--ngspice` | — | Aliases for `--simulator` |
| `--enable-cal` | off | Apply per-channel gain/offset calibration |
| `--mismatch-mode` | `static` | `static` or `time_varying` |
| `--golden-export` | off | Spectre only: Tier-1 vector parity (re-quantize on Python mux grid) |

Script-specific options:

| Script | Option | Default | Description |
| --- | --- | --- | --- |
| `run_ti_static.py` | `--samples-per-code` | `4` | Ramp hits per output code |
| `run_ti_static.py` | `--input` | — | Existing `ti_static_waveform.csv` |
| `run_ti_dynamic.py` | `--num-samples` | `8192` | FFT capture length |
| `run_ti_dynamic.py` | `--coherent-bin` | `997` | Coherent FFT bin index |
| `run_ti_dynamic.py` | `--fin` | coherent bin | Input tone frequency (Hz) |
| `run_ti_dynamic.py` | `--input` | — | Existing `ti_dynamic_waveform.csv` |

Default coherent tone at `fs = 1 GHz`: `Fin ≈ 997 × fs / 8192 ≈ 121.704 MHz`. Channel sample rate is `fs/M = 250 MHz`.

## Python API

```python
from ti_adc import (
    TiAdcConfig,
    preset_ideal,
    preset_combined_impaired,
    simulate_ti_static,
    simulate_ti_dynamic,
    compute_ti_inl_dnl,
    compute_ti_dynamic_metrics,
)
from adc_model.config import ideal_noise, default_noise

cfg = preset_ideal(bits=10, channels=4, fs_hz=1e9)
noise = ideal_noise()

static = simulate_ti_static(cfg, samples_per_code=4, noise=noise)
linearity = compute_ti_inl_dnl(static["vin"], static["code"].astype(int), cfg)

dynamic = simulate_ti_dynamic(
    cfg, num_samples=8192, fin_hz=121_704.102, noise=noise
)
metrics = compute_ti_dynamic_metrics(
    dynamic["code"].astype(int), cfg, fin_hz=121_704.102
)
```

`simulate_ti_static` / `simulate_ti_dynamic` return dicts with muxed-stream keys `time`, `vin`, `code`, and per-channel fields where applicable. Public exports are listed in `ti_adc.__all__`.

## Simulation engines

| Engine | Flag | Notes |
| --- | --- | --- |
| **Python** | `--simulator python` (default) | Golden reference: mux, skew, mismatch presets, analysis |
| **Spectre** | `--spectre` | Four phase-shifted `ti_channel_adc` instances; E2E mux from `v_code0..3` (default) |
| **ngspice** | `--ngspice` | Four channel chains + PULSE clocks; Python mux post-process |

### Cross-engine agreement and discrepancies

All engines share the same CLI impairment presets, mux rate `fs`, and the same analysis (`compute_ti_inl_dnl`, `compute_ti_dynamic_metrics`, TI spur table). Differences come from **how** each engine builds the muxed waveform CSV, not from different metric formulas.

**Golden rule:** Python defines mismatch math and analysis; Spectre and ngspice are independent simulations checked with `compare_ti_engines.py`.

#### Full-suite batch (representative)

Running `./scripts/run_all_ti_simulations.sh` with ngspice and Spectre on `PATH` yields:

| Case | Metric | Python | ngspice | Spectre |
| --- | --- | --- | --- | --- |
| **ideal** | SNDR | 61.53 dB | 61.53 dB | 61.53 dB |
| **ideal** | max \|DNL\| | 0.248 LSB | 0.248 LSB | 0.248 LSB |
| **gain** | SNDR | 37.44 dB | 37.44 dB | 37.23 dB |
| **gain** | max \|DNL\| | 6.18 LSB | 6.18 LSB | 6.68 LSB |
| **offset** | SNDR | 33.06 dB | 33.06 dB | 32.95 dB |
| **clock** | SNDR | 25.45 dB | 25.44 dB | 24.87 dB |
| **combined** | SNDR | 25.45 dB | 25.45 dB | 25.10 dB |

`compare_ti_engines.py --check-parity` enforces (vs Python):

| Check | Ideal | Impaired (`gain`, `offset`, `clock`, `combined`) |
| --- | --- | --- |
| SNDR | ≤ **0.5 dB** | ≤ **2.0 dB** |
| THD (with `--check-thd`) | ≤ 1.5 dB | ≤ 3.0 dB |
| max \|DNL\| (with `--check-static`) | ≤ 0.05 LSB | ≤ 1.5 LSB |
| max \|INL\| | ≤ 0.15 LSB | ≤ 1.0 LSB |

#### Python and ngspice — near-identical

For the default TI suite:

- **Ideal:** dynamic SNDR/THD/ENOB match Python to numerical noise; static DNL/INL match within 0.001 LSB.
- **Impaired (gain, offset, combined):** dynamic SNDR matches Python; static max \|DNL\| / \|INL\| often **bit-identical** on gain/offset/combined because ngspice finishes `jitter → gain → offset → nonlinearity → thermal → DNL → quantize` on captured `vin` at mux instants (same order as Python).

Treat **Python as the golden reference** for CI; use ngspice to validate exported netlists and PULSE clock wiring.

**Ideal quantizer-limited path:** ngspice muxes independent `v_nl` / `v_code` from SPICE at Python mux grid instants (no Python re-quantize on `vin`).

#### Spectre — expected gaps

Spectre runs **end-to-end** by default: mux simulated `v_code0..3` at `n/fs + timing_skew_k` (see [testbench/spectre/README.md](testbench/spectre/README.md)). Optional `--golden-export` re-quantizes on the Python grid for Tier-1 vector parity only.

| Observation | Typical cause |
| --- | --- |
| Slightly lower SNDR on **clock** / **combined** (~0.3–0.6 dB) | AHDL event scheduling vs Python mux grid |
| Higher static max \|DNL\| on **gain** / **offset** (~0.5 LSB) | Independent E2E codes vs Python histogram path |
| Lower static INL on **clock** (~0.43 vs ~0.98 LSB) | Different ramp crossing statistics on exported CSV |
| THD ~1 dB below Python on **ideal** | FFT / spur energy accounting |

These are within impaired parity tolerances; do not expect bit-exact INL curves next to Python on every case.

#### TI mismatch spurs

With default `Fin ≈ 121.7 MHz` and `fs/M = 250 MHz`, impaired dynamic runs show energy at **`k·fs/M`** (e.g. 250 MHz for `k = 1`). Ideal runs may show a small residual spur near `fs/M` from finite capture length.

#### Which engine when

| Goal | Engine |
| --- | --- |
| Regression, API, fast iteration | Python |
| Open-source SPICE netlist check | ngspice |
| Cadence VA / Spectre sign-off | Spectre |
| Strict metric parity | Python vs ngspice; `compare_ti_engines.py --check-parity` |

Further engine notes:

- [testbench/spectre/README.md](testbench/spectre/README.md) — Cadence Spectre and Verilog-A
- [PLAN.md](PLAN.md) — roadmap, mismatch model, Phase 1 exit criteria

## Project layout

```text
ti-adc-model/
├── LICENSE
├── README.md
├── PLAN.md
├── pyproject.toml
├── scripts/
│   ├── install_python.sh
│   ├── run_all_ti_simulations.sh   # All engines × cases × static + dynamic
│   ├── compare_ti_engines.py       # Multi-engine metrics + parity gate
│   ├── run_ti_static.py            # TI INL/DNL testbench
│   └── run_ti_dynamic.py           # TI FFT / spur testbench
├── src/ti_adc/
│   ├── config.py                   # TiAdcConfig, presets
│   ├── ti_model.py                 # Interleaved mux simulation
│   ├── mismatch.py                 # Static / time-varying profiles
│   ├── analysis.py                 # INL/DNL, FFT, spur table
│   ├── ti_io.py                    # Spectre / ngspice waveform mux
│   ├── spectre_engine.py
│   └── ngspice_engine.py
├── veriloga/
│   ├── ti_channel_adc.va
│   └── ti_adc_array.va
├── testbench/
│   ├── spectre/                    # TI Spectre netlists (.scs)
│   └── ngspice/                    # TI ngspice netlists (.cir)
└── tests/                          # pytest suite
```

Simulation outputs (CSV waveforms, SVG figures, logs, rendered netlists) are written under `outputs/` and are gitignored. Spectre AHDL caches stay under `<output-dir>/logs/*.ahdlSimDB/`.

## Development

```bash
source .venv/bin/activate
pytest
ruff check src tests scripts
```

Install without dev dependencies:

```bash
./scripts/install_python.sh --no-dev
```

See [PLAN.md](PLAN.md) for phase milestones and parity criteria.

## License

Licensed under [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). See [LICENSE](LICENSE) when present.

Third-party tools (Cadence Spectre, ngspice) are subject to their own license terms.
