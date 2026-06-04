"""Tests for TI ADC simulation and analysis."""

from __future__ import annotations

import numpy as np

from adc_model.config import AdcNoiseConfig

from ti_adc.analysis import compute_ti_dynamic_metrics, mismatch_spur_frequencies
from ti_adc.config import (
    ideal_noise,
    preset_mismatch_corrected,
    preset_combined_impaired,
    preset_gain_mismatch,
    preset_ideal,
    preset_offset_mismatch,
)
from ti_adc.mismatch import preset_static_profile
from ti_adc.ti_model import simulate_ti_dynamic, simulate_ti_static


def test_ideal_ti_dynamic_high_sndr() -> None:
    """Ideal TI mux should achieve high SNDR on a coherent tone."""
    cfg = preset_ideal(num_channels=4)
    data = simulate_ti_dynamic(
        cfg,
        num_samples=4096,
        coherent_bin=511,
        noise=ideal_noise(),
        profile=preset_static_profile(),
    )
    fin_hz = float(data["fin_hz"][0])
    report = compute_ti_dynamic_metrics(data["code"].astype(np.int64), cfg, fin_hz=fin_hz)
    assert report.metrics.sndr_db > 55.0


def test_gain_mismatch_spur_near_channel_fs() -> None:
    """Gain mismatch should create energy near fs/M."""
    cfg = preset_gain_mismatch(num_channels=4)
    data = simulate_ti_dynamic(
        cfg,
        num_samples=8192,
        coherent_bin=997,
        noise=ideal_noise(),
    )
    fin_hz = float(data["fin_hz"][0])
    report = compute_ti_dynamic_metrics(data["code"].astype(np.int64), cfg, fin_hz=fin_hz)
    assert report.metrics.sfdr_db < report.metrics.sndr_db + 5.0
    spur_freqs = [s.freq_hz for s in report.spurs]
    expected = cfg.channel_fs_hz
    assert any(abs(f - expected) < 5e3 for f in spur_freqs)


def test_offset_mismatch_reduces_sndr() -> None:
    """Offset mismatch should degrade SNDR versus ideal."""
    ideal_cfg = preset_ideal(num_channels=4)
    impaired_cfg = preset_offset_mismatch(num_channels=4)
    kwargs = dict(num_samples=4096, coherent_bin=511, noise=ideal_noise())
    ideal_report = compute_ti_dynamic_metrics(
        simulate_ti_dynamic(ideal_cfg, **kwargs)["code"].astype(np.int64),
        ideal_cfg,
        fin_hz=511 * ideal_cfg.fs_hz / 4096,
    )
    impaired_report = compute_ti_dynamic_metrics(
        simulate_ti_dynamic(impaired_cfg, **kwargs)["code"].astype(np.int64),
        impaired_cfg,
        fin_hz=511 * impaired_cfg.fs_hz / 4096,
    )
    assert impaired_report.metrics.sndr_db < ideal_report.metrics.sndr_db - 3.0


def test_mismatch_correction_improves_gain_mismatch() -> None:
    """Matching correction coeffs should recover SNDR after gain mismatch."""
    impaired = preset_gain_mismatch(num_channels=4)
    corrected = preset_mismatch_corrected(impaired)
    kwargs = dict(num_samples=4096, coherent_bin=511, noise=ideal_noise())
    fin = 511 * impaired.fs_hz / 4096
    raw = compute_ti_dynamic_metrics(
        simulate_ti_dynamic(impaired, **kwargs)["code"].astype(np.int64),
        impaired,
        fin_hz=fin,
    )
    corrected_report = compute_ti_dynamic_metrics(
        simulate_ti_dynamic(corrected, **kwargs)["code"].astype(np.int64),
        corrected,
        fin_hz=fin,
    )
    assert corrected_report.metrics.sndr_db > raw.metrics.sndr_db + 5.0


def test_static_ramp_covers_interior_codes() -> None:
    """Static ramp should hit many interior codes on the muxed stream."""
    cfg = preset_combined_impaired(num_channels=4)
    data = simulate_ti_static(cfg, samples_per_code=8, noise=ideal_noise())
    codes = data["code"].astype(np.int64)
    counts = np.bincount(np.clip(codes, 0, cfg.max_code), minlength=cfg.num_codes)
    interior_hits = int(np.count_nonzero(counts[50 : cfg.max_code - 50]))
    assert interior_hits > 200


def test_mismatch_spur_frequency_list() -> None:
    """Spur helper should list multiples of channel rate."""
    from ti_adc.config import DEFAULT_FS_HZ

    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    tones = mismatch_spur_frequencies(cfg, max_harmonic=2)
    assert tones[0][1] == DEFAULT_FS_HZ / cfg.num_channels
