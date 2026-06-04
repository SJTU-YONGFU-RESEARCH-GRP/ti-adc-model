"""TI waveform mux and Spectre multi-channel export helpers."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from adc_model.config import AdcConfig, AdcNoiseConfig
from adc_model.io import clock_pulse_waveform, rising_edge_indices
from adc_model.noise import apply_analog_front_end_at_edges, apply_post_front_end_noise, build_dnl_profile, quantize_front_end
from adc_model.static import decode_codes

from ti_adc.config import TiAdcConfig
from ti_adc.mismatch import ChannelMismatchSample, MismatchProfile, effective_mismatch, preset_static_profile
from ti_adc.ti_model import _sample_channel_with_skew

# Spectre nutascii often settles v_code one dense point after the clock edge.
_V_CODE_EDGE_OFFSET = 1
# ngspice coarse-grid captures settle v_code on the same step as the clock edge.
_NGSPICE_V_CODE_EDGE_OFFSET = 0


def _channel_keys(num_channels: int, prefix: str) -> list[str]:
    return [f"{prefix}{k}" for k in range(num_channels)]


def _python_mux_time_grid(cfg: TiAdcConfig, num_samples: int) -> NDArray[np.float64]:
    """Return mux times aligned with :func:`ti_adc.ti_model.simulate_ti_dynamic`."""
    return np.arange(num_samples, dtype=np.float64) / cfg.fs_hz


def _resample_vin_to_mux_grid(
    time_dense: NDArray[np.float64],
    vin_dense: NDArray[np.float64],
    cfg: TiAdcConfig,
    num_samples: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Resample a dense Spectre ``vin`` transient onto the uniform Python mux grid."""
    mux_time = _python_mux_time_grid(cfg, num_samples)
    vin_mux = np.interp(mux_time, time_dense, vin_dense)
    return mux_time, vin_mux


def _ti_static_sample_hold(codes_at_instants: NDArray[np.int64]) -> NDArray[np.int64]:
    """Apply Verilog-A-style global sample-and-hold across the muxed stream."""
    codes = np.empty(len(codes_at_instants), dtype=np.int64)
    code_int = int(codes_at_instants[0])
    for idx, new_code in enumerate(codes_at_instants):
        new_code = int(new_code)
        if new_code != code_int:
            code_int = new_code
        codes[idx] = code_int
    return codes


def _skew_adjusted_sample_time(
    t_mux: float,
    sample_index: int,
    channel: int,
    cfg: TiAdcConfig,
    profile: MismatchProfile,
) -> float:
    """Return the effective ADC sample time for mux index ``sample_index``."""
    mismatch = effective_mismatch(cfg, profile, sample_index, channel)
    skew = float(mismatch.timing_skew_s)
    if cfg.enable_cal:
        skew -= float(cfg.timing_cal_s[channel])
    return t_mux + skew


def _vin_at_mux_instant(
    mux_time: NDArray[np.float64],
    vin_mux: NDArray[np.float64],
    sample_index: int,
    channel: int,
    cfg: TiAdcConfig,
    profile: MismatchProfile,
) -> tuple[float, float]:
    """Return ``(vin_sample, v_eff)`` at the muxed sample instant (matches TI Python model)."""
    mismatch = effective_mismatch(cfg, profile, sample_index, channel)
    t_mux = float(mux_time[sample_index])
    t_sample = _skew_adjusted_sample_time(t_mux, sample_index, channel, cfg, profile)
    vin_sample = float(np.interp(t_sample, mux_time, vin_mux))
    v_eff = mismatch.gain * vin_sample + mismatch.offset_v
    if cfg.enable_cal:
        v_eff = (v_eff - float(cfg.offset_cal_v[channel])) / float(cfg.gain_cal[channel])
    return vin_sample, v_eff


