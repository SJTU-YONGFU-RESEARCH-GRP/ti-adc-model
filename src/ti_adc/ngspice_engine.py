"""ngspice testbench generation and execution for TI ADC arrays."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from adc_model.config import AdcNoiseConfig
from adc_model.io import write_waveform_csv
from adc_model.ngspice_engine import (
    _apply_input_jitter,
    _effective_samples_per_code,
    _needs_python_post_process,
    _render_pwl_source,
    _substitute_run_paths,
)

from ti_adc.config import TiAdcConfig
from ti_adc.mismatch import MismatchProfile, preset_static_profile
from ti_adc.ti_io import prepare_ti_ngspice_waveform

_MAX_CHANNELS = 4


@dataclass(frozen=True)
class TiNgspiceRunResult:
    """Artifacts produced by a TI ngspice simulation."""

    netlist_path: Path
    wrdata_path: Path
    log_path: Path
    csv_path: Path


def _validate_channel_count(cfg: TiAdcConfig) -> None:
    if cfg.num_channels > _MAX_CHANNELS:
        msg = f"ngspice TI templates support up to {_MAX_CHANNELS} channels."
        raise ValueError(msg)


def _ti_global_param_block(cfg: TiAdcConfig, noise: AdcNoiseConfig) -> str:
    """Return shared SPICE .param lines for a TI array netlist."""
    max_code = cfg.max_code
    vfs = cfg.vrefp - cfg.vrefn
    vcm = 0.5 * (cfg.vrefp + cfg.vrefn)
    return f"""
