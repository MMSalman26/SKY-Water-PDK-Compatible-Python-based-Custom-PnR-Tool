"""4x4 combinational matmul synth (basic-gate mapping)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pnr_tool.config import load_config
from pnr_tool.pdk.fetch import TT_CORNER
from pnr_tool.synth.liberty import write_mapping_liberty
from pnr_tool.synth.yosys import find_yosys, run_yosys_synth

ROOT = Path(__file__).resolve().parents[1]
MATMUL_RTL = ROOT / "designs" / "matmul44" / "matmul44.v"
MATMUL_CFG = ROOT / "designs" / "matmul44" / "config.yaml"

_ALLOWED = ("inv", "and2", "or2", "nand2", "nor2", "xor2", "xnor2")
_INST_RE = re.compile(r"^\s*(sky130_fd_sc_hd__[A-Za-z0-9_]+)\s+", re.MULTILINE)


def test_mapping_liberty_allowlist(tmp_path):
    root = tmp_path / "sky130_fd_sc_hd" / "cells"
    corner = TT_CORNER

    def dump(folder: str, stem: str, payload: dict) -> None:
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}__{corner}.lib.json").write_text(json.dumps(payload), encoding="utf-8")

    dump(
        "inv",
        "sky130_fd_sc_hd__inv_2",
        {
            "area": 1.0,
            "pin,A": {"direction": "input", "capacitance": 0.002},
            "pin,Y": {"direction": "output", "function": "(!A)"},
        },
    )
    dump(
        "and2",
        "sky130_fd_sc_hd__and2_1",
        {
            "area": 2.0,
            "pin,A": {"direction": "input", "capacitance": 0.002},
            "pin,B": {"direction": "input", "capacitance": 0.002},
            "pin,X": {"direction": "output", "function": "(A&B)"},
        },
    )
    dump(
        "mux2",
        "sky130_fd_sc_hd__mux2_1",
        {
            "area": 3.0,
            "pin,A0": {"direction": "input", "capacitance": 0.002},
            "pin,A1": {"direction": "input", "capacitance": 0.002},
            "pin,S": {"direction": "input", "capacitance": 0.002},
            "pin,X": {"direction": "output", "function": "(S?A1:A0)"},
        },
    )
    dump(
        "dfxtp",
        "sky130_fd_sc_hd__dfxtp_1",
        {
            "area": 8.0,
            "ff,IQ,IQ_N": {"next_state": "D", "clocked_on": "CLK"},
            "pin,D": {"direction": "input", "capacitance": 0.002},
            "pin,CLK": {"direction": "input", "clock": True, "capacitance": 0.002},
            "pin,Q": {"direction": "output", "function": "IQ"},
        },
    )
    dest = tmp_path / "map.lib"
    write_mapping_liberty(
        tmp_path,
        dest,
        corner=corner,
        allow_prefixes=["inv", "and2", "or2", "nand2", "nor2", "xor2", "xnor2"],
    )
    text = dest.read_text(encoding="utf-8")
    assert "cell (sky130_fd_sc_hd__inv_2)" in text
    assert "cell (sky130_fd_sc_hd__and2_1)" in text
    assert "mux2" not in text
    assert "dfxtp" not in text


@pytest.mark.skipif(not MATMUL_RTL.is_file(), reason="matmul44 RTL missing")
def test_matmul44_synth_if_yosys():
    try:
        find_yosys()
    except FileNotFoundError:
        pytest.skip("yosys / yowasp-yosys not installed")
    work = ROOT / "runs" / "_pytest_matmul44"
    out = work / "matmul44.gl.v"
    result = run_yosys_synth(
        MATMUL_RTL,
        top="matmul44",
        out=out,
        config_path=MATMUL_CFG,
        fetch_if_missing=True,
    )
    gl = Path(result["netlist"]).read_text(encoding="utf-8")
    insts = _INST_RE.findall(gl)
    assert "module matmul44" in gl
    assert insts
    assert len(insts) <= 1500
    for cell in insts:
        body = cell.split("__", 1)[-1]
        assert any(body == p or body.startswith(p + "_") for p in _ALLOWED), cell
        assert "dfxtp" not in body
        assert "sdfxtp" not in body
    assert load_config(MATMUL_CFG)["synth"]["mapping_prefixes"]