def _va_code_at_mux_sample(
    time: NDArray[np.float64],
    clk: NDArray[np.float64],
    v_code: NDArray[np.float64],
    sample_index: int,
    channel: int,
    cfg: TiAdcConfig,
    profile: MismatchProfile,
    *,
    edge_offset: int = _V_CODE_EDGE_OFFSET,
) -> int:
    """Return VA ``v_code`` at mux index ``sample_index`` on ``channel``.

    Picks the channel clock edge whose time is closest to ``n/fs`` plus the
    effective timing skew for that sample (matches interleaved TI sampling).
    Spectre nutascii often settles ``v_code`` one dense point after the edge.
    """
    adc_cfg = cfg.effective_adc_config()
    edges = rising_edge_indices(clk)
    if len(edges) == 0:
        idx = 0
    else:
        mismatch = effective_mismatch(cfg, profile, sample_index, channel)
        skew = float(mismatch.timing_skew_s)
        if cfg.enable_cal:
            skew -= float(cfg.timing_cal_s[channel])
        t_target = sample_index / cfg.fs_hz + skew
        edge_times = time[edges]
        edge_idx = int(np.argmin(np.abs(edge_times - t_target)))
        idx = min(int(edges[edge_idx]) + edge_offset, len(v_code) - 1)
    return int(decode_codes(np.array([v_code[idx]], dtype=np.float64), adc_cfg)[0])


def _finalize_ti_ngspice_channels(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
) -> None:
    """Finish per-channel ``v_nl{k}`` at clock edges and populate ``v_code{k}``."""
    adc_cfg = cfg.effective_adc_config()
    time_len = len(waveform["time"])

    for k in range(cfg.num_channels):
        clk_key = f"clk{k}"
        if clk_key not in waveform:
            continue
        if f"v_code{k}" in waveform:
            continue

        clk = waveform[clk_key]
        v_nl = waveform[f"v_nl{k}"]
        edges = rising_edge_indices(clk)
        if len(edges) == 0:
            waveform[f"v_code{k}"] = np.zeros(time_len, dtype=np.float64)
            continue

        rng = np.random.default_rng(noise.noise_seed + k)
        dnl_profile = build_dnl_profile(adc_cfg, noise) if noise.dnl_sigma_lsb > 0.0 else None
        codes = apply_post_front_end_noise(
            v_nl[edges].astype(np.float64),
            adc_cfg,
            noise,
            rng=rng,
            dnl_profile=dnl_profile,
        )
        v_code = np.zeros(time_len, dtype=np.float64)
        for edge_idx, code in zip(edges, codes, strict=True):
            idx = min(int(edge_idx) + _NGSPICE_V_CODE_EDGE_OFFSET, time_len - 1)
            v_code[idx] = float(code) * cfg.lsb
        waveform[f"v_code{k}"] = v_code


def _va_codes_at_mux_grid(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile,
    edge_offset: int = _V_CODE_EDGE_OFFSET,
) -> NDArray[np.int64]:
    """Read per-channel ``v_code`` on the skew-aware mux grid (end-to-end path)."""
    time = waveform["time"]
    codes = np.zeros(num_samples, dtype=np.int64)
    for n in range(num_samples):
        k = n % cfg.num_channels
        codes[n] = _va_code_at_mux_sample(
            time,
            waveform[f"clk{k}"],
            waveform[f"v_code{k}"],
            n,
            k,
            cfg,
            profile,
            edge_offset=edge_offset,
        )
    return codes


def _channel_adc_config_with_mismatch(
    cfg: TiAdcConfig,
    channel: int,
    mismatch: ChannelMismatchSample,
) -> AdcConfig:
    """Build a per-sample :class:`AdcConfig` using effective mismatch values."""
    base = cfg.channel_adc_config(channel)
    return AdcConfig(
        bits=base.bits,
        vrefp=base.vrefp,
        vrefn=base.vrefn,
        fs_hz=base.fs_hz,
        gain=mismatch.gain,
        offset_v=mismatch.offset_v,
    )


