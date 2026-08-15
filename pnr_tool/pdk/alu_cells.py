"""Logic cell variants used by the OpenLane 32-bit ALU gate-level netlist."""

from __future__ import annotations

import re
from typing import List, Tuple

# sky130_fd_sc_hd logic cells observed in designs/alu/ALU.v (excludes fill/decap/tap)
ALU_LOGIC_CELLS: Tuple[str, ...] = (
    "sky130_fd_sc_hd__a211o_1",
    "sky130_fd_sc_hd__a21bo_1",
    "sky130_fd_sc_hd__a21o_1",
    "sky130_fd_sc_hd__a21oi_1",
    "sky130_fd_sc_hd__a21oi_2",
    "sky130_fd_sc_hd__a221o_1",
    "sky130_fd_sc_hd__a221oi_1",
    "sky130_fd_sc_hd__a22o_1",
    "sky130_fd_sc_hd__a311o_1",
    "sky130_fd_sc_hd__a31o_1",
    "sky130_fd_sc_hd__a31o_4",
    "sky130_fd_sc_hd__a31oi_1",
    "sky130_fd_sc_hd__a32o_1",
    "sky130_fd_sc_hd__and2_1",
    "sky130_fd_sc_hd__and2b_1",
    "sky130_fd_sc_hd__and3_1",
    "sky130_fd_sc_hd__and3b_1",
    "sky130_fd_sc_hd__and4_1",
    "sky130_fd_sc_hd__and4b_1",
    "sky130_fd_sc_hd__buf_1",
    "sky130_fd_sc_hd__buf_2",
    "sky130_fd_sc_hd__buf_4",
    "sky130_fd_sc_hd__buf_6",
    "sky130_fd_sc_hd__clkbuf_1",
    "sky130_fd_sc_hd__clkbuf_2",
    "sky130_fd_sc_hd__clkbuf_4",
    "sky130_fd_sc_hd__clkbuf_16",
    "sky130_fd_sc_hd__dfxtp_1",
    "sky130_fd_sc_hd__dfxtp_2",
    "sky130_fd_sc_hd__dfxtp_4",
    "sky130_fd_sc_hd__dlygate4sd3_1",
    "sky130_fd_sc_hd__dlymetal6s2s_1",
    "sky130_fd_sc_hd__inv_2",
    "sky130_fd_sc_hd__nand2_1",
    "sky130_fd_sc_hd__nand3_1",
    "sky130_fd_sc_hd__nand3b_1",
    "sky130_fd_sc_hd__nor2_1",
    "sky130_fd_sc_hd__nor2_2",
    "sky130_fd_sc_hd__nor3b_1",
    "sky130_fd_sc_hd__o2111a_1",
    "sky130_fd_sc_hd__o211a_1",
    "sky130_fd_sc_hd__o211ai_1",
    "sky130_fd_sc_hd__o21a_1",
    "sky130_fd_sc_hd__o21ai_1",
    "sky130_fd_sc_hd__o21ai_2",
    "sky130_fd_sc_hd__o21ba_1",
    "sky130_fd_sc_hd__o21bai_1",
    "sky130_fd_sc_hd__o22a_1",
    "sky130_fd_sc_hd__o2bb2a_1",
    "sky130_fd_sc_hd__o31a_1",
    "sky130_fd_sc_hd__o31ai_1",
    "sky130_fd_sc_hd__o31ai_2",
    "sky130_fd_sc_hd__o32a_1",
    "sky130_fd_sc_hd__o41a_1",
    "sky130_fd_sc_hd__o41a_4",
    "sky130_fd_sc_hd__or2_1",
    "sky130_fd_sc_hd__or2b_1",
    "sky130_fd_sc_hd__or3_1",
    "sky130_fd_sc_hd__or3_2",
    "sky130_fd_sc_hd__or3_4",
    "sky130_fd_sc_hd__or3b_1",
    "sky130_fd_sc_hd__or4_1",
    "sky130_fd_sc_hd__or4_4",
    "sky130_fd_sc_hd__xnor2_1",
    "sky130_fd_sc_hd__xnor2_2",
    "sky130_fd_sc_hd__xor2_1",
    "sky130_fd_sc_hd__xor2_2",
)


def cell_folder(stem: str) -> str:
    """sky130_fd_sc_hd__a21o_1 -> a21o"""
    body = stem.split("__", 1)[1]
    return re.sub(r"_\d+$", "", body)


def alu_cell_pairs() -> List[Tuple[str, str]]:
    return [(cell_folder(c), c) for c in ALU_LOGIC_CELLS]
