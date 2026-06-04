"""Resolve the adc-model repository root (Verilog-A and testbench assets)."""

from __future__ import annotations

import os
from pathlib import Path

_VA_MARKER = Path("veriloga/configurable_adc.va")


def adc_model_root() -> Path:
    """Return adc-model checkout root containing ``veriloga/`` and ``testbench/``.

    Resolution order:
    1. ``ADC_MODEL_ROOT`` environment variable
    2. Parent directories of the installed ``adc_model`` package (editable git install)

    Returns:
        Absolute path to the adc-model repository root.

    Raises:
        FileNotFoundError: If ``veriloga/configurable_adc.va`` cannot be located.
    """
    env = os.environ.get("ADC_MODEL_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / _VA_MARKER).is_file():
            return root
        msg = f"ADC_MODEL_ROOT={root} missing {_VA_MARKER}"
        raise FileNotFoundError(msg)

    import adc_model

    pkg_dir = Path(adc_model.__file__).resolve().parent
    for candidate in (pkg_dir.parent.parent, pkg_dir.parent):
        root = candidate.resolve()
        if (root / _VA_MARKER).is_file():
            return root

    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        src_checkout = Path(venv) / "src" / "adc-model"
        if (src_checkout / _VA_MARKER).is_file():
            return src_checkout.resolve()

    msg = (
        "adc-model assets not found. Install adc-model editable from "
        "git@github.com:SJTU-YONGFU-RESEARCH-GRP/adc-model.git "
        "(see scripts/install_python.sh) or set ADC_MODEL_ROOT."
    )
    raise FileNotFoundError(msg)
