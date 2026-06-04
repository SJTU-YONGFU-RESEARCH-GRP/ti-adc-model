# TI ADC Spectre testbenches (Phase 1.2)

Cadence Spectre simulations for the **time-interleaved** array (`M = 4` by default).

## Architecture

- **`veriloga/ti_channel_adc.va`** — per-channel wrapper around adc-model `veriloga/configurable_adc.va`
- **`testbench/spectre/ti_* .scs`** — four phase-shifted clocks (`period = M/fs`, `delay = k/fs + timing_skew_k`)
- **Python mux** — `ti_adc.ti_io.prepare_ti_spectre_waveform()` builds the muxed `fs` stream for analysis

## Export tiers

| Tier | Flag | Source | Use |
|------|------|--------|-----|
| **End-to-end (default)** | *(none)* | Simulated `v_code0..3` from nutascii, muxed at `n/fs` + skew | Phase 1.4 validation vs Python reference |
| **Golden / vectors** | `--golden-export` | Resample Spectre `vin`, Python quantizer + noise on mux grid | Tier-1 shared vectors, tight Python parity |

End-to-end export picks the channel clock edge nearest to `n/fs + timing_skew_k` and reads `v_code` one dense sample after that edge (Spectre settling). It does **not** re-run the Python quantizer on `vin`.

## Run from repo root

```bash
source .venv/bin/activate

# One case (ideal, quantizer-limited)
python scripts/run_ti_dynamic.py --spectre --ideal --impairment ideal \
  --output-dir outputs/spectre/ideal

python scripts/run_ti_static.py --spectre --ideal --impairment ideal \
  --output-dir outputs/spectre/ideal

# Full suite (python + spectre when `spectre` is on PATH)
./scripts/run_all_ti_simulations.sh
```

## Outputs

```text
outputs/spectre/<case>/
  ti_dynamic_waveform.csv
  ti_static_waveform.csv
  ti_spectrum.svg
  ti_inl_dnl.svg
  logs/spectre_*.log
  logs/netlists/*.scs
  logs/*.nutascii
```

## Compare engines

```bash
python scripts/compare_ti_engines.py --output-root outputs --case ideal

# Fail if Spectre (VA export) differs from Python beyond PLAN tolerances
python scripts/compare_ti_engines.py --output-root outputs --case clock --check-parity
```

Initial parity limits (see `PLAN.md` Phase 1.4): SNDR within **2 dB** (dynamic), max |DNL| within **0.05 LSB**, max |INL| within **0.15 LSB** (ideal static).

## Requirements

- Cadence Spectre with Verilog-A (AHDL)
- `adc-model` installed from Git (`./scripts/install_python.sh`; optional override: `ADC_MODEL_ROOT`)