def _quantize_codes_at_mux_grid(
    time_dense: NDArray[np.float64],
    vin_dense: NDArray[np.float64],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile,
    noise: AdcNoiseConfig | None = None,
) -> NDArray[np.int64]:
    """Quantize ``vin`` on the Python golden mux grid.

    Spectre ``vin`` is resampled to the uniform ``n/fs`` grid first so skew lookups
    match :func:`ti_adc.ti_model.simulate_ti_dynamic`. When ``noise`` is enabled the
    Python noise model is applied (golden parity path).
    """
    mux_time, vin_mux = _resample_vin_to_mux_grid(time_dense, vin_dense, cfg, num_samples)
    adc_cfg = cfg.effective_adc_config()
    codes = np.zeros(num_samples, dtype=np.int64)
    noise_cfg = noise or AdcNoiseConfig()

    if not noise_cfg.enabled:
        for n in range(num_samples):
            k = n % cfg.num_channels
            _vin, v_eff = _vin_at_mux_instant(mux_time, vin_mux, n, k, cfg, profile)
            codes[n] = int(quantize_front_end(np.array([v_eff], dtype=np.float64), adc_cfg)[0])
        return codes

    dt = 1.0 / cfg.fs_hz
    rng = np.random.default_rng(noise_cfg.noise_seed)
    dnl_profile = (
        build_dnl_profile(adc_cfg, noise_cfg) if noise_cfg.dnl_sigma_lsb > 0.0 else None
    )
    edge_one = np.array([0], dtype=np.int64)
    for n in range(num_samples):
        k = n % cfg.num_channels
        vin_s, mismatch = _sample_channel_with_skew(mux_time, vin_mux, n, k, cfg, profile)
        ch_cfg = _channel_adc_config_with_mismatch(cfg, k, mismatch)
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
        codes[n] = int(quantize_front_end(v_front, adc_cfg)[0])
    return codes


def _mux_waveform_dict(
    mux_time: NDArray[np.float64],
    vin_mux: NDArray[np.float64],
    cfg: TiAdcConfig,
    codes: NDArray[np.int64],
) -> dict[str, NDArray[np.float64]]:
    """Build a muxed waveform dict from integer codes."""
    v_mux = codes.astype(np.float64) * cfg.lsb
    return {
        "time": mux_time,
        "vin": vin_mux,
        "clk": clock_pulse_waveform(mux_time, cfg.fs_hz),
        "v_code": v_mux,
        "code": codes.astype(np.float64),
    }


def build_ti_static_mux_from_vin(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
    noise: AdcNoiseConfig | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Build a muxed static ramp using the Python golden sampling grid."""
    mismatch_profile = profile or preset_static_profile()
    mux_time, vin_mux = _resample_vin_to_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples,
    )
    codes_at_instants = _quantize_codes_at_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
        noise=noise,
    )
    codes = _ti_static_sample_hold(codes_at_instants)
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def build_ti_dynamic_mux_from_vin(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
    noise: AdcNoiseConfig | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Build a muxed dynamic capture using the Python golden sampling grid."""
    mismatch_profile = profile or preset_static_profile()
    mux_time, vin_mux = _resample_vin_to_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples,
    )
    codes = _quantize_codes_at_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
        noise=noise,
    )
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def _quantize_ngspice_vnl_at_mux_grid(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile,
    noise: AdcNoiseConfig,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.int64]]:
    """Quantize ngspice ``v_nl{k}`` at Python mux instants (SPICE analog, aligned timing)."""
    time_dense = waveform["time"]
    mux_time, vin_mux = _resample_vin_to_mux_grid(
        time_dense,
        waveform["vin"],
        cfg,
        num_samples,
    )
    adc_cfg = cfg.effective_adc_config()
    codes = np.zeros(num_samples, dtype=np.int64)
    rng = np.random.default_rng(noise.noise_seed)
    dnl_profile = (
        build_dnl_profile(adc_cfg, noise) if noise.dnl_sigma_lsb > 0.0 else None
    )

    for n in range(num_samples):
        k = n % cfg.num_channels
        mismatch = effective_mismatch(cfg, profile, n, k)
        skew = float(mismatch.timing_skew_s)
        if cfg.enable_cal:
            skew -= float(cfg.timing_cal_s[k])
        t_target = n / cfg.fs_hz + skew
        idx = int(np.argmin(np.abs(time_dense - t_target)))
        v_nl = float(waveform[f"v_nl{k}"][idx])

        if noise.sigma_thermal_v > 0.0 or dnl_profile is not None:
            v_front = np.array([v_nl], dtype=np.float64)
            if noise.sigma_thermal_v > 0.0:
                v_front += rng.normal(0.0, noise.sigma_thermal_v, size=1)
            if dnl_profile is not None:
                code_idx = int(
                    np.clip(
                        np.floor((v_front[0] - cfg.vrefn) / cfg.lsb),
                        0,
                        cfg.max_code - 1,
                    )
                )
                v_front[0] += dnl_profile[code_idx]
            codes[n] = int(quantize_front_end(v_front, adc_cfg)[0])
        else:
            codes[n] = int(quantize_front_end(np.array([v_nl], dtype=np.float64), adc_cfg)[0])

    return mux_time, vin_mux, codes


