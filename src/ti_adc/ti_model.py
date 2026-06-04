"""Time-interleaved ADC behavioral simulation."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from adc_model.config import AdcConfig, AdcNoiseConfig
from adc_model.io import clock_pulse_waveform
from adc_model.noise import (
    apply_analog_front_end_at_edges,
    build_dnl_profile,
    quantize_front_end,
)
from ti_adc.config import TiAdcConfig
from ti_adc.mismatch import ChannelMismatchSample, MismatchProfile, effective_mismatch, preset_static_profile


def _eval_vin(
    time: NDArray[np.float64],
    vin: NDArray[np.float64],
    t_sample: float,
) -> float:
    """Linear interpolation of the input waveform at ``t_sample``."""
    if t_sample <= time[0]:
        return float(vin[0])
    if t_sample >= time[-1]:
        return float(vin[-1])
    return float(np.interp(t_sample, time, vin))


def _apply_channel_front_end(
    vin_sample: float,
    mismatch: ChannelMismatchSample,
    cfg: TiAdcConfig,
    channel: int,
) -> float:
    """Gain, offset, timing skew, and optional per-channel correction."""
    v_eff = mismatch.gain * vin_sample + mismatch.offset_v
    if cfg.enable_cal:
        v_eff = (v_eff - float(cfg.offset_cal_v[channel])) / float(cfg.gain_cal[channel])
    return v_eff


def _quantize_sample(v_eff: float, cfg: TiAdcConfig) -> int:
    codes = quantize_front_end(np.array([v_eff], dtype=np.float64), cfg.effective_adc_config())
    return int(codes[0])


def _sample_channel_with_skew(
    time: NDArray[np.float64],
    vin: NDArray[np.float64],
    sample_index: int,
    channel: int,
    cfg: TiAdcConfig,
    profile: MismatchProfile,
) -> tuple[float, ChannelMismatchSample]:
    """Input voltage and mismatch at the effective sample instant."""
    mismatch = effective_mismatch(cfg, profile, sample_index, channel)
    t_ideal = sample_index / cfg.fs_hz
    skew = mismatch.timing_skew_s
    if cfg.enable_cal:
        skew = skew - float(cfg.timing_cal_s[channel])
    t_sample = t_ideal + skew
    vin_sample = _eval_vin(time, vin, t_sample)
    return vin_sample, mismatch


def simulate_ti_dynamic(
    cfg: TiAdcConfig,
    *,
    num_samples: int = 8192,
    fin_hz: float | None = None,
    amplitude: float | None = None,
    coherent_bin: int | None = None,
    noise: AdcNoiseConfig | None = None,
    profile: MismatchProfile | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Simulate a coherent sine through a TI muxed ADC.

    Args:
        cfg: TI array configuration.
        num_samples: Length of interleaved output stream.
        fin_hz: Tone frequency; default coherent bin.
        amplitude: Peak sine amplitude; default 0.95 * half FS.
        coherent_bin: FFT bin when ``fin_hz`` is omitted.
        noise: Shared per-channel noise (applied at mux sample instants).
        profile: Static or time-varying mismatch overlay.

    Returns:
        Waveform dict: ``time``, ``vin``, ``clk``, ``v_code``, ``code``,
        ``channel``, ``fin_hz``, optional per-channel diagnostics.
    """
    noise_cfg = noise or AdcNoiseConfig()
    mismatch_profile = profile or preset_static_profile()

    if fin_hz is None:
        bin_idx = coherent_bin if coherent_bin is not None else 997
        if bin_idx <= 0 or bin_idx >= num_samples // 2:
            msg = f"coherent_bin must be in (0, {num_samples // 2}), got {bin_idx}"
            raise ValueError(msg)
        fin_hz = bin_idx * cfg.fs_hz / num_samples

    dt = 1.0 / cfg.fs_hz
    time = np.arange(num_samples, dtype=np.float64) * dt
    mid = 0.5 * (cfg.vrefp + cfg.vrefn)
    if amplitude is None:
        amplitude = 0.95 * (cfg.vrefp - cfg.vrefn) / 2.0
    vin = mid + amplitude * np.sin(2.0 * np.pi * fin_hz * time)
    clk = clock_pulse_waveform(time, cfg.fs_hz)
    channel_idx = (np.arange(num_samples) % cfg.num_channels).astype(np.int64)

    rng = np.random.default_rng(noise_cfg.noise_seed)
    dnl_profile = build_dnl_profile(cfg.effective_adc_config(), noise_cfg) if noise_cfg.dnl_sigma_lsb > 0.0 else None

    codes = np.zeros(num_samples, dtype=np.int64)
    v_front = np.zeros(num_samples, dtype=np.float64)
    edge_one = np.array([0], dtype=np.int64)

    for n in range(num_samples):
        k = int(channel_idx[n])
        vin_s, mismatch = _sample_channel_with_skew(time, vin, n, k, cfg, mismatch_profile)

        if noise_cfg.enabled or dnl_profile is not None:
            ch_cfg = cfg.channel_adc_config(k)
            ch_cfg = AdcConfig(
                bits=ch_cfg.bits,
                vrefp=ch_cfg.vrefp,
                vrefn=ch_cfg.vrefn,
                fs_hz=ch_cfg.fs_hz,
                gain=mismatch.gain,
                offset_v=mismatch.offset_v,
            )
            vin_edge = np.array([vin_s], dtype=np.float64)
            v_front_edges = apply_analog_front_end_at_edges(
                vin_edge,
                edge_one,
                ch_cfg,
                noise_cfg,
                dt=dt,
                rng=rng,
                dnl_profile=dnl_profile,
            )
            v_eff = float(v_front_edges[0])
        else:
            v_eff = _apply_channel_front_end(vin_s, mismatch, cfg, k)

        v_front[n] = v_eff
        codes[n] = _quantize_sample(v_eff, cfg)

    v_code = codes.astype(np.float64) * cfg.lsb

    return {
        "time": time,
        "vin": vin,
        "clk": clk,
        "v_code": v_code,
        "code": codes.astype(np.float64),
        "channel": channel_idx.astype(np.float64),
        "fin_hz": np.array([fin_hz], dtype=np.float64),
        "v_front": v_front,
    }


