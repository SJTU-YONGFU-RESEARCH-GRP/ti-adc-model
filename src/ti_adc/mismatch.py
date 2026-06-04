"""Static and time-varying TI channel mismatch profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ti_adc.config import TiAdcConfig


class MismatchKind(str, Enum):
    """How mismatch evolves over the capture."""

    STATIC = "static"
    TIME_VARYING = "time_varying"


class DriftWaveform(str, Enum):
    """Waveform used for time-varying mismatch."""

    SINUSOID = "sinusoid"
    RAMP = "ramp"
    STEP = "step"


@dataclass(frozen=True)
class MismatchProfile:
    """Optional time-varying overlay on top of :class:`TiAdcConfig` static arrays.

    Static terms in ``TiAdcConfig`` (``gain``, ``offset_v``, ``timing_skew_s``)
    are always the baseline. When ``kind`` is ``TIME_VARYING``, the deltas below
    modulate that baseline versus sample index ``n`` or time ``t``.

    Attributes:
        kind: Static-only or time-varying overlay.
        gain_drift_frac: Peak fractional gain modulation per channel (e.g. 0.01 = 1 %).
        offset_drift_v: Peak offset modulation in volts.
        skew_drift_s: Peak timing-skew modulation in seconds.
        drift_freq_hz: Frequency of sinusoidal drift; ignored for ramp/step.
        drift_waveform: Sinusoid, ramp across capture, or step at ``step_sample``.
        step_sample: Sample index where step drift switches (STEP only).
        channel_phases: Per-channel phase (rad) for sinusoidal drift.
    """

    kind: MismatchKind = MismatchKind.STATIC
    gain_drift_frac: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    offset_drift_v: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    skew_drift_s: NDArray[np.float64] = field(default_factory=lambda: np.array([]))
    drift_freq_hz: float = 10e3
    drift_waveform: DriftWaveform = DriftWaveform.SINUSOID
    step_sample: int = 0
    channel_phases: NDArray[np.float64] = field(default_factory=lambda: np.array([]))

    def __post_init__(self) -> None:
        if self.kind == MismatchKind.STATIC:
            return
        if self.drift_freq_hz < 0.0:
            msg = "drift_freq_hz must be non-negative."
            raise ValueError(msg)


@dataclass(frozen=True)
class ChannelMismatchSample:
    """Effective mismatch for one muxed sample."""

    gain: float
    offset_v: float
    timing_skew_s: float


def _filled_channels(
    values: NDArray[np.float64],
    num_channels: int,
    default: float = 0.0,
) -> NDArray[np.float64]:
    if values.size == 0:
        return np.zeros(num_channels, dtype=np.float64)
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if len(arr) != num_channels:
        msg = f"Expected length {num_channels}, got {len(arr)}"
        raise ValueError(msg)
    return arr


def _drift_factor(
    profile: MismatchProfile,
    cfg: TiAdcConfig,
    sample_index: int,
    channel: int,
) -> float:
    """Return modulation in [-1, 1] for time-varying profiles."""
    if profile.kind == MismatchKind.STATIC:
        return 0.0

    m = cfg.num_channels
    phases = _filled_channels(profile.channel_phases, m, default=0.0)
    phase = float(phases[channel])

    if profile.drift_waveform == DriftWaveform.STEP:
        return 1.0 if sample_index >= profile.step_sample else -1.0

    t = sample_index / cfg.fs_hz
    if profile.drift_waveform == DriftWaveform.RAMP:
        if cfg.fs_hz <= 0.0:
            return 0.0
        # One ramp from -1 to +1 over 1 s of wall time (scaled by capture length).
        return float(np.clip(2.0 * t - 1.0, -1.0, 1.0))

    # Sinusoid
    if profile.drift_freq_hz == 0.0:
        return 0.0
    return float(np.sin(2.0 * np.pi * profile.drift_freq_hz * t + phase))


def effective_mismatch(
    cfg: TiAdcConfig,
    profile: MismatchProfile,
    sample_index: int,
    channel: int,
) -> ChannelMismatchSample:
    """Return gain, offset, and timing skew for mux sample ``sample_index`` on ``channel``."""
    mod = _drift_factor(profile, cfg, sample_index, channel)
    gain_drift = _filled_channels(profile.gain_drift_frac, cfg.num_channels)
    offset_drift = _filled_channels(profile.offset_drift_v, cfg.num_channels)
    skew_drift = _filled_channels(profile.skew_drift_s, cfg.num_channels)

    gain = float(cfg.gain[channel]) * (1.0 + mod * gain_drift[channel])
    offset_v = float(cfg.offset_v[channel]) + mod * float(offset_drift[channel])
    timing_skew_s = float(cfg.timing_skew_s[channel]) + mod * float(skew_drift[channel])

    return ChannelMismatchSample(
        gain=gain,
        offset_v=offset_v,
        timing_skew_s=timing_skew_s,
    )


def preset_static_profile() -> MismatchProfile:
    """No time-varying overlay."""
    return MismatchProfile(kind=MismatchKind.STATIC)


def preset_slow_drift(
    num_channels: int = 4,
    *,
    gain_drift_frac: float = 0.005,
    offset_drift_v: float = 2e-3,
    skew_drift_s: float = 20e-12,
    drift_freq_hz: float = 15e3,
) -> MismatchProfile:
    """Sinusoidal drift overlay (typical background tracking experiment)."""
    m = num_channels
    phases = np.linspace(0.0, np.pi, m, endpoint=False, dtype=np.float64)
    return MismatchProfile(
        kind=MismatchKind.TIME_VARYING,
        gain_drift_frac=np.full(m, gain_drift_frac, dtype=np.float64),
        offset_drift_v=np.full(m, offset_drift_v, dtype=np.float64),
        skew_drift_s=np.full(m, skew_drift_s, dtype=np.float64),
        drift_freq_hz=drift_freq_hz,
        drift_waveform=DriftWaveform.SINUSOID,
        channel_phases=phases,
    )


def preset_step_drift(
    num_channels: int = 4,
    *,
    step_sample: int = 4096,
    gain_drift_frac: float = 0.01,
) -> MismatchProfile:
    """Step change in mismatch mid-capture (stress for tracking algorithms)."""
    m = num_channels
    return MismatchProfile(
        kind=MismatchKind.TIME_VARYING,
        gain_drift_frac=np.full(m, gain_drift_frac, dtype=np.float64),
        drift_waveform=DriftWaveform.STEP,
        step_sample=step_sample,
    )