def build_ti_static_mux_from_ngspice(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
    noise: AdcNoiseConfig | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Mux ngspice static capture (``v_code*`` or ``v_nl*``) onto the mux grid."""
    mismatch_profile = profile or preset_static_profile()
    noise_cfg = noise or AdcNoiseConfig()

    if "v_code0" in waveform:
        mux_time, vin_mux = _resample_vin_to_mux_grid(
            waveform["time"],
            waveform["vin"],
            cfg,
            num_samples,
        )
        codes_at_instants = _va_codes_at_mux_grid(
            waveform,
            cfg,
            num_samples=num_samples,
            profile=mismatch_profile,
            edge_offset=_NGSPICE_V_CODE_EDGE_OFFSET,
        )
        codes = _ti_static_sample_hold(codes_at_instants)
        return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)

    mux_time, vin_mux, codes_at_instants = _quantize_ngspice_vnl_at_mux_grid(
        waveform,
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
        noise=noise_cfg,
    )
    codes = _ti_static_sample_hold(codes_at_instants)
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def build_ti_dynamic_mux_from_ngspice(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
    noise: AdcNoiseConfig | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Mux ngspice channel ``v_nl`` at Python dynamic instants."""
    mismatch_profile = profile or preset_static_profile()
    noise_cfg = noise or AdcNoiseConfig()
    mux_time, vin_mux, codes = _quantize_ngspice_vnl_at_mux_grid(
        waveform,
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
        noise=noise_cfg,
    )
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def build_ti_static_mux_from_va(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Mux per-channel VA ``v_code`` onto the skew-aware ``n/fs`` grid (validation)."""
    mismatch_profile = profile or preset_static_profile()
    mux_time, vin_mux = _resample_vin_to_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples,
    )
    codes_at_instants = _va_codes_at_mux_grid(
        waveform,
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
    )
    codes = _ti_static_sample_hold(codes_at_instants)
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def build_ti_dynamic_mux_from_va(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int,
    profile: MismatchProfile | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Mux per-channel VA ``v_code`` onto the skew-aware ``n/fs`` grid (validation)."""
    mismatch_profile = profile or preset_static_profile()
    mux_time, vin_mux = _resample_vin_to_mux_grid(
        waveform["time"],
        waveform["vin"],
        cfg,
        num_samples,
    )
    codes = _va_codes_at_mux_grid(
        waveform,
        cfg,
        num_samples=num_samples,
        profile=mismatch_profile,
    )
    return _mux_waveform_dict(mux_time, vin_mux, cfg, codes)


def interleave_channel_samples(
    channel_v_codes: list[NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    num_samples: int | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    """Round-robin mux of per-channel edge-aligned ``v_code`` arrays."""
    m = cfg.num_channels
    if len(channel_v_codes) != m:
        msg = f"Expected {m} channel waveforms, got {len(channel_v_codes)}."
        raise ValueError(msg)

    lengths = [len(v) for v in channel_v_codes]
    max_mux = min(lengths) * m
    if num_samples is None:
        num_samples = max_mux
    num_samples = min(num_samples, max_mux)

    v_mux = np.zeros(num_samples, dtype=np.float64)
    for n in range(num_samples):
        k = n % m
        idx = n // m
        v_mux[n] = channel_v_codes[k][idx]
    codes = decode_codes(v_mux, cfg.effective_adc_config())
    return v_mux, codes


def prepare_ti_ngspice_waveform(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    max_samples: int | None = None,
    static_capture: bool = False,
    noise: AdcNoiseConfig | None = None,
    profile: MismatchProfile | None = None,
    golden_export: bool = False,
) -> dict[str, NDArray[np.float64]]:
    """Prepare muxed TI waveforms from ngspice ``wrdata``.

    Quantizer-limited (ideal): mux from ngspice ``v_nl`` / ``v_code`` (independent E2E).

    With noise enabled: full ``jitter → gain → nonlinearity → thermal → DNL`` on the
    captured ``vin`` transient at Python mux instants (matches Python / Spectre order;
    SPICE channel chains still run for analog validation in ``wrdata``).

    ``golden_export=True`` forces the vin path even when ideal.
    """
    mismatch_profile = profile or preset_static_profile()
    noise_cfg = noise or AdcNoiseConfig()
    use_vin_mux = golden_export or noise_cfg.enabled

    if static_capture:
        num_samples = max_samples if max_samples is not None else cfg.num_codes * 4
        if use_vin_mux:
            return build_ti_static_mux_from_vin(
                waveform,
                cfg,
                num_samples=num_samples,
                profile=mismatch_profile,
                noise=noise_cfg,
            )
        return build_ti_static_mux_from_ngspice(
            waveform,
            cfg,
            num_samples=num_samples,
            profile=mismatch_profile,
            noise=noise_cfg,
        )

    if max_samples is None:
        msg = "Dynamic ngspice export requires max_samples."
        raise ValueError(msg)

    if use_vin_mux:
        return build_ti_dynamic_mux_from_vin(
            waveform,
            cfg,
            num_samples=max_samples,
            profile=mismatch_profile,
            noise=noise_cfg,
        )
    return build_ti_dynamic_mux_from_ngspice(
        waveform,
        cfg,
        num_samples=max_samples,
        profile=mismatch_profile,
        noise=noise_cfg,
    )


def prepare_ti_spectre_waveform(
    waveform: dict[str, NDArray[np.float64]],
    cfg: TiAdcConfig,
    *,
    max_samples: int | None = None,
    static_capture: bool = False,
    noise: AdcNoiseConfig | None = None,
    profile: MismatchProfile | None = None,
    golden_export: bool = False,
) -> dict[str, NDArray[np.float64]]:
    """Prepare muxed TI waveforms from dense Spectre nutascii.

    By default (``golden_export=False``) muxes simulated per-channel ``v_code*``
    from Verilog-A — end-to-end validation without re-quantizing ``vin`` in Python.

    With ``golden_export=True``, resamples ``vin`` on the Python ``n/fs`` grid and
    applies the Python quantizer/noise model (Tier-1 vector parity only).
    """
    mismatch_profile = profile or preset_static_profile()
    noise_cfg = noise or AdcNoiseConfig()

    if static_capture:
        num_samples = max_samples if max_samples is not None else cfg.num_codes * 4
        if golden_export:
            return build_ti_static_mux_from_vin(
                waveform,
                cfg,
                num_samples=num_samples,
                profile=mismatch_profile,
                noise=noise_cfg,
            )
        return build_ti_static_mux_from_va(
            waveform,
            cfg,
            num_samples=num_samples,
            profile=mismatch_profile,
        )

    if max_samples is None:
        msg = "Dynamic Spectre export requires max_samples."
        raise ValueError(msg)

    if golden_export:
        return build_ti_dynamic_mux_from_vin(
            waveform,
            cfg,
            num_samples=max_samples,
            profile=mismatch_profile,
            noise=noise_cfg,
        )
    return build_ti_dynamic_mux_from_va(
        waveform,
        cfg,
        num_samples=max_samples,
        profile=mismatch_profile,
    )


def spectre_save_statement(cfg: TiAdcConfig) -> str:
    """Return Spectre ``save`` line for TI array waveforms."""
    names = ["vin", "clk_mux"] + _channel_keys(cfg.num_channels, "clk")
    names += _channel_keys(cfg.num_channels, "v_code")
    return "save " + " ".join(names)
