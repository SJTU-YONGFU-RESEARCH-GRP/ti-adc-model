#!/usr/bin/env python3
"""Run TI ADC dynamic spectrum testbench (Python or Spectre)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adc_model.io import read_waveform_csv, write_waveform_csv

from ti_adc.analysis import (
    compute_ti_dynamic_metrics,
    format_ti_dynamic_summary,
    plot_ti_spectrum,
)
from ti_adc.cli_helpers import (
    add_ti_adc_args,
    add_ti_noise_args,
    add_ti_simulator_args,
    add_ti_spectre_export_args,
    build_mismatch_profile,
    build_noise_config,
    build_ti_adc_config,
    resolve_engine_label,
)
from ti_adc.ngspice_engine import run_ti_ngspice_testbench
from ti_adc.spectre_engine import run_ti_spectre_testbench
from ti_adc.ti_model import simulate_ti_dynamic


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ti_adc_args(parser)
    add_ti_noise_args(parser)
    add_ti_simulator_args(parser)
    add_ti_spectre_export_args(parser)
    parser.add_argument("--num-samples", type=int, default=8192)
    parser.add_argument("--coherent-bin", type=int, default=997)
    parser.add_argument("--fin", type=float, default=None, help="Input tone (Hz).")
    parser.add_argument(
        "--input",
        type=Path,
        help="Existing ti_dynamic_waveform.csv (skip simulation).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/python/dynamic"),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = build_ti_adc_config(args)
    noise = build_noise_config(args)
    profile = build_mismatch_profile(args, cfg)
    engine = resolve_engine_label(args.simulator)

    fin_hz = args.fin
    if fin_hz is None:
        fin_hz = args.coherent_bin * cfg.fs_hz / args.num_samples

    csv_path = output_dir / "ti_dynamic_waveform.csv"
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.input is not None:
        data = read_waveform_csv(args.input)
    elif args.simulator == "spectre":
        run_ti_spectre_testbench(
            repo_root=repo_root,
            scs_path=repo_root / "testbench/spectre/ti_dynamic_spectrum.scs",
            output_csv=csv_path,
            log_path=log_dir / "spectre_dynamic.log",
            cfg=cfg,
            noise=noise,
            num_samples=args.num_samples,
            coherent_bin=args.coherent_bin,
            golden_export=args.golden_export,
        )
        data = read_waveform_csv(csv_path)
    elif args.simulator == "ngspice":
        run_ti_ngspice_testbench(
            repo_root=repo_root,
            template_path=repo_root / "testbench/ngspice/ti_dynamic_spectrum.cir",
            output_csv=csv_path,
            log_path=log_dir / "ngspice_dynamic.log",
            cfg=cfg,
            noise=noise,
            num_samples=args.num_samples,
            fin_hz=fin_hz,
            profile=profile,
            golden_export=args.golden_export,
        )
        data = read_waveform_csv(csv_path)
    else:
        data = simulate_ti_dynamic(
            cfg,
            num_samples=args.num_samples,
            fin_hz=fin_hz,
            coherent_bin=args.coherent_bin,
            noise=noise,
            profile=profile,
        )
        write_waveform_csv(csv_path, data)

    codes = data["code"].astype("int64")
    report = compute_ti_dynamic_metrics(codes, cfg, fin_hz=fin_hz)
    plot_ti_spectrum(report, cfg, output_path=output_dir / "ti_spectrum.svg")

    summary_path = output_dir / "ti_dynamic_summary.txt"
    summary_path.write_text(format_ti_dynamic_summary(report) + "\n", encoding="utf-8")

    print(f"Engine     : {engine}")
    print(format_ti_dynamic_summary(report))
    print(f"Waveform : {csv_path.resolve()}")
    print(f"Spectrum : {(output_dir / 'ti_spectrum.svg').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
