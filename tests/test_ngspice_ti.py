"""Tests for TI ngspice netlist rendering and Python mux post-process."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from adc_model.config import AdcNoiseConfig

from adc_model.io import read_waveform_csv

from ti_adc.analysis import compute_ti_dynamic_metrics, compute_ti_inl_dnl
from ti_adc.config import DEFAULT_FS_HZ, preset_ideal, preset_skew_mismatch
from ti_adc.ngspice_engine import (
    read_ti_ngspice_wrdata,
    render_ti_dynamic_netlist,
    render_ti_static_netlist,
    run_ti_ngspice_testbench,
)
from ti_adc.ti_io import prepare_ti_ngspice_waveform
from ti_adc.ti_model import simulate_ti_dynamic, simulate_ti_static

GOLDEN_SNDR_TOLERANCE_DB = 0.5
GOLDEN_DNL_TOLERANCE_LSB = 0.05
GOLDEN_INL_TOLERANCE_LSB = 0.15
E2E_DNL_TOLERANCE_LSB = 1.5
E2E_INL_TOLERANCE_LSB = 1.0
E2E_SNDR_TOLERANCE_DB = 2.0


def test_render_ti_static_netlist_has_four_channels() -> None:
    """Static TI netlists should instantiate four phase-shifted channel chains."""
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig()
    text = render_ti_static_netlist(cfg, noise, samples_per_code=4)
    assert "Bgain0" in text and "Bgain3" in text
    assert "Vclk0" in text and "Vclk3" in text
    assert "Bnonlin0" in text and "Bquant0" in text
    assert "wrdata" in text


def test_render_ti_static_netlist_stops_before_quantize_when_noisy() -> None:
    """Noisy static netlists should export ``v_nl`` for Python edge finalize."""
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig(sigma_thermal_v=250e-6, dnl_sigma_lsb=0.08)
    text = render_ti_static_netlist(cfg, noise, samples_per_code=4)
    assert "Bquant0" not in text
    assert "v(v_nl0)" in text


def test_render_ti_dynamic_netlist_uses_inline_pwl() -> None:
    """Dynamic TI netlists should include a coherent inline PWL stimulus."""
    cfg = preset_ideal(num_channels=4, fs_hz=1.0e6)
    noise = AdcNoiseConfig()
    text = render_ti_dynamic_netlist(
        cfg,
        noise,
        num_samples=128,
        fin_hz=100_000.0,
    )
    assert "Vin vin 0 PWL(" in text
    assert ".param FIN=100000.0" in text


def test_read_ti_ngspice_wrdata_parses_multi_channel_columns(tmp_path: Path) -> None:
    """Multi-channel wrdata exports should parse into per-channel probes."""
    rows = np.array(
        [
            [0.0, 0.1, 0.0, 0.11, 1.0, 0.12],
            [1e-9, 0.2, 1.0, 0.21, 0.0, 0.22],
        ]
    )
    path = tmp_path / "ti.wrdata"
    np.savetxt(path, rows)
    data = read_ti_ngspice_wrdata(
        path,
        num_channels=2,
        noise=AdcNoiseConfig(sigma_thermal_v=250e-6),
    )
    assert len(data["time"]) == 2
    assert np.isclose(data["vin"][0], 0.1)
    assert np.isclose(data["clk1"][0], 1.0)
    assert np.isclose(data["v_nl1"][1], 0.22)


def test_prepare_ti_ngspice_waveform_matches_python_static() -> None:
    """Golden ngspice mux should match Python on an ideal static capture."""
    cfg = preset_ideal(num_channels=4, fs_hz=1.0e6)
    noise = AdcNoiseConfig()
    py = simulate_ti_static(cfg, samples_per_code=4, noise=noise)
    waveform = {"time": py["time"], "vin": py["vin"]}
    sp = prepare_ti_ngspice_waveform(
        waveform,
        cfg,
        max_samples=len(py["code"]),
        static_capture=True,
        noise=noise,
        golden_export=True,
    )
    np.testing.assert_allclose(sp["code"], py["code"])


def test_prepare_ti_ngspice_waveform_matches_python_dynamic() -> None:
    """Golden ngspice mux should match Python on a skewed dynamic capture."""
    cfg = replace(preset_skew_mismatch(num_channels=4), fs_hz=1.0e6)
    noise = AdcNoiseConfig()
    num_samples = 256
    coherent_bin = 97
    py = simulate_ti_dynamic(
        cfg,
        num_samples=num_samples,
        coherent_bin=coherent_bin,
        noise=noise,
    )
    waveform = {"time": py["time"], "vin": py["vin"]}
    sp = prepare_ti_ngspice_waveform(
        waveform,
        cfg,
        max_samples=num_samples,
        static_capture=False,
        noise=noise,
        golden_export=True,
    )
    fin_hz = coherent_bin * cfg.fs_hz / num_samples
    py_report = compute_ti_dynamic_metrics(py["code"].astype(int), cfg, fin_hz=fin_hz)
    sp_report = compute_ti_dynamic_metrics(sp["code"].astype(int), cfg, fin_hz=fin_hz)
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < GOLDEN_SNDR_TOLERANCE_DB
    np.testing.assert_allclose(sp["code"], py["code"])


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_run_ti_ngspice_static_smoke(tmp_path: Path) -> None:
    """Run a reduced TI static netlist through ngspice when available."""
    repo = Path(__file__).resolve().parents[1]
    cfg = replace(preset_ideal(num_channels=4), bits=8, fs_hz=1.0e6)
    noise = AdcNoiseConfig()
    output_dir = tmp_path / "ideal"
    run_ti_ngspice_testbench(
        repo_root=repo,
        template_path=repo / "testbench/ngspice/ti_static_inl_dnl.cir",
        output_csv=output_dir / "ti_static_waveform.csv",
        log_path=output_dir / "logs/ngspice_static.log",
        cfg=cfg,
        noise=noise,
        samples_per_code=2,
    )
    csv_path = output_dir / "ti_static_waveform.csv"
    assert csv_path.is_file()
    py = simulate_ti_static(cfg, samples_per_code=2, noise=noise)
    sp_codes = np.loadtxt(csv_path, delimiter=",", skiprows=1, usecols=(4,))
    assert len(sp_codes) == len(py["code"])


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_run_ti_ngspice_ideal_dynamic_matches_python(tmp_path: Path) -> None:
    """End-to-end ideal dynamic ngspice run should match Python SNDR."""
    repo = Path(__file__).resolve().parents[1]
    cfg = preset_ideal(num_channels=4, fs_hz=1.0e6)
    noise = AdcNoiseConfig()
    num_samples = 512
    coherent_bin = 127
    fin_hz = coherent_bin * cfg.fs_hz / num_samples
    output_dir = tmp_path / "ideal"
    run_ti_ngspice_testbench(
        repo_root=repo,
        template_path=repo / "testbench/ngspice/ti_dynamic_spectrum.cir",
        output_csv=output_dir / "ti_dynamic_waveform.csv",
        log_path=output_dir / "logs/ngspice_dynamic.log",
        cfg=cfg,
        noise=noise,
        num_samples=num_samples,
        fin_hz=fin_hz,
    )
    py = simulate_ti_dynamic(
        cfg,
        num_samples=num_samples,
        coherent_bin=coherent_bin,
        noise=noise,
    )
    sp = read_waveform_csv(output_dir / "ti_dynamic_waveform.csv")
    sp_codes = sp["code"].astype(np.int64)
    py_report = compute_ti_dynamic_metrics(py["code"].astype(int), cfg, fin_hz=fin_hz)
    sp_report = compute_ti_dynamic_metrics(sp_codes, cfg, fin_hz=fin_hz)
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < GOLDEN_SNDR_TOLERANCE_DB


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_run_ti_ngspice_noisy_static_matches_python(tmp_path: Path) -> None:
    """Noisy static E2E ngspice should agree with Python within impaired tolerance."""
    repo = Path(__file__).resolve().parents[1]
    cfg = preset_ideal(num_channels=4, fs_hz=1.0e6)
    noise = AdcNoiseConfig(
        sigma_thermal_v=250e-6,
        jitter_rms_s=500e-15,
        nonlinearity_a3=-0.002,
        dnl_sigma_lsb=0.08,
        noise_seed=1,
    )
    output_dir = tmp_path / "ideal"
    run_ti_ngspice_testbench(
        repo_root=repo,
        template_path=repo / "testbench/ngspice/ti_static_inl_dnl.cir",
        output_csv=output_dir / "ti_static_waveform.csv",
        log_path=output_dir / "logs/ngspice_static.log",
        cfg=cfg,
        noise=noise,
        samples_per_code=4,
    )
    py = simulate_ti_static(cfg, samples_per_code=4, noise=noise)
    sp = read_waveform_csv(output_dir / "ti_static_waveform.csv")
    py_lin = compute_ti_inl_dnl(py["vin"], py["code"].astype(int), cfg)
    sp_lin = compute_ti_inl_dnl(sp["vin"], sp["code"].astype(int), cfg)
    assert abs(sp_lin.max_dnl_lsb - py_lin.max_dnl_lsb) < E2E_DNL_TOLERANCE_LSB
    assert abs(sp_lin.max_inl_lsb - py_lin.max_inl_lsb) < E2E_INL_TOLERANCE_LSB
