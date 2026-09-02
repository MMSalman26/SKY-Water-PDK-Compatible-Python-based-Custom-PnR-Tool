"""Yosys synth front-end tests (liberty writer always; Yosys if installed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pnr_tool.pdk.fetch import TT_CORNER
from pnr_tool.synth.liberty import write_mapping_liberty
from pnr_tool.synth.yosys import find_yosys, run_yosys_synth

ROOT = Path(__file__).resolve().parents[1]
MUX_RTL = ROOT / "designs" / "mux41" / "mux41.v"


def test_mapping_liberty_from_json(tmp_path):
    cell_dir = tmp_path / "sky130_fd_sc_hd" / "cells" / "inv"
    cell_dir.mkdir(parents=True)
    payload = {
        "area": 3.75,
        "pin,A": {"direction": "input", "capacitance": 0.002},
        "pin,Y": {"direction": "output", "function": "(!A)"},
        "pg_pin,VGND": {"pg_type": "primary_ground"},
    }
    (cell_dir / f"sky130_fd_sc_hd__inv_2__{TT_CORNER}.lib.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    dest = tmp_path / "map.lib"
    write_mapping_liberty(tmp_path, dest, corner=TT_CORNER)
    text = dest.read_text(encoding="utf-8")
    assert "cell (sky130_fd_sc_hd__inv_2)" in text
    assert 'function : "(!A)"' in text
    assert "VGND" not in text


def test_find_yosys_or_skip():
    try:
        cmd = find_yosys()
    except FileNotFoundError:
        pytest.skip("yosys / yowasp-yosys not installed")
    assert cmd


@pytest.mark.skipif(not MUX_RTL.is_file(), reason="mux41 RTL missing")
def test_mux41_synth_if_yosys(tmp_path):
    try:
        find_yosys()
    except FileNotFoundError:
        pytest.skip("yosys / yowasp-yosys not installed")
    out = tmp_path / "mux41.gl.v"
    # YoWASP/WASI cannot always read pytest's temp dir; keep the workdir in-repo.
    work = ROOT / "runs" / "_pytest_mux41"
    out = work / "mux41.gl.v"
    result = run_yosys_synth(MUX_RTL, top="mux41", out=out, fetch_if_missing=True)
    gl = Path(result["netlist"]).read_text(encoding="utf-8")
    assert "module mux41" in gl
    assert "sky130_fd_sc_hd__" in gl
    assert result["cells"]
