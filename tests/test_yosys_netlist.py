"""Yosys-style netlist parsing + netlist cell scan."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.io.verilog_parser import elaborate, parse_verilog
from pnr_tool.pdk.fetch import scan_netlist_cells

ROOT = Path(__file__).resolve().parents[1]
PICORV = ROOT / "designs" / "picorv32a" / "picorv32a.synthesis.v"

YOSYS_SNIPPET = """
module tiny_yosys(clk, din, dout);
  wire _000_;
  wire alias_net;
  wire [3:0] bus;
  input clk;
  input [3:0] din;
  output [3:0] dout;
  assign alias_net = _000_;
  assign dout[0] = bus[0];
  sky130_fd_sc_hd__conb_1 _tie_ (
    .LO(_000_)
  );
  sky130_fd_sc_hd__inv_2 _i0_ (
    .A(din[0]),
    .Y(bus[0])
  );
  sky130_fd_sc_hd__dfxtp_2 \\reg[0]  (
    .CLK(clk),
    .D(alias_net),
    .Q(dout[1])
  );
  sky130_fd_sc_hd__buf_1 _b1_ (
    .A(\\mem_state[0] ),
    .X(dout[2])
  );
  wire \\mem_state[0] ;
endmodule
"""


def test_scan_netlist_cells_snippet(tmp_path):
    path = tmp_path / "snippet.v"
    path.write_text(YOSYS_SNIPPET, encoding="utf-8")
    stems = scan_netlist_cells(path)
    assert "sky130_fd_sc_hd__conb_1" in stems
    assert "sky130_fd_sc_hd__inv_2" in stems
    assert "sky130_fd_sc_hd__dfxtp_2" in stems
    assert "sky130_fd_sc_hd__buf_1" in stems


def test_yosys_assign_alias_and_buses():
    mods = parse_verilog(YOSYS_SNIPPET)
    assert "tiny_yosys" in mods
    mod = mods["tiny_yosys"]
    assert ("alias_net", "_000_") in mod.assigns
    assert any(p[0] == "din[0]" for p in mod.ports)
    assert any(p[0] == "dout[3]" for p in mod.ports)

    design = elaborate(mods, top="tiny_yosys", library_cell_names=set())
    assert "\\reg[0]" not in design.cells  # escaped name normalized
    assert "reg[0]" in design.cells
    # Flop D should follow assign alias_net -> _000_ (conb LO)
    assert design.cells["reg[0]"]["pins"]["D"] == "_000_"
    # assign dout[0] = bus[0] collapses onto the port net
    assert design.cells["_i0_"]["pins"]["Y"] == "dout[0]"
    assert any(p[0] == "PORT:dout[0]" for p in design.nets["dout[0]"]["pins"])
    # Escaped bit select normalized
    assert design.cells["_b1_"]["pins"]["A"] == "mem_state[0]"
    assert "conb" in design.cells["_tie_"]["cell_type"]


def test_picorv32a_elaborates_if_present():
    if not PICORV.exists():
        return
    stems = scan_netlist_cells(PICORV)
    assert len(stems) == 58
    mods = parse_verilog(PICORV.read_text(encoding="utf-8"))
    design = elaborate(mods, top="picorv32a", library_cell_names=set())
    assert len(design.cells) == 14876
    assert sum(1 for c in design.cells.values() if "dfxtp" in c["cell_type"]) == 1613
    assert len(design.nets.get("clk", {}).get("pins", [])) >= 1613