def simulate_ti_static(
    cfg: TiAdcConfig,
    samples_per_code: int = 4,
    noise: AdcNoiseConfig | None = None,
    profile: MismatchProfile | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Simulate a slow ramp for TI muxed INL/DNL analysis.

    Args:
        cfg: TI array configuration.
        samples_per_code: Clock periods per output code on the muxed stream.
        noise: Optional noise model (effective depth bumped to 16 when enabled).
        profile: Static or time-varying mismatch overlay.

    Returns:
        Waveform dict compatible with :func:`adc_model.static.compute_inl_dnl`.
    """
    noise_cfg = noise or AdcNoiseConfig()
    mismatch_profile = profile or preset_static_profile()
    if noise_cfg.enabled and samples_per_code < 16:
        samples_per_code = 16

    num_samples = cfg.num_codes * samples_per_code
    dt = 1.0 / cfg.fs_hz
    time = np.arange(num_samples, dtype=np.float64) * dt
    margin = 0.5 * cfg.lsb
    vin = np.linspace(cfg.vrefn + margin, cfg.vrefp - margin, num_samples)
    clk = clock_pulse_waveform(time, cfg.fs_hz)
    channel_idx = (np.arange(num_samples) % cfg.num_channels).astype(np.int64)

    codes = np.zeros(num_samples, dtype=np.int64)
    code_int = 0
    codes[0] = 0

    rng = np.random.default_rng(noise_cfg.noise_seed)
    dnl_profile = (
        build_dnl_profile(cfg.effective_adc_config(), noise_cfg)
        if noise_cfg.dnl_sigma_lsb > 0.0
        else None
    )
    edge_one = np.array([0], dtype=np.int64)

    for n in range(num_samples):
        k = int(channel_idx[n])
        vin_s, mismatch = _sample_channel_with_skew(time, vin, n, k, cfg, mismatch_profile)

        if noise_cfg.enabled or dnl_profile is not None:
            ch_cfg = cfg.channel_adc_config(k)
            ch_cfg = AdcConfig(
                bits=ch_cfg.bits,
                vrefp=ch_cfg.vrefp,
                vrefn=ch_cfg.vrefn,
                fs_hz=ch_cfg.fs_hz,
                gain=mismatch.gain,
                offset_v=mismatch.offset_v,
            )
            vin_edge = np.array([vin_s], dtype=np.float64)
            v_front = apply_analog_front_end_at_edges(
                vin_edge,
                edge_one,
                ch_cfg,
                noise_cfg,
                dt=dt,
                rng=rng,
                dnl_profile=dnl_profile,
            )
            v_eff = float(v_front[0])
        else:
            v_eff = _apply_channel_front_end(vin_s, mismatch, cfg, k)

        new_code = _quantize_sample(v_eff, cfg)
        if new_code != code_int:
            code_int = new_code
        codes[n] = code_int

    v_code = codes.astype(np.float64) * cfg.lsb
    return {
        "time": time,
        "vin": vin,
        "clk": clk,
        "v_code": v_code,
        "code": codes.astype(np.float64),
        "channel": channel_idx.astype(np.float64),
    }
