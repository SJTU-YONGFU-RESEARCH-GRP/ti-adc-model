"""TI-ADC configuration and impairment presets."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from adc_model.config import AdcConfig, AdcNoiseConfig

# Default muxed output sample rate (Hz) and ADC resolution.
DEFAULT_FS_HZ: float = 1.0e9
DEFAULT_BITS: int = 10


def _validate_length(name: str, values: NDArray[np.float64], num_channels: int) -> None:
    if len(values) != num_channels:
        msg = f"{name} length {len(values)} != num_channels {num_channels}"
        raise ValueError(msg)


@dataclass(frozen=True)
class TiAdcConfig:
    """Configuration for a time-interleaved ADC array.

    Per-channel arrays describe static mismatch. Time-varying drift is applied
    via :class:`~ti_adc.mismatch.MismatchProfile` during simulation.
    """

    num_channels: int = 4
    bits: int = DEFAULT_BITS
    vrefp: float = 1.0
    vrefn: float = 0.0
    fs_hz: float = DEFAULT_FS_HZ
    enable_cal: bool = False
    gain: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    offset_v: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    timing_skew_s: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    gain_cal: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    offset_cal_v: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    timing_cal_s: NDArray[np.float64] = field(default_factory=lambda: np.array([]))

    def __post_init__(self) -> None:
        if self.num_channels < 2:
            msg = f"num_channels must be >= 2, got {self.num_channels}"
            raise ValueError(msg)
        object.__setattr__(self, "gain", self._filled(self.gain, 1.0))
        object.__setattr__(self, "offset_v", self._filled(self.offset_v, 0.0))
        object.__setattr__(self, "timing_skew_s", self._filled(self.timing_skew_s, 0.0))
        object.__setattr__(self, "gain_cal", self._filled(self.gain_cal, 1.0))
        object.__setattr__(self, "offset_cal_v", self._filled(self.offset_cal_v, 0.0))
        object.__setattr__(self, "timing_cal_s", self._filled(self.timing_cal_s, 0.0))

    def _filled(self, values: NDArray[np.float64], default: float) -> NDArray[np.float64]:
        if values.size == 0:
            return np.full(self.num_channels, default, dtype=np.float64)
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
        _validate_length("array", arr, self.num_channels)
        return arr.copy()

    @property
    def channel_fs_hz(self) -> float:
        """Per-channel sample rate ``fs / M``."""
        return self.fs_hz / self.num_channels

    @property
    def max_code(self) -> int:
        """Full-scale digital code."""
        return (1 << self.bits) - 1

    @property
    def lsb(self) -> float:
        """Ideal LSB size in volts."""
        return (self.vrefp - self.vrefn) / self.max_code

    @property
    def num_codes(self) -> int:
        """Number of quantizer levels."""
        return self.max_code + 1

    def channel_adc_config(self, channel: int) -> AdcConfig:
        """Build a single-channel :class:`AdcConfig` for analysis helpers."""
        return AdcConfig(
            bits=self.bits,
            vrefp=self.vrefp,
            vrefn=self.vrefn,
            fs_hz=self.fs_hz,
            gain=float(self.gain[channel]),
            offset_v=float(self.offset_v[channel]),
        )

    def effective_adc_config(self) -> AdcConfig:
        """Nominal ADC config for FFT/INL helpers on the muxed output stream."""
        return AdcConfig(
            bits=self.bits,
            vrefp=self.vrefp,
            vrefn=self.vrefn,
            fs_hz=self.fs_hz,
        )


def uniform_mismatch(
    num_channels: int,
    *,
    gain: float = 1.0,
    offset_v: float = 0.0,
    timing_skew_s: float = 0.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Return constant per-channel mismatch arrays."""
    m = num_channels
    return (
        np.full(m, gain, dtype=np.float64),
        np.full(m, offset_v, dtype=np.float64),
        np.full(m, timing_skew_s, dtype=np.float64),
    )


