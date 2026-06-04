"""Tests for TI Spectre netlist rendering and channel mux."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from adc_model.config import AdcNoiseConfig
from adc_model.spectre_engine import read_spectre_nutascii

from ti_adc.analysis import compute_ti_dynamic_metrics, compute_ti_inl_dnl
from ti_adc.cli_helpers import add_ti_adc_args, add_ti_noise_args, build_ti_adc_config
from ti_adc.config import DEFAULT_FS_HZ, preset_ideal, preset_skew_mismatch
from ti_adc.spectre_engine import render_ti_spectre_netlist
from ti_adc.ti_io import interleave_channel_samples, prepare_ti_spectre_waveform
from ti_adc.ti_model import simulate_ti_dynamic, simulate_ti_static

# PLAN.md Phase 1.4: Spectre end-to-end (VA mux) vs Python reference.
E2E_SNDR_TOLERANCE_DB = 2.0
E2E_DNL_TOLERANCE_LSB = 0.05
E2E_INL_TOLERANCE_LSB = 0.15


def test_ideal_flag_selects_ideal_impairment_preset() -> None:
    """``--ideal`` alone should not leave the default combined mismatch preset."""
    parser = argparse.ArgumentParser()
    add_ti_adc_args(parser)
    add_ti_noise_args(parser)
    args = parser.parse_args(["--ideal"])
    cfg = build_ti_adc_config(args)
    ref = preset_ideal(cfg.num_channels, bits=cfg.bits, fs_hz=cfg.fs_hz)
    np.testing.assert_allclose(cfg.gain, ref.gain)
    np.testing.assert_allclose(cfg.offset_v, ref.offset_v)
    np.testing.assert_allclose(cfg.timing_skew_s, ref.timing_skew_s)


def test_render_ti_dynamic_netlist_has_four_channels() -> None:
    """Rendered Spectre netlist should instantiate four phase-shifted ADCs."""
    repo = Path(__file__).resolve().parents[1]
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig()
    text = render_ti_spectre_netlist(
        repo / "testbench/spectre/ti_dynamic_spectrum.scs",
        cfg=cfg,
        noise=noise,
        repo_root=repo,
        num_samples=8192,
        coherent_bin=997,
    )
    assert "ADC0" in text and "ADC3" in text
    assert "VCLK0" in text and "clk_mux" in text
    assert "configurable_adc.va" in text or "ti_channel_adc" in text


def test_interleave_channel_round_robin() -> None:
    """Mux should round-robin channel samples."""
    cfg = preset_ideal(num_channels=4, fs_hz=1.0e6)
    ch0 = np.array([0.0, 1.0, 2.0])
    ch1 = np.array([0.25, 1.25, 2.25])
    ch2 = np.array([0.5, 1.5, 2.5])
    ch3 = np.array([0.75, 1.75, 2.75])
    v_mux, _ = interleave_channel_samples([ch0, ch1, ch2, ch3], cfg, num_samples=8)
    assert len(v_mux) == 8
    assert np.isclose(v_mux[0], 0.0)
    assert np.isclose(v_mux[1], 0.25)
    assert np.isclose(v_mux[4], 1.0)
    assert np.isclose(v_mux[5], 1.25)
    assert np.isclose(v_mux[7], 1.75)


def test_read_ti_static_nutascii_fixture() -> None:
    """Captured TI static nutascii should parse all channel save variables."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/ideal/logs/ti_static_inl_dnl.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre static ideal case to generate fixture")
    data = read_spectre_nutascii(fixture)
    assert len(data["time"]) > 1000
    assert set(data) >= {
        "time",
        "vin",
        "clk_mux",
        "clk0",
        "clk3",
        "v_code0",
        "v_code3",
    }


def test_prepare_ti_static_spectre_e2e_va() -> None:
    """Ideal static Spectre VA export should agree with Python within PLAN tolerance."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/ideal/logs/ti_static_inl_dnl.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre static ideal case to generate fixture")
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig()
    raw = read_spectre_nutascii(fixture)
    num_samples = cfg.num_codes * 4
    py = simulate_ti_static(cfg, samples_per_code=4, noise=noise)
    sp = prepare_ti_spectre_waveform(
        raw,
        cfg,
        max_samples=num_samples,
        static_capture=True,
        noise=noise,
    )
    py_lin = compute_ti_inl_dnl(py["vin"], py["code"].astype(int), cfg)
    sp_lin = compute_ti_inl_dnl(sp["vin"], sp["code"].astype(int), cfg)
    assert abs(sp_lin.max_dnl_lsb - py_lin.max_dnl_lsb) < E2E_DNL_TOLERANCE_LSB
    assert abs(sp_lin.max_inl_lsb - py_lin.max_inl_lsb) < E2E_INL_TOLERANCE_LSB


def test_prepare_ti_static_golden_export_matches_python() -> None:
    """Tier-1 golden export should match Python codes on ideal static fixture."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/ideal/logs/ti_static_inl_dnl.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre static ideal case to generate fixture")
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig()
    raw = read_spectre_nutascii(fixture)
    num_samples = cfg.num_codes * 4
    py = simulate_ti_static(cfg, samples_per_code=4, noise=noise)
    sp = prepare_ti_spectre_waveform(
        raw,
        cfg,
        max_samples=num_samples,
        static_capture=True,
        noise=noise,
        golden_export=True,
    )
    assert np.max(np.abs(sp["code"] - py["code"])) <= 1.0


