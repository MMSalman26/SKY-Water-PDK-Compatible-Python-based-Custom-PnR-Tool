"""Emit a Yosys/ABC mapping liberty from cached SkyWater ``*.lib.json`` files.

Timing tables are omitted: this file is only for technology mapping (cell
functions), not signoff STA.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from pnr_tool.pdk.fetch import TT_CORNER
from pnr_tool.pdk.lib_parser import _pin_name

_POWER_PINS = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS"})
_SKIP_PREFIXES = ("fill", "decap", "tap", "diode", "dlxtn", "dlrtp", "edfx", "sdfx")


def _escape_lib(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _cell_stem_from_json_path(path: Path) -> str:
    stem = path.stem  # sky130_fd_sc_hd__mux2_1__tt_025C_1v80.lib
    stem = re.sub(r"\.lib$", "", stem)
    stem = re.sub(r"__(ff|ss|tt)_.*$", "", stem)
    return stem


def _ff_group(data: dict) -> Optional[Dict[str, str]]:
    for key, val in data.items():
        if not str(key).startswith("ff") or not isinstance(val, dict):
            continue
        # "ff,IQ,IQ_N"
        parts = str(key).split(",")
        iq = parts[1] if len(parts) > 1 else "IQ"
        iqn = parts[2] if len(parts) > 2 else "IQ_N"
        ns = val.get("next_state")
        clk = val.get("clocked_on")
        if not ns or not clk:
            continue
        rec = {
            "iq": str(iq),
            "iqn": str(iqn),
            "next_state": str(ns),
            "clocked_on": str(clk),
        }
        if val.get("clear"):
            rec["clear"] = str(val["clear"])
        if val.get("preset"):
            rec["preset"] = str(val["preset"])
        return rec
    return None


def _iter_json_cells(cache_dir: Path, corner: str) -> Iterable[tuple[str, dict]]:
    root = Path(cache_dir) / "sky130_fd_sc_hd"
    if not root.is_dir():
        return
    for path in sorted(root.rglob(f"*{corner}.lib.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        yield _cell_stem_from_json_path(path), data


def _cell_body(name: str) -> str:
    return name.split("__", 1)[-1] if "__" in name else name


def _prefix_allowed(body: str, allow_prefixes: Optional[Sequence[str]]) -> bool:
    if not allow_prefixes:
        return True
    for raw in allow_prefixes:
        p = str(raw)
        if body == p or body.startswith(p + "_"):
            return True
    return False


def write_mapping_liberty(
    cache_dir: Path,
    dest: Path,
    corner: str = TT_CORNER,
    allow_prefixes: Optional[Sequence[str]] = None,
) -> Path:
    """Write a combinational+DFF liberty ABC can map against.

    ``allow_prefixes`` limits cells to those whose stem (after
    ``sky130_fd_sc_hd__``) starts with one of the strings, e.g.
    ``inv``, ``nand2``. Sequential cells are omitted when this is set.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"/* pnr-tool mapping liberty from {corner} lib.json; not for signoff */",
        "library (pnr_tool_sky130_hd) {",
        "  delay_model : generic;",
        '  time_unit : "1ns";',
        '  voltage_unit : "1V";',
        '  current_unit : "1mA";',
        '  pulling_resistance_unit : "1kohm";',
        '  capacitive_load_unit (1,pf);',
        "  slew_lower_threshold_pct_rise : 20.0;",
        "  slew_lower_threshold_pct_fall : 20.0;",
        "  slew_upper_threshold_pct_rise : 80.0;",
        "  slew_upper_threshold_pct_fall : 80.0;",
        "  input_threshold_pct_rise : 50.0;",
        "  input_threshold_pct_fall : 50.0;",
        "  output_threshold_pct_rise : 50.0;",
        "  output_threshold_pct_fall : 50.0;",
        "",
    ]
    n_cells = 0
    for name, data in _iter_json_cells(cache_dir, corner):
        body = _cell_body(name)
        if any(body.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if not _prefix_allowed(body, allow_prefixes):
            continue
        ff = _ff_group(data)
        if allow_prefixes and ff:
            continue
        pins: Dict[str, Any] = {}
        for key, val in data.items():
            pname = _pin_name(key)
            if pname is None or not isinstance(val, dict):
                continue
            if pname in _POWER_PINS:
                continue
            pins[pname] = val
        has_fn = any(
            p.get("function") not in (None, "")
            and str(p.get("direction", "")).lower() == "output"
            for p in pins.values()
        )
        if not has_fn and not ff:
            continue
        if any(p.get("three_state") for p in pins.values()):
            continue
        area = float(data.get("area", 1.0) or 1.0)
        lines.append(f"  cell ({name}) {{")
        lines.append(f"    area : {area:.4f};")
        if ff:
            extra = ""
            if ff.get("clear"):
                extra += f'\n      clear : "{_escape_lib(ff["clear"])}";'
            if ff.get("preset"):
                extra += f'\n      preset : "{_escape_lib(ff["preset"])}";'
            lines.append(f"    ff ({ff['iq']}, {ff['iqn']}) {{")
            lines.append(f'      next_state : "{_escape_lib(ff["next_state"])}";')
            lines.append(f'      clocked_on : "{_escape_lib(ff["clocked_on"])}";')
            if extra:
                lines.append(extra)
            lines.append("    }")
        for pname, pinfo in pins.items():
            direction = str(pinfo.get("direction", "input")).lower()
            lines.append(f"    pin ({pname}) {{")
            lines.append(f"      direction : {direction};")
            if direction == "input":
                cap = float(pinfo.get("capacitance", 0.001) or 0.001)
                lines.append(f"      capacitance : {cap:.6f};")
                if pinfo.get("clock") is True or str(pinfo.get("clock", "")).lower() == "true":
                    lines.append("      clock : true;")
            else:
                fn = pinfo.get("function")
                if fn is not None and str(fn) != "":
                    lines.append(f'      function : "{_escape_lib(fn)}";')
                max_c = pinfo.get("max_capacitance")
                if max_c is not None:
                    try:
                        lines.append(f"      max_capacitance : {float(max_c):.6f};")
                    except (TypeError, ValueError):
                        pass
            lines.append("    }")
        lines.append("  }")
        n_cells += 1
    lines.append("}")
    lines.append("")
    if n_cells < 1:
        raise FileNotFoundError(
            f"No mapping cells found under {cache_dir} for corner {corner}. "
            "Run: python -m pnr_tool fetch-pdk"
        )
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest
