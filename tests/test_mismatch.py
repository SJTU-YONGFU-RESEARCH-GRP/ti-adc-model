"""Tests for static and time-varying mismatch profiles."""

from __future__ import annotations

import numpy as np

from ti_adc.config import preset_combined_impaired, preset_ideal
from ti_adc.mismatch import (
    DriftWaveform,
    MismatchKind,
    effective_mismatch,
    preset_slow_drift,
    preset_static_profile,
    preset_step_drift,
)


def test_static_profile_matches_config() -> None:
    """Static profile should return baseline TiAdcConfig arrays."""
    cfg = preset_combined_impaired(num_channels=4)
    profile = preset_static_profile()
    sample = effective_mismatch(cfg, profile, sample_index=100, channel=2)
    assert sample.gain == cfg.gain[2]
    assert sample.offset_v == cfg.offset_v[2]
    assert sample.timing_skew_s == cfg.timing_skew_s[2]


def test_sinusoidal_drift_modulates_gain() -> None:
    """Sinusoidal drift should change gain between sample indices."""
    cfg = preset_ideal(num_channels=2)
    profile = preset_slow_drift(2, gain_drift_frac=0.01, drift_freq_hz=50e3)
    assert profile.kind == MismatchKind.TIME_VARYING
    s0 = effective_mismatch(cfg, profile, 0, 0).gain
    s1 = effective_mismatch(cfg, profile, 5, 0).gain
    assert abs(s0 - s1) > 1e-6


def test_step_drift_changes_mid_capture() -> None:
    """Step profile should flip mismatch before/after step_sample."""
    cfg = preset_ideal(num_channels=2)
    profile = preset_step_drift(2, step_sample=100, gain_drift_frac=0.02)
    assert profile.drift_waveform == DriftWaveform.STEP
    before = effective_mismatch(cfg, profile, 50, 0).gain
    after = effective_mismatch(cfg, profile, 150, 0).gain
    assert before < 1.0 < after