def test_prepare_ti_dynamic_golden_export_matches_python() -> None:
    """Tier-1 golden export should match Python on a synthetic dense waveform."""
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
    dt = 0.25 / cfg.fs_hz
    time = np.arange(0.0, num_samples / cfg.fs_hz, dt)
    waveform = {"time": time, "vin": np.interp(time, py["time"], py["vin"])}
    for k in range(cfg.num_channels):
        waveform[f"clk{k}"] = np.zeros_like(time)
        waveform[f"v_code{k}"] = np.zeros_like(time)

    sp = prepare_ti_spectre_waveform(
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
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < 0.5
    np.testing.assert_allclose(sp["code"], py["code"])


def test_prepare_ti_dynamic_spectre_clock_e2e_va() -> None:
    """Captured clock-case Spectre dynamic VA export vs Python (no code equality)."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/clock/logs/ti_dynamic_spectrum.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre clock dynamic case to generate fixture")
    cfg = replace(preset_skew_mismatch(num_channels=4), fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig(
        sigma_thermal_v=250e-6,
        jitter_rms_s=500e-15,
        nonlinearity_a3=-0.002,
        dnl_sigma_lsb=0.08,
        noise_seed=1,
    )
    num_samples = 8192
    coherent_bin = 997
    fin_hz = coherent_bin * cfg.fs_hz / num_samples
    py = simulate_ti_dynamic(
        cfg,
        num_samples=num_samples,
        coherent_bin=coherent_bin,
        noise=noise,
    )
    raw = read_spectre_nutascii(fixture)
    sp = prepare_ti_spectre_waveform(
        raw,
        cfg,
        max_samples=num_samples,
        static_capture=False,
        noise=noise,
    )
    py_report = compute_ti_dynamic_metrics(py["code"].astype(int), cfg, fin_hz=fin_hz)
    sp_report = compute_ti_dynamic_metrics(sp["code"].astype(int), cfg, fin_hz=fin_hz)
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < E2E_SNDR_TOLERANCE_DB


def test_prepare_ti_dynamic_spectre_ideal_e2e_va() -> None:
    """Captured ideal-case Spectre dynamic VA export vs Python (quantizer-limited)."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/ideal/logs/ti_dynamic_spectrum.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre ideal dynamic case to generate fixture")
    cfg = preset_ideal(num_channels=4, fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig()
    num_samples = 8192
    coherent_bin = 997
    fin_hz = coherent_bin * cfg.fs_hz / num_samples
    py = simulate_ti_dynamic(
        cfg,
        num_samples=num_samples,
        coherent_bin=coherent_bin,
        noise=noise,
    )
    raw = read_spectre_nutascii(fixture)
    sp = prepare_ti_spectre_waveform(
        raw,
        cfg,
        max_samples=num_samples,
        static_capture=False,
        noise=noise,
    )
    py_report = compute_ti_dynamic_metrics(py["code"].astype(int), cfg, fin_hz=fin_hz)
    sp_report = compute_ti_dynamic_metrics(sp["code"].astype(int), cfg, fin_hz=fin_hz)
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < E2E_SNDR_TOLERANCE_DB


def test_prepare_ti_dynamic_golden_export_clock_fixture() -> None:
    """Tier-1 golden export on clock fixture should match Python codes closely."""
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "outputs/spectre/clock/logs/ti_dynamic_spectrum.nutascii"
    if not fixture.is_file():
        pytest.skip("Run spectre clock dynamic case to generate fixture")
    cfg = replace(preset_skew_mismatch(num_channels=4), fs_hz=DEFAULT_FS_HZ)
    noise = AdcNoiseConfig(
        sigma_thermal_v=250e-6,
        jitter_rms_s=500e-15,
        nonlinearity_a3=-0.002,
        dnl_sigma_lsb=0.08,
        noise_seed=1,
    )
    num_samples = 8192
    coherent_bin = 997
    fin_hz = coherent_bin * cfg.fs_hz / num_samples
    py = simulate_ti_dynamic(
        cfg,
        num_samples=num_samples,
        coherent_bin=coherent_bin,
        noise=noise,
    )
    raw = read_spectre_nutascii(fixture)
    sp = prepare_ti_spectre_waveform(
        raw,
        cfg,
        max_samples=num_samples,
        static_capture=False,
        noise=noise,
        golden_export=True,
    )
    py_report = compute_ti_dynamic_metrics(py["code"].astype(int), cfg, fin_hz=fin_hz)
    sp_report = compute_ti_dynamic_metrics(sp["code"].astype(int), cfg, fin_hz=fin_hz)
    assert abs(sp_report.metrics.sndr_db - py_report.metrics.sndr_db) < 1.0
    np.testing.assert_allclose(sp["code"], py["code"])


def test_prepare_ti_spectre_waveform_synthetic_va() -> None:
    """End-to-end VA mux from dense multi-channel clk/v_code arrays."""
    cfg = preset_ideal(num_channels=2, fs_hz=10.0e6)
    m = cfg.num_channels
    fs_ch = cfg.channel_fs_hz
    num_ch_samples = 16
    time = np.arange(num_ch_samples * m) / cfg.fs_hz
    vin = np.linspace(0.0, 1.0, len(time))
    waveform: dict[str, np.ndarray] = {"time": time, "vin": vin}
    for k in range(m):
        t_edges = (np.arange(num_ch_samples) * m + k) / cfg.fs_hz
        waveform[f"clk{k}"] = np.zeros_like(time)
        v_code = np.zeros_like(time)
        for j, te in enumerate(t_edges):
            idx = int(np.argmin(np.abs(time - te)))
            waveform[f"clk{k}"][idx] = 1.0
            hold_idx = min(idx + 1, len(time) - 1)
            v_code[hold_idx] = j * cfg.lsb
        waveform[f"v_code{k}"] = v_code

    muxed = prepare_ti_spectre_waveform(waveform, cfg, max_samples=8)
    assert len(muxed["code"]) == 8
    assert np.all(np.isfinite(muxed["code"]))
    assert muxed["code"][-1] >= muxed["code"][0]
