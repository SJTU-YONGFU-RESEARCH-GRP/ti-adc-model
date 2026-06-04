"""CLI helpers for TI ADC scripts."""

from __future__ import annotations

import argparse

from adc_model.cli_helpers import add_noise_args as add_adc_noise_args
from adc_model.cli_helpers import add_simulator_args, resolve_engine_label
from adc_model.config import AdcNoiseConfig

from ti_adc.config import (
    DEFAULT_BITS,
    DEFAULT_FS_HZ,
    TiAdcConfig,
    preset_combined_impaired,
    preset_ideal,
)
from ti_adc.mismatch import (
    DriftWaveform,
    MismatchKind,
    MismatchProfile,
    preset_slow_drift,
    preset_static_profile,
    preset_step_drift,
)


def add_ti_adc_args(parser: argparse.ArgumentParser) -> None:
    """Register TI ADC configuration arguments."""
    parser.add_argument("--channels", type=int, default=4, help="Number of interleaved channels M.")
    parser.add_argument(
        "--bits",
        type=int,
        default=DEFAULT_BITS,
        help=f"ADC resolution (default: {DEFAULT_BITS}-bit).",
    )
    parser.add_argument("--vrefp", type=float, default=1.0)
    parser.add_argument("--vrefn", type=float, default=0.0)
    parser.add_argument(
        "--fs",
        type=float,
        default=DEFAULT_FS_HZ,
        help="Muxed output sample rate in Hz (default: 1 GHz).",
    )
    parser.add_argument(
        "--impairment",
        choices=("ideal", "combined", "gain", "offset", "skew", "clock"),
        default="combined",
        help="Static mismatch preset (clock is timing skew, alias for skew).",
    )
    parser.add_argument("--enable-cal", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--mismatch-mode",
        choices=("static", "time_varying"),
        default="static",
        help="Static-only or time-varying mismatch overlay.",
    )
    parser.add_argument(
        "--drift-waveform",
        choices=("sinusoid", "ramp", "step"),
        default="sinusoid",
    )
    parser.add_argument("--drift-freq-hz", type=float, default=15e3)
    parser.add_argument("--gain-drift-frac", type=float, default=0.005)
    parser.add_argument("--offset-drift-v", type=float, default=2e-3)
    parser.add_argument("--skew-drift-s", type=float, default=20e-12)
    parser.add_argument("--step-sample", type=int, default=4096)


def add_ti_simulator_args(parser: argparse.ArgumentParser) -> None:
    """Register simulator selection (python / spectre / ngspice)."""
    add_simulator_args(parser)


def add_ti_spectre_export_args(parser: argparse.ArgumentParser) -> None:
    """Register optional golden-export mode (Tier-1 vector parity only)."""
    parser.add_argument(
        "--golden-export",
        action="store_true",
        help=(
            "Re-quantize simulator vin on the Python mux grid (Tier-1 vector parity). "
            "Default: mux each engine's own per-channel outputs (independent E2E)."
        ),
    )


def add_ti_noise_args(parser: argparse.ArgumentParser) -> None:
    """Reuse adc-model noise arguments."""
    add_adc_noise_args(parser)


def normalize_impairment(name: str) -> str:
    """Map CLI impairment name to internal preset (``clock`` → ``skew``)."""
    if name == "clock":
        return "skew"
    return name


def build_ti_adc_config(args: argparse.Namespace) -> TiAdcConfig:
    """Build :class:`TiAdcConfig` from CLI arguments."""
    m = args.channels
    impairment = normalize_impairment(args.impairment)
    if getattr(args, "ideal", False) and impairment == "combined":
        impairment = "ideal"
    if impairment == "ideal":
        cfg = preset_ideal(m, bits=args.bits, fs_hz=args.fs)
    elif impairment == "gain":
        from ti_adc.config import preset_gain_mismatch

        cfg = preset_gain_mismatch(m)
    elif impairment == "offset":
        from ti_adc.config import preset_offset_mismatch

        cfg = preset_offset_mismatch(m)
    elif impairment == "skew":
        from ti_adc.config import preset_skew_mismatch

        cfg = preset_skew_mismatch(m)
    else:
        cfg = preset_combined_impaired(m)

    return TiAdcConfig(
        num_channels=m,
        bits=args.bits,
        vrefp=args.vrefp,
        vrefn=args.vrefn,
        fs_hz=args.fs,
        gain=cfg.gain,
        offset_v=cfg.offset_v,
        timing_skew_s=cfg.timing_skew_s,
        enable_cal=args.enable_cal,
    )


def build_mismatch_profile(args: argparse.Namespace, cfg: TiAdcConfig) -> MismatchProfile:
    """Build mismatch profile from CLI arguments."""
    if args.mismatch_mode == "static":
        return preset_static_profile()

    waveform = DriftWaveform(args.drift_waveform)
    if waveform == DriftWaveform.STEP:
        return preset_step_drift(
            cfg.num_channels,
            step_sample=args.step_sample,
            gain_drift_frac=args.gain_drift_frac,
        )

    profile = preset_slow_drift(
        cfg.num_channels,
        gain_drift_frac=args.gain_drift_frac,
        offset_drift_v=args.offset_drift_v,
        skew_drift_s=args.skew_drift_s,
        drift_freq_hz=args.drift_freq_hz,
    )
    if waveform == DriftWaveform.RAMP:
        return MismatchProfile(
            kind=MismatchKind.TIME_VARYING,
            gain_drift_frac=profile.gain_drift_frac,
            offset_drift_v=profile.offset_drift_v,
            skew_drift_s=profile.skew_drift_s,
            drift_waveform=DriftWaveform.RAMP,
        )
    return profile


def build_noise_config(args: argparse.Namespace) -> AdcNoiseConfig:
    """Build noise config (adc-model compatible)."""
    if args.ideal:
        return AdcNoiseConfig()
    return AdcNoiseConfig(
        sigma_thermal_v=args.sigma_thermal_v,
        jitter_rms_s=args.jitter_rms_s,
        nonlinearity_a2=args.nonlinearity_a2,
        nonlinearity_a3=args.nonlinearity_a3,
        dnl_sigma_lsb=args.dnl_sigma_lsb,
        noise_seed=args.noise_seed,
    )
