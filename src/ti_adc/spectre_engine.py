"""Cadence Spectre runners for TI ADC testbenches."""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from adc_model.config import AdcNoiseConfig
from adc_model.io import write_waveform_csv
from adc_model.spectre_engine import (
    _PARAMETER_LINE,
    _effective_samples_per_code,
    _spectre_parameter_value,
    cleanup_spectre_artifacts,
    read_spectre_nutascii,
)

from ti_adc.adc_model_root import adc_model_root
from ti_adc.config import TiAdcConfig
from ti_adc.ti_io import prepare_ti_spectre_waveform, spectre_save_statement

_TI_ADC_INCLUDE = re.compile(
    r'include\s+"\./testbench/spectre/ti_adc_include\.scs"\s*',
    re.IGNORECASE,
)
_MAX_CHANNELS = 4


def _absolutize_ti_includes(text: str, repo_root: Path) -> str:
    """Point ahdl_include at absolute Verilog-A paths (adc-model git dep + this repo)."""
    cfg_va = (adc_model_root() / "veriloga/configurable_adc.va").resolve()
    ch_va = (repo_root / "veriloga/ti_channel_adc.va").resolve()
    text = _TI_ADC_INCLUDE.sub(
        f'ahdl_include "{cfg_va}"\n'
        f'ahdl_include "{ch_va}"\n',
        text,
    )
    return text


def _render_channel_instances(cfg: TiAdcConfig, noise: AdcNoiseConfig) -> str:
    """Return Spectre instances for ``M`` interleaved channels (M <= 4)."""
    if cfg.num_channels > _MAX_CHANNELS:
        msg = f"Spectre TI templates support up to {_MAX_CHANNELS} channels."
        raise ValueError(msg)

    m = cfg.num_channels
    fs_ch = cfg.channel_fs_hz
    enable_cal = 1 if cfg.enable_cal else 0
    lines: list[str] = []

    for k in range(m):
        skew = float(cfg.timing_skew_s[k])
        if cfg.enable_cal:
            skew -= float(cfg.timing_cal_s[k])
        delay = k / cfg.fs_hz + skew
        lines.append(
            f'VCLK{k} (clk{k} 0) vsource type=pulse val0=0 val1=1 '
            f"period={_spectre_parameter_value('p', m / cfg.fs_hz)} "
            f"delay={_spectre_parameter_value('d', delay)} "
            f"rise=100p fall=100p width={_spectre_parameter_value('w', 0.5 * m / cfg.fs_hz)}"
        )
        lines.append(
            f"ADC{k} (vin clk{k} v_code{k} vdd vss) ti_channel_adc "
            f"BITS={cfg.bits} VREFP={cfg.vrefp} VREFN={cfg.vrefn} "
            f"GAIN={cfg.gain[k]} OFFSET_V={cfg.offset_v[k]} "
            f"GAIN_CAL={cfg.gain_cal[k]} OFFSET_CAL_V={cfg.offset_cal_v[k]} "
            f"ENABLE_CAL={enable_cal} "
            f"SIGMA_THERMAL={noise.sigma_thermal_v} JITTER_RMS={noise.jitter_rms_s} "
            f"NONLINEARITY_A2={noise.nonlinearity_a2} NONLINEARITY_A3={noise.nonlinearity_a3} "
            f"DNL_SIGMA_LSB={noise.dnl_sigma_lsb} NOISE_SEED={noise.noise_seed + k}"
        )
    lines.append(
        f"VCLK_MUX (clk_mux 0) vsource type=pulse val0=0 val1=1 "
        f"period={_spectre_parameter_value('p', 1 / cfg.fs_hz)} "
        f"rise=100p fall=100p width={_spectre_parameter_value('w', 0.5 / cfg.fs_hz)}"
    )
    return "\n".join(lines)