def staggered_mismatch(
    num_channels: int,
    *,
    gain_delta: float = 0.01,
    offset_delta_v: float = 5e-3,
    timing_skew_delta_s: float = 50e-12,
    center_channel: bool = True,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Linear stagger across channels (channel 0 lowest, M-1 highest)."""
    idx = np.arange(num_channels, dtype=np.float64)
    if center_channel and num_channels > 1:
        idx = idx - 0.5 * (num_channels - 1)
    gain = 1.0 + gain_delta * idx
    offset_v = offset_delta_v * idx
    timing_skew_s = timing_skew_delta_s * idx
    return gain, offset_v, timing_skew_s


def preset_ideal(
    num_channels: int = 4,
    *,
    bits: int = DEFAULT_BITS,
    fs_hz: float = DEFAULT_FS_HZ,
) -> TiAdcConfig:
    """Ideal TI array (no mismatch, correction disabled)."""
    gain, offset_v, skew = uniform_mismatch(num_channels)
    return TiAdcConfig(
        num_channels=num_channels,
        bits=bits,
        fs_hz=fs_hz,
        gain=gain,
        offset_v=offset_v,
        timing_skew_s=skew,
        enable_cal=False,
    )


def preset_gain_mismatch(num_channels: int = 4, *, gain_span: float = 0.02) -> TiAdcConfig:
    """Static gain mismatch only (±span/2 across channels)."""
    gain, offset_v, skew = staggered_mismatch(
        num_channels,
        gain_delta=gain_span / max(num_channels - 1, 1),
        offset_delta_v=0.0,
        timing_skew_delta_s=0.0,
    )
    return TiAdcConfig(
        num_channels=num_channels,
        gain=gain,
        offset_v=offset_v,
        timing_skew_s=skew,
    )


def preset_offset_mismatch(num_channels: int = 4, *, offset_span_v: float = 20e-3) -> TiAdcConfig:
    """Static offset mismatch only."""
    gain, offset_v, skew = staggered_mismatch(
        num_channels,
        gain_delta=0.0,
        offset_delta_v=offset_span_v / max(num_channels - 1, 1),
        timing_skew_delta_s=0.0,
    )
    return TiAdcConfig(num_channels=num_channels, gain=gain, offset_v=offset_v, timing_skew_s=skew)


def preset_skew_mismatch(num_channels: int = 4, *, skew_span_s: float = 200e-12) -> TiAdcConfig:
    """Static timing-skew mismatch only."""
    gain, offset_v, skew = staggered_mismatch(
        num_channels,
        gain_delta=0.0,
        offset_delta_v=0.0,
        timing_skew_delta_s=skew_span_s / max(num_channels - 1, 1),
    )
    return TiAdcConfig(num_channels=num_channels, gain=gain, offset_v=offset_v, timing_skew_s=skew)


def preset_combined_impaired(num_channels: int = 4) -> TiAdcConfig:
    """Static gain, offset, and skew mismatch (default impaired preset)."""
    gain, offset_v, skew = staggered_mismatch(
        num_channels,
        gain_delta=0.01,
        offset_delta_v=5e-3,
        timing_skew_delta_s=50e-12,
    )
    return TiAdcConfig(num_channels=num_channels, gain=gain, offset_v=offset_v, timing_skew_s=skew)


def preset_mismatch_corrected(cfg: TiAdcConfig) -> TiAdcConfig:
    """Per-channel correction coeffs that invert static mismatch (bring-up preset)."""
    return TiAdcConfig(
        num_channels=cfg.num_channels,
        bits=cfg.bits,
        vrefp=cfg.vrefp,
        vrefn=cfg.vrefn,
        fs_hz=cfg.fs_hz,
        gain=cfg.gain.copy(),
        offset_v=cfg.offset_v.copy(),
        timing_skew_s=cfg.timing_skew_s.copy(),
        gain_cal=cfg.gain.copy(),
        offset_cal_v=cfg.offset_v.copy(),
        timing_cal_s=cfg.timing_skew_s.copy(),
        enable_cal=True,
    )


def default_noise() -> AdcNoiseConfig:
    """Default non-ideal noise matching adc-model CLI defaults."""
    return AdcNoiseConfig(
        sigma_thermal_v=250e-6,
        jitter_rms_s=500e-15,
        nonlinearity_a3=-0.002,
        dnl_sigma_lsb=0.08,
        noise_seed=1,
    )


def ideal_noise() -> AdcNoiseConfig:
    """Quantizer-limited noise configuration."""
    return AdcNoiseConfig()
