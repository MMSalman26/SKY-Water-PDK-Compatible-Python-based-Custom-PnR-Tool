"""Stand-alone DFT: scan-replace + logical stitch on a gate-level netlist."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pnr_tool.config import load_config, project_root
from pnr_tool.design.graph import infer_drivers
from pnr_tool.dft.replace import scan_replace
from pnr_tool.dft.stitch import stitch_scan
from pnr_tool.io.verilog_parser import elaborate, parse_verilog_file
from pnr_tool.io.verilog_writer import write_verilog
from pnr_tool.pdk.fetch import fetch_pdk, pdk_ready


class DftError(RuntimeError):
    pass


def insert_dft(
    netlist: Union[str, Path],
    *,
    top: Optional[str] = None,
    out: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    fetch_if_missing: bool = True,
    stitch: bool = True,
) -> Dict[str, Any]:
    """Rewrite a SkyWater HD GLN with scan cells and stitched chains.

    Combinational netlists (no flops) are copied unchanged.
    """
    cfg = config if config is not None else load_config(config_path)
    src = Path(netlist)
    if not src.is_file():
        raise DftError(f"Netlist not found: {src}")

    modules = parse_verilog_file(src)
    design = elaborate(modules, top=top, strip_physical=True, strip_power_pins=True)
    replace_summary = scan_replace(design)
    stitch_summary: Dict[str, Any] = {
        "chains": [],
        "scan_cells": 0,
        "scan_enable": None,
        "note": "stitch skipped",
    }
    if stitch:
        stitch_summary = stitch_scan(design, cfg)
        infer_drivers(design)

    out_v = Path(out) if out else project_root() / "runs" / design.name / f"{design.name}.dft.v"
    out_v.parent.mkdir(parents=True, exist_ok=True)

    unchanged = not replace_summary["replaced"] and not stitch_summary.get("chains")
    if unchanged:
        if src.resolve() != out_v.resolve():
            shutil.copyfile(src, out_v)
    else:
        write_verilog(design, out_v)

    cache = Path(cfg["pdk"]["cache_dir"])
    if fetch_if_missing and not unchanged:
        if not pdk_ready(cache):
            fetch_pdk(cache)
        fetch_pdk(cache, extra_netlists=[out_v])

    report = {
        "top": design.name,
        "netlist_in": str(src),
        "netlist_out": str(out_v),
        "unchanged": unchanged,
        "replace": replace_summary,
        "stitch": stitch_summary,
        "note": (
            "Scan replace + chain stitch only. Not ATPG, lockup-latch, "
            "compression, or manufacturing DFT signoff."
        ),
    }
    report_path = (
        out_v.with_suffix(".json")
        if out_v.suffix == ".v" and out_v.stem.endswith(".dft")
        else out_v.parent / f"{out_v.stem}.dft.json"
    )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report"] = str(report_path)
    return report
