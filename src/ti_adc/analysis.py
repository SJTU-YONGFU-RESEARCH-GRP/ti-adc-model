"""TI ADC analysis wrappers (reuses adc-model static/dynamic metrics)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from adc_model.dynamic import DynamicMetrics, compute_dynamic_metrics, plot_spectrum
from adc_model.static import StaticLinearity, compute_inl_dnl, plot_inl_dnl

from ti_adc.config import TiAdcConfig


@dataclass(frozen=True)
class TiSpurTone:
    """Spur identified near a TI mismatch tone frequency."""

    label: str
    freq_hz: float
    bin_index: int
    magnitude_dbfs: float


@dataclass(frozen=True)
class TiDynamicReport:
    """Dynamic metrics for a muxed TI capture plus mismatch-tone spurs."""

    metrics: DynamicMetrics
    spurs: tuple[TiSpurTone, ...]
    channel_fs_hz: float


def mismatch_spur_frequencies(cfg: TiAdcConfig, *, max_harmonic: int = 5) -> list[tuple[str, float]]:
    """Return labeled spur frequencies for offset/gain images (``k * fs / M``)."""
    m = cfg.num_channels
    fs_m = cfg.channel_fs_hz
    tones: list[tuple[str, float]] = []
    for k in range(1, max_harmonic + 1):
        f = k * fs_m
        tones.append((f"fs/M x {k}", f))
        if f >= cfg.fs_hz / 2.0:
            break
    return tones


def _find_nearest_bin(
    freq_hz: float,
    num_samples: int,
    fs_hz: float,
) -> int:
    return int(round(freq_hz * num_samples / fs_hz))


def analyze_ti_spurs(
    metrics: DynamicMetrics,
    cfg: TiAdcConfig,
    *,
    max_harmonic: int = 5,
    bin_tolerance: int = 2,
) -> tuple[TiSpurTone, ...]:
    """Identify spectral tones near expected TI mismatch frequencies."""
    num_samples = max(2, (len(metrics.magnitude_dbfs) - 1) * 2)
    spurs: list[TiSpurTone] = []
    for label, freq in mismatch_spur_frequencies(cfg, max_harmonic=max_harmonic):
        if freq <= 0.0 or freq >= cfg.fs_hz / 2.0:
            continue
        bin_idx = _find_nearest_bin(freq, num_samples, cfg.fs_hz)
        lo = max(1, bin_idx - bin_tolerance)
        hi = min(len(metrics.magnitude_dbfs) - 1, bin_idx + bin_tolerance)
        peak_idx = lo + int(np.argmax(metrics.magnitude_dbfs[lo : hi + 1]))
        spurs.append(
            TiSpurTone(
                label=label,
                freq_hz=float(metrics.freq_hz[peak_idx]),
                bin_index=peak_idx,
                magnitude_dbfs=float(metrics.magnitude_dbfs[peak_idx]),
            )
        )
    return tuple(spurs)


def compute_ti_dynamic_metrics(
    codes: NDArray[np.int64],
    cfg: TiAdcConfig,
    *,
    fin_hz: float,
    num_harmonics: int = 5,
) -> TiDynamicReport:
    """Compute FFT metrics on the muxed TI stream (same API as adc-model)."""
    adc_cfg = cfg.effective_adc_config()
    metrics = compute_dynamic_metrics(
        codes,
        adc_cfg,
        fin_hz=fin_hz,
        num_harmonics=num_harmonics,
    )
    spurs = analyze_ti_spurs(metrics, cfg)
    return TiDynamicReport(
        metrics=metrics,
        spurs=spurs,
        channel_fs_hz=cfg.channel_fs_hz,
    )


def compute_ti_inl_dnl(
    vin: NDArray[np.float64],
    codes: NDArray[np.int64],
    cfg: TiAdcConfig,
    *,
    method: str = "auto",
) -> StaticLinearity:
    """INL/DNL on the interleaved output (treats muxed stream as one ADC at ``fs``)."""
    return compute_inl_dnl(vin, codes, cfg.effective_adc_config(), method=method)


def plot_ti_spectrum(
    report: TiDynamicReport,
    cfg: TiAdcConfig,
    output_path: Path,
    *,
    title: str | None = None,
) -> None:
    """Plot spectrum and annotate TI mismatch spur markers."""
    plot_spectrum(
        report.metrics,
        cfg.effective_adc_config(),
        output_path,
        title=title or f"TI ADC spectrum ({cfg.num_channels}x{cfg.bits}-bit, fs={cfg.fs_hz/1e6:.3g} MHz)",
    )


def plot_ti_inl_dnl(
    result: StaticLinearity,
    cfg: TiAdcConfig,
    output_path: Path,
    *,
    title: str | None = None,
) -> None:
    """Plot INL/DNL for the muxed TI ramp capture."""
    plot_inl_dnl(
        result,
        cfg.effective_adc_config(),
        output_path,
        title=title or f"TI static linearity ({cfg.num_channels}x interleaved {cfg.bits}-bit)",
    )


def format_ti_dynamic_summary(report: TiDynamicReport) -> str:
    """Return a text summary similar to adc-model run scripts."""
    m = report.metrics
    lines = [
        f"SNDR     : {m.sndr_db:.2f} dB",
        f"SFDR     : {m.sfdr_db:.2f} dB",
        f"ENOB     : {m.enob_bits:.2f} bits",
        f"THD      : {m.thd_db:.2f} dB",
        f"Fin      : {m.fin_hz/1e6:.6f} MHz",
        f"Channel Fs (fs/M): {report.channel_fs_hz/1e6:.6f} MHz",
        "TI mismatch spurs:",
    ]
    for spur in report.spurs:
        lines.append(
            f"  {spur.label:12s} @ {spur.freq_hz/1e3:8.3f} kHz  {spur.magnitude_dbfs:7.2f} dBFS"
        )
    return "\n".join(lines)


def format_ti_static_summary(result: StaticLinearity) -> str:
    """Return static linearity summary text."""
    return (
        f"Max |DNL| : {result.max_dnl_lsb:.4f} LSB\n"
        f"Max |INL| : {result.max_inl_lsb:.4f} LSB"
    )