.param BITS={cfg.bits}
.param VREFP={cfg.vrefp}
.param VREFN={cfg.vrefn}
.param MAXCODE={max_code}
.param LSB=({cfg.vrefp}-{cfg.vrefn})/{max_code}
.param VCM={vcm}
.param VFS={vfs}
.param FS={cfg.fs_hz}
.param NUM_CHANNELS={cfg.num_channels}
.param SIGMA_THERMAL={noise.sigma_thermal_v}
.param NONLINEARITY_A2={noise.nonlinearity_a2}
.param NONLINEARITY_A3={noise.nonlinearity_a3}
.param JITTER_RMS={noise.jitter_rms_s}
.param DNL_SIGMA_LSB={noise.dnl_sigma_lsb}
.param NOISE_SEED={noise.noise_seed}
""".strip()


def _render_ti_channel_instances(cfg: TiAdcConfig, noise: AdcNoiseConfig) -> str:
    """Return per-channel gain/nonlinearity B-sources, clocks, and optional quantizers."""
    m = cfg.num_channels
    fs = cfg.fs_hz
    period = m / fs
    width = 0.5 * period
    stop_before_quantize = _needs_python_post_process(noise)
    lines: list[str] = []

    for k in range(m):
        skew = float(cfg.timing_skew_s[k])
        if cfg.enable_cal:
            skew -= float(cfg.timing_cal_s[k])
        delay = k / fs + skew
        gain = float(cfg.gain[k])
        offset = float(cfg.offset_v[k])
        lines.append(
            f"Vclk{k} clk{k} 0 PULSE(0 1 {delay:.12e} 100p 100p "
            f"{width:.12e} {period:.12e})"
        )
        lines.append(f"Bgain{k} v_gain{k} 0 V={{ {gain:.12e}*V(vin)+{offset:.12e} }}")
        lines.append(
            f"Bnonlin{k} v_nl{k} 0 V={{ "
            f"V(v_gain{k}) + VFS*(NONLINEARITY_A2*pow((V(v_gain{k})-VCM)/VFS,2) "
            f"+ NONLINEARITY_A3*pow((V(v_gain{k})-VCM)/VFS,3)) "
            f"}}"
        )
        if not stop_before_quantize:
            lines.append(
                f"Bquant{k} v_code{k} 0 V={{ "
                f"( V(v_nl{k}) <= VREFN ? 0 : "
                f"V(v_nl{k}) >= VREFP ? MAXCODE : "
                f"floor((V(v_nl{k})-VREFN)/LSB+0.5) ) * LSB "
                f"}}"
            )
    return "\n".join(lines)


def _wrdata_signal_list(cfg: TiAdcConfig, noise: AdcNoiseConfig) -> str:
    """Return ngspice ``wrdata`` probe list for a TI capture."""
    stop_before_quantize = _needs_python_post_process(noise)
    probes = ["v(vin)"]
    for k in range(cfg.num_channels):
        probes.append(f"v(clk{k})")
        analog = f"v_nl{k}" if stop_before_quantize else f"v_code{k}"
        probes.append(f"v({analog})")
    return " ".join(probes)


def read_ti_ngspice_wrdata(
    path: Path,
    *,
    num_channels: int,
    noise: AdcNoiseConfig | None = None,
) -> dict[str, NDArray[np.float64]]:
    """Read a multi-channel TI ngspice ``wrdata`` file.

    Args:
        path: wrdata path written with ``wr_singlescale``.
        num_channels: Number of interleaved channels ``M``.
        noise: Noise config used for the run (selects ``v_nl`` vs ``v_code`` columns).

    Returns:
        Waveform dictionary with ``time``, ``vin``, ``clk{k}``, and channel analog probes.
    """
    noise_cfg = noise or AdcNoiseConfig()
    analog_prefix = "v_nl" if _needs_python_post_process(noise_cfg) else "v_code"
    table = np.loadtxt(path)
    if table.ndim == 1:
        table = table.reshape(1, -1)
    expected_cols = 1 + 1 + num_channels * 2
    if table.shape[1] < 2:
        msg = f"Expected at least 2 columns in wrdata file, got {table.shape[1]}."
        raise ValueError(msg)

    result: dict[str, NDArray[np.float64]] = {
        "time": table[:, 0],
        "vin": table[:, 1],
    }
    col = 2
    for k in range(num_channels):
        if col >= table.shape[1]:
            break
        result[f"clk{k}"] = table[:, col]
        col += 1
        if col >= table.shape[1]:
            break
        result[f"{analog_prefix}{k}"] = table[:, col]
        col += 1

    if table.shape[1] != expected_cols:
        # Golden mux only needs ``time`` and ``vin``; tolerate short exports.
        if "vin" not in result:
            msg = f"wrdata missing vin column (shape {table.shape})."
            raise ValueError(msg)
    return result


def render_ti_static_netlist(
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
    *,
    samples_per_code: int,
) -> str:
    """Render the TI static INL/DNL ngspice netlist."""
    _validate_channel_count(cfg)
    samples_per_code = _effective_samples_per_code(samples_per_code, noise)
    num_samples = cfg.num_codes * samples_per_code
    dt = 1.0 / cfg.fs_hz
    t_stop = num_samples * dt
    margin = 0.5 * cfg.lsb
    ramp_start = cfg.vrefn + margin
    ramp_end = cfg.vrefp - margin
    time = np.arange(num_samples, dtype=np.float64) * dt
    vin = np.linspace(ramp_start, ramp_end, num_samples)
    rng = np.random.default_rng(noise.noise_seed)
    if not _needs_python_post_process(noise):
        vin = _apply_input_jitter(vin, dt, noise, rng)
    pwl_source = _render_pwl_source(time, vin)

    return f"""
* TI static INL/DNL testbench (ngspice behavioral, channel-wise E2E + TI mux)
* Reference: veriloga/ti_channel_adc.va + ti_adc Python golden model
{_ti_global_param_block(cfg, noise)}
.param SAMPLES_PER_CODE={samples_per_code}
.param NUM_SAMPLES={num_samples}
.param DT={dt:.12e}
.param TSTOP={t_stop:.12e}

.options delmax={dt:.12e} maxstep={dt:.12e}
{pwl_source}
{_render_ti_channel_instances(cfg, noise)}

.control
tran {dt:.12e} {t_stop:.12e}
set wr_singlescale
wrdata $wrdata {_wrdata_signal_list(cfg, noise)}
.endc
.end
""".strip()


def render_ti_dynamic_netlist(
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
    *,
    num_samples: int,
    fin_hz: float,
) -> str:
    """Render the TI dynamic spectrum ngspice netlist."""
    _validate_channel_count(cfg)
    dt = 1.0 / cfg.fs_hz
    t_stop = num_samples * dt
    amplitude = 0.95 * (cfg.vrefp - cfg.vrefn) / 2.0
    mid = 0.5 * (cfg.vrefp + cfg.vrefn)
    time = np.arange(num_samples, dtype=np.float64) * dt
    vin = mid + amplitude * np.sin(2.0 * np.pi * fin_hz * time)
    rng = np.random.default_rng(noise.noise_seed)
    if not _needs_python_post_process(noise):
        vin = _apply_input_jitter(vin, dt, noise, rng)
    pwl_source = _render_pwl_source(time, vin)

    return f"""