def render_ti_spectre_netlist(
    template_path: Path,
    *,
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
    repo_root: Path,
    samples_per_code: int | None = None,
    num_samples: int | None = None,
    coherent_bin: int | None = None,
) -> str:
    """Render a TI Spectre testbench for the given configuration."""
    enable_cal = 1 if cfg.enable_cal else 0
    overrides: dict[str, str] = {
        "bits": _spectre_parameter_value("bits", cfg.bits),
        "vrefp": _spectre_parameter_value("vrefp", cfg.vrefp),
        "vrefn": _spectre_parameter_value("vrefn", cfg.vrefn),
        "enable_cal": str(enable_cal),
        "fs": _spectre_parameter_value("fs", cfg.fs_hz),
        "num_channels": _spectre_parameter_value("num_channels", cfg.num_channels),
        "sigma_thermal": _spectre_parameter_value("sigma_thermal", noise.sigma_thermal_v),
        "jitter_rms": _spectre_parameter_value("jitter_rms", noise.jitter_rms_s),
        "nonlinearity_a2": _spectre_parameter_value("nonlinearity_a2", noise.nonlinearity_a2),
        "nonlinearity_a3": _spectre_parameter_value("nonlinearity_a3", noise.nonlinearity_a3),
        "dnl_sigma_lsb": _spectre_parameter_value("dnl_sigma_lsb", noise.dnl_sigma_lsb),
        "noise_seed": _spectre_parameter_value("noise_seed", noise.noise_seed),
    }

    for k in range(cfg.num_channels):
        overrides[f"gain_{k}"] = _spectre_parameter_value(f"gain_{k}", cfg.gain[k])
        overrides[f"offset_v_{k}"] = _spectre_parameter_value(f"offset_v_{k}", cfg.offset_v[k])
        overrides[f"gain_cal_{k}"] = _spectre_parameter_value(f"gain_cal_{k}", cfg.gain_cal[k])
        overrides[f"offset_cal_{k}"] = _spectre_parameter_value(
            f"offset_cal_{k}",
            cfg.offset_cal_v[k],
        )
        overrides[f"timing_skew_{k}"] = _spectre_parameter_value(
            f"timing_skew_{k}",
            cfg.timing_skew_s[k],
        )

    if samples_per_code is not None:
        effective_spc = _effective_samples_per_code(samples_per_code, noise)
        overrides["samples_per_code"] = _spectre_parameter_value(
            "samples_per_code",
            effective_spc,
        )
        if template_path.stem == "ti_static_inl_dnl":
            overrides["num_samples"] = _spectre_parameter_value(
                "num_samples",
                cfg.num_codes * effective_spc,
            )
    if num_samples is not None:
        overrides["num_samples"] = _spectre_parameter_value("num_samples", num_samples)
    if coherent_bin is not None:
        overrides["coherent_bin"] = _spectre_parameter_value("coherent_bin", coherent_bin)

    text = template_path.read_text(encoding="utf-8")

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in overrides:
            return f"parameters {name}={overrides[name]}"
        return match.group(0)

    text = _PARAMETER_LINE.sub(_replace, text)
    text = text.replace("// TI_CHANNEL_INSTANCES", _render_channel_instances(cfg, noise))
    text = text.replace("// TI_SAVE", spectre_save_statement(cfg))
    text = _absolutize_ti_includes(text, repo_root)
    return text


def run_ti_spectre_testbench(
    *,
    repo_root: Path,
    scs_path: Path,
    output_csv: Path,
    log_path: Path,
    cfg: TiAdcConfig,
    noise: AdcNoiseConfig,
    samples_per_code: int | None = None,
    num_samples: int | None = None,
    coherent_bin: int | None = None,
    cleanup_ahdl_cache: bool = False,
    golden_export: bool = False,
) -> Path:
    """Run TI Spectre testbench, mux channels, and write analysis CSV."""
    rendered_dir = log_path.parent / "netlists"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    run_scs_path = rendered_dir / scs_path.name
    run_scs_path.write_text(
        render_ti_spectre_netlist(
            scs_path,
            cfg=cfg,
            noise=noise,
            repo_root=repo_root,
            samples_per_code=samples_per_code,
            num_samples=num_samples,
            coherent_bin=coherent_bin,
        ),
        encoding="utf-8",
    )

    max_samples = num_samples
    if max_samples is None and scs_path.stem == "ti_static_inl_dnl":
        spc = samples_per_code or 4
        spc = _effective_samples_per_code(spc, noise)
        max_samples = cfg.num_codes * spc

    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    run_dir = log_path.parent.resolve()
    netlist_stem = scs_path.stem
    cleanup_spectre_artifacts(repo_root.resolve(), netlist_stem)

    raw_path = run_dir / f"{netlist_stem}.nutascii"
    cmd = [
        "spectre",
        str(run_scs_path.resolve()),
        "-format",
        "nutascii",
        "-raw",
        str(raw_path.resolve()),
    ]
    header = [
        f"# TI Spectre simulation log: {scs_path.name}",
        f"Generated: {datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Spectre run directory: {run_dir}",
        f"Command: {' '.join(cmd)}",
        f"Raw output: {raw_path.resolve()}",
        "",
    ]
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("\n".join(header))
        log_file.flush()
        subprocess.run(
            cmd,
            check=True,
            cwd=run_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if cleanup_ahdl_cache:
        cleanup_spectre_artifacts(run_dir, netlist_stem, remove_ahdl_cache=True)

    static_capture = scs_path.stem == "ti_static_inl_dnl"
    raw_waveform = read_spectre_nutascii(raw_path)
    muxed = prepare_ti_spectre_waveform(
        raw_waveform,
        cfg,
        max_samples=max_samples,
        static_capture=static_capture,
        noise=noise,
        golden_export=golden_export,
    )
    write_waveform_csv(output_csv, muxed)
    return log_path.resolve()
