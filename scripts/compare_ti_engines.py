#!/usr/bin/env python3
"""Compare TI static/dynamic results across Python, Spectre, and ngspice."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from adc_model.io import read_waveform_csv

from ti_adc.analysis import compute_ti_dynamic_metrics, compute_ti_inl_dnl
from ti_adc.cli_helpers import (
    add_ti_adc_args,
    add_ti_noise_args,
    build_noise_config,
    build_ti_adc_config,
)

ENGINES = ("python", "spectre", "ngspice")

# PLAN.md Phase 1.4 tolerances (Python reference vs independent simulators).
DEFAULT_SNDR_TOLERANCE_DB = 2.0
DEFAULT_DNL_TOLERANCE_LSB = 0.05
DEFAULT_INL_TOLERANCE_LSB = 0.15
IMPAIRED_DNL_TOLERANCE_LSB = 1.5
IMPAIRED_INL_TOLERANCE_LSB = 1.0
IMPAIRED_CASES = frozenset({"gain", "offset", "clock", "combined", "skew"})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ti_adc_args(parser)
    add_ti_noise_args(parser)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs"),
        help="Parent with <engine>/<case>/ subfolders.",
    )
    parser.add_argument(
        "--case",
        type=str,
        default="ideal",
        help="Impairment case folder name (ideal, gain, offset, clock, combined).",
    )
    parser.add_argument("--coherent-bin", type=int, default=997)
    parser.add_argument("--num-samples", type=int, default=8192)
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=ENGINES,
        default=list(ENGINES),
    )
    parser.add_argument(
        "--check-parity",
        action="store_true",
        help="Exit 1 if Spectre dynamic SNDR differs from Python beyond tolerance.",
    )
    parser.add_argument(
        "--check-static",
        action="store_true",
        help="Also compare static max |DNL| and |INL| when CSVs exist.",
    )
    parser.add_argument(
        "--sndr-tolerance-db",
        type=float,
        default=DEFAULT_SNDR_TOLERANCE_DB,
        help="Max |SNDR_spectre - SNDR_python| for --check-parity.",
    )
    parser.add_argument(
        "--dnl-tolerance-lsb",
        type=float,
        default=DEFAULT_DNL_TOLERANCE_LSB,
        help="Max |max|DNL| delta for static parity check.",
    )
    parser.add_argument(
        "--inl-tolerance-lsb",
        type=float,
        default=DEFAULT_INL_TOLERANCE_LSB,
        help="Max |max|INL| delta for static parity check.",
    )
    return parser.parse_args()


def _static_tolerances(case: str, args: argparse.Namespace) -> tuple[float, float]:
    """Return (DNL, INL) tolerances for a case folder name."""
    if case in IMPAIRED_CASES:
        return (
            max(args.dnl_tolerance_lsb, IMPAIRED_DNL_TOLERANCE_LSB),
            max(args.inl_tolerance_lsb, IMPAIRED_INL_TOLERANCE_LSB),
        )
    return args.dnl_tolerance_lsb, args.inl_tolerance_lsb


def main() -> int:
    args = _parse_args()
    cfg = build_ti_adc_config(args)
    build_noise_config(args)
    fin_hz = args.coherent_bin * cfg.fs_hz / args.num_samples

    print(f"Case: {args.case}  Fin: {fin_hz/1e6:.4f} MHz  fs: {cfg.fs_hz/1e9:.3g} GHz")
    print(f"{'Engine':<10} {'SNDR (dB)':>10} {'ENOB':>8} {'Max|DNL|':>10} {'Max|INL|':>10}")
    print("-" * 52)

    metrics: dict[str, tuple[float, float, float]] = {}
    for engine in args.engines:
        case_dir = args.output_root / engine / args.case
        dyn_csv = case_dir / "ti_dynamic_waveform.csv"
        static_csv = case_dir / "ti_static_waveform.csv"
        if not dyn_csv.is_file():
            print(f"{engine:<10}  (missing {dyn_csv})")
            continue
        dyn = read_waveform_csv(dyn_csv)
        codes = dyn["code"].astype("int64")
        report = compute_ti_dynamic_metrics(codes, cfg, fin_hz=fin_hz)
        max_dnl = max_inl = float("nan")
        if static_csv.is_file():
            st = read_waveform_csv(static_csv)
            lin = compute_ti_inl_dnl(
                st["vin"],
                st["code"].astype("int64"),
                cfg,
            )
            max_dnl = lin.max_dnl_lsb
            max_inl = lin.max_inl_lsb
        metrics[engine] = (report.metrics.sndr_db, max_dnl, max_inl)
        print(
            f"{engine:<10} {report.metrics.sndr_db:10.2f} "
            f"{report.metrics.enob_bits:8.2f} {max_dnl:10.4f} {max_inl:10.4f}"
        )

    if not args.check_parity:
        return 0

    if "python" not in metrics:
        print("parity check: need python reference outputs", file=sys.stderr)
        return 1

    reference_engine = "python"
    compare_engines = [engine for engine in args.engines if engine != "python"]
    if not compare_engines:
        print("parity check: need at least one non-python engine", file=sys.stderr)
        return 1

    py_sndr, py_dnl, py_inl = metrics["python"]
    failed: list[str] = []
    dnl_tol, inl_tol = _static_tolerances(args.case, args)

    for engine in compare_engines:
        if engine not in metrics:
            print(f"parity check: missing {engine} outputs", file=sys.stderr)
            return 1
        eng_sndr, eng_dnl, eng_inl = metrics[engine]
        sndr_delta = abs(eng_sndr - py_sndr)
        if sndr_delta > args.sndr_tolerance_db:
            failed.append(
                f"{engine} SNDR delta {sndr_delta:.2f} dB > {args.sndr_tolerance_db:.2f} dB"
            )

        if args.check_static and math.isfinite(py_dnl) and math.isfinite(eng_dnl):
            dnl_delta = abs(eng_dnl - py_dnl)
            if dnl_delta > dnl_tol:
                failed.append(
                    f"{engine} max|DNL| delta {dnl_delta:.4f} LSB "
                    f"> {dnl_tol:.4f} LSB"
                )
            inl_delta = abs(eng_inl - py_inl)
            if inl_delta > inl_tol:
                failed.append(
                    f"{engine} max|INL| delta {inl_delta:.4f} LSB "
                    f"> {inl_tol:.4f} LSB"
                )

    if failed:
        print("PARITY FAIL:", file=sys.stderr)
        for msg in failed:
            print(f"  - {msg}", file=sys.stderr)
        return 1

    if len(compare_engines) == 1:
        eng_sndr = metrics[compare_engines[0]][0]
        sndr_delta = abs(eng_sndr - py_sndr)
        print(
            f"PARITY PASS: {compare_engines[0]} SNDR delta {sndr_delta:.2f} dB "
            f"(limit {args.sndr_tolerance_db:.2f} dB)"
        )
    else:
        print(
            f"PARITY PASS: all engines within {args.sndr_tolerance_db:.2f} dB SNDR "
            f"of {reference_engine}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