* TI dynamic spectrum testbench (ngspice behavioral, channel-wise E2E + TI mux)
* Reference: veriloga/ti_channel_adc.va + ti_adc Python golden model
{_ti_global_param_block(cfg, noise)}
.param FIN={fin_hz}
.param NUM_SAMPLES={num_samples}
.param DT={dt:.12e}
.param TSTOP={t_stop:.12e}

.options delmax={dt:.12e} maxstep={dt:.12e}
{pwl_source}
{_render_ti_channel_instances(cfg, noise)}

.control
tran {dt:.12e} {t_stop:.12e}
set wr_singlescale
wrdata $wrdata {_wrdata_signal_list(cfg, noise)}
.endc
.end
""".strip()


def run_ti_ngspice_testbench(
    *,
    repo_root: Path,
    template_path: Path,
    output_csv: Path,
    log_path: Path,
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
    samples_per_code: int | None = None,
    num_samples: int | None = None,
    fin_hz: float | None = None,
    profile: MismatchProfile | None = None,
    golden_export: bool = False,
) -> Path:
    """Run a TI ngspice testbench, mux channels, and write CSV.

    By default each channel is simulated in ngspice (gain/offset/nonlinearity plus
    optional in-netlist quantizer) and muxed from per-channel ``v_code*`` probes.
    ``golden_export=True`` re-quantizes ``vin`` in Python (Tier-1 vector parity).

    Args:
        repo_root: Repository root (netlists archived under ``output_csv`` parent).
        template_path: Reference netlist stem under ``testbench/ngspice/``.
        output_csv: Destination muxed waveform CSV.
        log_path: Simulator log path.
        cfg: TI ADC configuration.
        noise: Per-channel noise configuration.
        samples_per_code: Static ramp depth per code.
        num_samples: Dynamic capture length.
        fin_hz: Dynamic input tone frequency.
        profile: Optional time-varying mismatch overlay.

    Returns:
        Resolved log path.
    """
    _validate_channel_count(cfg)
    mismatch_profile = profile or preset_static_profile()
    static_capture = template_path.stem == "ti_static_inl_dnl"

    if static_capture:
        spc = samples_per_code or 4
        netlist_text = render_ti_static_netlist(cfg, noise, samples_per_code=spc)
        max_samples = cfg.num_codes * _effective_samples_per_code(spc, noise)
    else:
        if num_samples is None:
            msg = "Dynamic ngspice export requires num_samples."
            raise ValueError(msg)
        if fin_hz is None:
            msg = "Dynamic ngspice export requires fin_hz."
            raise ValueError(msg)
        netlist_text = render_ti_dynamic_netlist(
            cfg,
            noise,
            num_samples=num_samples,
            fin_hz=fin_hz,
        )
        max_samples = num_samples

    netlist_dir = log_path.parent / "netlists"
    netlist_dir.mkdir(parents=True, exist_ok=True)
    netlist_path = netlist_dir / f"{template_path.stem}.cir"
    wrdata_path = log_path.parent / f"{template_path.stem}.wrdata"
    rendered = _substitute_run_paths(netlist_text, wrdata_path)
    netlist_path.write_text(rendered, encoding="utf-8")

    cmd = ["ngspice", "-b", str(netlist_path.resolve())]
    header = [
        f"# TI ngspice simulation log: {template_path.name}",
        f"Generated: {datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Engine: ngspice behavioral TI array (reference: ti_channel_adc.va)",
        f"Repo root: {repo_root.resolve()}",
        f"Netlist: {netlist_path.resolve()}",
        f"Command: {' '.join(cmd)}",
        "",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("\n".join(header))
        log_file.flush()
        subprocess.run(
            cmd,
            check=True,
            cwd=netlist_path.parent,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

    raw_waveform = read_ti_ngspice_wrdata(
        wrdata_path,
        num_channels=cfg.num_channels,
        noise=noise,
    )
    muxed = prepare_ti_ngspice_waveform(
        raw_waveform,
        cfg,
        max_samples=max_samples,
        static_capture=static_capture,
        noise=noise,
        profile=mismatch_profile,
        golden_export=golden_export,
    )
    write_waveform_csv(output_csv, muxed)
    return log_path.resolve()
