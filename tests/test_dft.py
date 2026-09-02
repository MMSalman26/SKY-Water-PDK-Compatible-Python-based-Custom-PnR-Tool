"""DFT scan-replace + chain stitch."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.dft.cells import is_nonscan_flop, is_scan_cell, scan_equivalent
from pnr_tool.dft.replace import scan_replace
from pnr_tool.dft.run import insert_dft
from pnr_tool.dft.stitch import stitch_scan
from pnr_tool.io.verilog_parser import elaborate, parse_verilog

ROOT = Path(__file__).resolve().parents[1]
MUX_GL = ROOT / "designs" / "mux41" / "mux41.gl.v"

_TINY = """
module dft_tiny(clk, d, q);
  input clk;
  input d;
  output q;
  wire n0;
  sky130_fd_sc_hd__dfxtp_1 f0 (.CLK(clk), .D(d), .Q(n0));
  sky130_fd_sc_hd__dfxtp_1 f1 (.CLK(clk), .D(n0), .Q(q));
endmodule
"""


def test_scan_equivalent_sky130():
    assert scan_equivalent("sky130_fd_sc_hd__dfxtp_1") == "sky130_fd_sc_hd__sdfxtp_1"
    assert scan_equivalent("sky130_fd_sc_hd__dfrtp_2") == "sky130_fd_sc_hd__sdfrtp_2"
    assert scan_equivalent("sky130_fd_sc_hd__edfxtp_1") == "sky130_fd_sc_hd__sedfxtp_1"
    assert is_nonscan_flop("sky130_fd_sc_hd__dfxtp_1")
    assert is_scan_cell("sky130_fd_sc_hd__sdfxtp_1")
    assert scan_equivalent("sky130_fd_sc_hd__mux4_1") is None


def test_replace_and_stitch_two_flops():
    design = elaborate(parse_verilog(_TINY), top="dft_tiny")
    repl = scan_replace(design)
    assert repl["replaced_count"] == 2
    assert design.cells["f0"]["cell_type"] == "sky130_fd_sc_hd__sdfxtp_1"
    st = stitch_scan(design, {"dft": {"max_length": 8}})
    assert st["scan_cells"] == 2
    assert len(st["chains"]) == 1
    chain = st["chains"][0]
    assert chain["length"] == 2
    assert "scan_en" in design.ports
    assert "scan_in_0" in design.ports
    assert "scan_out_0" in design.ports
    assert design.cells["f0"]["pins"]["SCD"] == "scan_in_0"
    assert design.cells["f0"]["pins"]["SCE"] == "scan_en"
    assert design.cells["f1"]["pins"]["SCD"] == design.cells["f0"]["pins"]["Q"]
    assert design.cells["f1"]["pins"]["SCE"] == "scan_en"


def test_max_length_splits_chains():
    design = elaborate(parse_verilog(_TINY), top="dft_tiny")
    scan_replace(design)
    st = stitch_scan(design, {"dft": {"max_length": 1}})
    assert len(st["chains"]) == 2
    assert all(c["length"] == 1 for c in st["chains"])
    assert "scan_in_1" in design.ports


def test_insert_dft_writes_scan_netlist(tmp_path):
    src = tmp_path / "tiny.v"
    src.write_text(_TINY, encoding="utf-8")
    out = tmp_path / "tiny.dft.v"
    result = insert_dft(src, top="dft_tiny", out=out, fetch_if_missing=False)
    assert result["unchanged"] is False
    text = out.read_text(encoding="utf-8")
    assert "sky130_fd_sc_hd__sdfxtp_1" in text
    assert "scan_en" in text
    assert "scan_in_0" in text
    assert "SCD" in text
    assert "SCE" in text


def test_mux41_dft_is_noop():
    assert MUX_GL.is_file()
    out = ROOT / "runs" / "_dft_mux41" / "mux41.dft.v"
    result = insert_dft(MUX_GL, top="mux41", out=out, fetch_if_missing=False)
    assert result["unchanged"] is True
    assert result["replace"]["replaced_count"] == 0
    assert result["stitch"]["chains"] == []
    text = Path(result["netlist_out"]).read_text(encoding="utf-8")
    assert "module mux41" in text
    assert "sky130_fd_sc_hd__mux4_1" in text
    assert "scan_en" not in text
    assert "sdfxtp" not in text
