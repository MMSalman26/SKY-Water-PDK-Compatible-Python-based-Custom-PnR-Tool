"""Architect and stitch scan chains (OpenROAD ``execute_dft_plan`` ideas)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pnr_tool.design.object import DesignObject
from pnr_tool.dft.cells import (
    CLK_PINS,
    Q_PINS,
    SCAN_EN_PINS,
    SCAN_IN_PINS,
    is_scan_cell,
    pick_pin,
)
from pnr_tool.dft.nets import add_port, connect_pin


def _lib_pins(design: DesignObject, cell_type: str) -> dict:
    return ((design.library or {}).get("cells", {}).get(cell_type) or {}).get("pins") or {}


def _pin(design: DesignObject, inst: str, candidates: Tuple[str, ...], fallback: str) -> str:
    info = design.cells[inst]
    pins = dict(info.get("pins") or {})
    found = pick_pin(pins, candidates)
    if found:
        return found
    found = pick_pin(_lib_pins(design, str(info.get("cell_type") or "")), candidates)
    if found:
        return found
    return fallback


def _clock_net(design: DesignObject, inst: str) -> str:
    clk = _pin(design, inst, CLK_PINS, "CLK")
    return str((design.cells[inst].get("pins") or {}).get(clk) or "_none")


def _xy(design: DesignObject, inst: str) -> Tuple[float, float]:
    placed = design.instances.get(inst) or {}
    try:
        return float(placed.get("x", 0.0)), float(placed.get("y", 0.0))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _format_name(pattern: str, index: int) -> str:
    if "{" in pattern:
        return pattern.format(index)
    return f"{pattern}_{index}" if index else pattern


def _scan_instances(design: DesignObject) -> List[str]:
    return sorted(
        inst
        for inst, info in design.cells.items()
        if is_scan_cell(str(info.get("cell_type") or ""))
    )


def _order_cells(design: DesignObject, insts: Sequence[str], clock_mixing: str) -> List[str]:
    placed = bool(design.instances)
    if clock_mixing == "clock_mix":
        groups = [list(insts)]
    else:
        by_clk: Dict[str, List[str]] = {}
        for inst in insts:
            by_clk.setdefault(_clock_net(design, inst), []).append(inst)
        groups = [by_clk[k] for k in sorted(by_clk)]

    ordered: List[str] = []
    for group in groups:
        if placed:
            group = sorted(group, key=lambda i: (_xy(design, i)[1], _xy(design, i)[0], i))
        else:
            group = sorted(group)
        ordered.extend(group)
    return ordered


def _chunk(insts: Sequence[str], max_length: Optional[int], max_chains: Optional[int]) -> List[List[str]]:
    cells = list(insts)
    if not cells:
        return []
    n = len(cells)
    if max_chains is not None and max_chains > 0:
        length = max(1, (n + max_chains - 1) // max_chains)
        if max_length is not None and max_length > 0:
            length = min(length, max_length)
    elif max_length is not None and max_length > 0:
        length = max_length
    else:
        length = n
    return [cells[i : i + length] for i in range(0, n, length)]


def stitch_scan(design: DesignObject, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Connect scan flops into chains. Run **after placement** when XY is available.

    Combinational designs (no scan cells) are a no-op: no extra ports.
    """
    cfg = dict((config or {}).get("dft") or {})
    max_length = cfg.get("max_length")
    max_length_i = int(max_length) if max_length not in (None, "", 0, "0") else None
    max_chains = cfg.get("max_chains")
    max_chains_i = int(max_chains) if max_chains not in (None, "", 0, "0") else None
    clock_mixing = str(cfg.get("clock_mixing") or "no_mix")
    se_pat = str(cfg.get("scan_enable") or "scan_en")
    si_pat = str(cfg.get("scan_in") or "scan_in_{}")
    so_pat = str(cfg.get("scan_out") or "scan_out_{}")

    scan_insts = _scan_instances(design)
    if not scan_insts:
        summary: Dict[str, Any] = {
            "chains": [],
            "scan_cells": 0,
            "scan_enable": None,
            "note": "no scan cells (combinational or not yet replaced)",
        }
        design.meta["dft_stitch"] = summary
        return summary

    ordered = _order_cells(design, scan_insts, clock_mixing)
    chunks = _chunk(ordered, max_length_i, max_chains_i)
    se_name = se_pat.format(0) if "{" in se_pat else se_pat
    if se_name not in design.ports:
        add_port(design, se_name, "input")

    chains: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        si_name = _format_name(si_pat, idx)
        so_name = _format_name(so_pat, idx)
        if si_name not in design.ports:
            add_port(design, si_name, "input")
        prev_q: Optional[str] = None
        for i, inst in enumerate(chunk):
            si_pin = _pin(design, inst, SCAN_IN_PINS, "SCD")
            se_pin = _pin(design, inst, SCAN_EN_PINS, "SCE")
            q_pin = _pin(design, inst, Q_PINS, "Q")
            connect_pin(design, inst, se_pin, se_name)
            if i == 0:
                connect_pin(design, inst, si_pin, si_name)
            else:
                assert prev_q is not None
                connect_pin(design, inst, si_pin, prev_q)
            prev_q = str((design.cells[inst].get("pins") or {}).get(q_pin) or "")
        if so_name not in design.ports:
            add_port(design, so_name, "output", net=prev_q or so_name)
        chains.append(
            {
                "index": idx,
                "length": len(chunk),
                "cells": list(chunk),
                "scan_in": si_name,
                "scan_out": so_name,
                "scan_enable": se_name,
                "clock": _clock_net(design, chunk[0]) if chunk else None,
            }
        )

    summary = {
        "chains": chains,
        "scan_cells": len(scan_insts),
        "scan_enable": se_name,
        "clock_mixing": clock_mixing,
        "max_length": max_length_i,
        "max_chains": max_chains_i,
    }
    design.meta["dft_stitch"] = summary
    return summary
