#!/usr/bin/env python3
"""Run TI ADC static INL/DNL testbench (Python or Spectre)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adc_model.io import read_waveform_csv, write_waveform_csv

from ti_adc.analysis import (
    compute_ti_inl_dnl,
    format_ti_static_summary,
    plot_ti_inl_dnl,
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
from ti_adc.ti_model import simulate_ti_static


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ti_adc_args(parser)
    add_ti_noise_args(parser)
    add_ti_simulator_args(parser)
    add_ti_spectre_export_args(parser)
    parser.add_argument("--samples-per-code", type=int, default=4)
    parser.add_argument(
        "--inl-dnl-method",
        choices=("auto", "histogram", "transition"),
        default="auto",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Existing ti_static_waveform.csv (skip simulation).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/python/static"),
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

    csv_path = output_dir / "ti_static_waveform.csv"
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.input is not None:
        data = read_waveform_csv(args.input)
    elif args.simulator == "spectre":
        run_ti_spectre_testbench(
            repo_root=repo_root,
            scs_path=repo_root / "testbench/spectre/ti_static_inl_dnl.scs",
            output_csv=csv_path,
            log_path=log_dir / "spectre_static.log",
            cfg=cfg,
            noise=noise,
            samples_per_code=args.samples_per_code,
            golden_export=args.golden_export,
        )
        data = read_waveform_csv(csv_path)
    elif args.simulator == "ngspice":
        run_ti_ngspice_testbench(
            repo_root=repo_root,
            template_path=repo_root / "testbench/ngspice/ti_static_inl_dnl.cir",
            output_csv=csv_path,
            log_path=log_dir / "ngspice_static.log",
            cfg=cfg,
            noise=noise,
            samples_per_code=args.samples_per_code,
            profile=profile,
            golden_export=args.golden_export,
        )
        data = read_waveform_csv(csv_path)
    else:
        data = simulate_ti_static(
            cfg,
            samples_per_code=args.samples_per_code,
            noise=noise,
            profile=profile,
        )
        write_waveform_csv(csv_path, data)

    codes = data["code"].astype("int64")
    linearity = compute_ti_inl_dnl(
        data["vin"],
        codes,
        cfg,
        method=args.inl_dnl_method,
    )
    plot_ti_inl_dnl(linearity, cfg, output_path=output_dir / "ti_inl_dnl.svg")

    summary_path = output_dir / "ti_static_summary.txt"
    summary_path.write_text(format_ti_static_summary(linearity) + "\n", encoding="utf-8")

    print(f"Engine     : {engine}")
    print(format_ti_static_summary(linearity))
    print(f"Waveform : {csv_path.resolve()}")
    print(f"INL/DNL  : {(output_dir / 'ti_inl_dnl.svg').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
