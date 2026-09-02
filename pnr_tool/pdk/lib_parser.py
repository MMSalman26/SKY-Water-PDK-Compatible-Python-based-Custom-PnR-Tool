"""Parse SkyWater per-cell .lib.json NLDM timing (ff corner)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _is_seq(cell_name: str, data: dict) -> bool:
    if "dfxtp" in cell_name or "dfrtp" in cell_name or "dfstp" in cell_name or "sdfxtp" in cell_name:
        return True
    for key, val in data.items():
        if key.startswith("pin,") and isinstance(val, dict):
            if str(val.get("clock", "")).lower() == "true" or val.get("clock") is True:
                return True
    return False


def _pin_name(key: str) -> Optional[str]:
    if key.startswith("pin,"):
        return key.split(",", 1)[1]
    return None


def _extract_timing_tables(pin_data: dict) -> List[dict]:
    timing = pin_data.get("timing")
    if timing is None:
        return []
    if isinstance(timing, dict):
        return [timing]
    if isinstance(timing, list):
        return timing
    return []


def _as_float_list(val: Any) -> List[float]:
    if val is None:
        return []
    if isinstance(val, (int, float)):
        return [float(val)]
    if isinstance(val, list):
        out: List[float] = []
        for x in val:
            if isinstance(x, (list, tuple)):
                out.extend(float(y) for y in x)
            else:
                out.append(float(x))
        return out
    raise TypeError(f"expected list/number, got {type(val)}")


def _parse_nldm_table(matched: dict) -> Optional[dict]:
    """Normalize NLDM / constraint table; tolerate flat or nested values."""
    try:
        index_1 = _as_float_list(matched.get("index_1"))
        index_2 = _as_float_list(matched.get("index_2"))
    except (TypeError, ValueError):
        return None
    raw = matched.get("values", [])
    values: List[List[float]] = []
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        values = [[float(raw)]]
    elif isinstance(raw, list):
        if not raw:
            values = []
        elif isinstance(raw[0], (list, tuple)):
            try:
                values = [[float(x) for x in row] for row in raw]
            except (TypeError, ValueError):
                return None
        else:
            try:
                values = [[float(x) for x in raw]]
            except (TypeError, ValueError):
                return None
    else:
        return None
    return {"index_1": index_1, "index_2": index_2, "values": values}


def parse_lib_json_file(path: Path, cell_name: Optional[str] = None) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if cell_name is None:
        # stem: sky130_fd_sc_hd__inv_2__ff_100C_1v95 -> sky130_fd_sc_hd__inv_2
        stem = Path(path).stem
        cell_name = re.sub(r"__ff_.*$", "", stem)
        cell_name = re.sub(r"__ss_.*$", "", cell_name)
        cell_name = re.sub(r"__tt_.*$", "", cell_name)
    return {cell_name: _parse_cell(cell_name, data)}


def _parse_cell(cell_name: str, data: dict) -> Dict[str, Any]:
    pins: Dict[str, Any] = {}
    for key, val in data.items():
        pname = _pin_name(key)
        if pname is None or not isinstance(val, dict):
            continue
        direction = str(val.get("direction", "input")).lower()
        pin_info: Dict[str, Any] = {
            "direction": direction,
            "capacitance": float(val.get("capacitance", 0.0) or 0.0),
            "is_clock": bool(val.get("clock") is True or str(val.get("clock", "")).lower() == "true"),
            "function": val.get("function"),
            "timing_arcs": [],
        }
        for t in _extract_timing_tables(val):
            arc = {
                "related_pin": t.get("related_pin"),
                "timing_type": t.get("timing_type", "combinational"),
                "timing_sense": t.get("timing_sense"),
                "when": t.get("when") or t.get("sdf_cond"),
            }
            for field in (
                "cell_rise",
                "cell_fall",
                "rise_transition",
                "fall_transition",
                "rise_constraint",
                "fall_constraint",
            ):
                # keys look like "cell_rise,del_1_7_7"
                matched = None
                for k, v in t.items():
                    if k.startswith(field):
                        matched = v
                        break
                if matched and isinstance(matched, dict):
                    parsed = _parse_nldm_table(matched)
                    if parsed is not None:
                        arc[field] = parsed
            pin_info["timing_arcs"].append(arc)
        pins[pname] = pin_info

    area = float(data.get("area", 0.0) or 0.0)
    leakage = float(data.get("cell_leakage_power", 0.0) or 0.0)
    cell: Dict[str, Any] = {
        "area": area,
        "leakage_power": leakage,
        "is_sequential": _is_seq(cell_name, data),
        "pins": pins,
    }
    ip = data.get("internal_power")
    if isinstance(ip, (int, float)):
        cell["internal_power"] = float(ip)
    ip_nw = data.get("internal_power_nw")
    if isinstance(ip_nw, (int, float)):
        cell["internal_power_nw"] = float(ip_nw)
    return cell


def parse_lib_json_dir(root: Path, corner_substr: str = "ff_100C_1v95") -> Dict[str, Any]:
    cells: Dict[str, Any] = {}
    for path in Path(root).rglob(f"*{corner_substr}.lib.json"):
        cells.update(parse_lib_json_file(path))
    return cells


def interpolate_nldm(table: dict, slew: float, load: float) -> float:
    """2D bilinear interpolation on NLDM table; clamp to edges."""
    if not table or not table.get("values"):
        return 0.0
    i1 = np.asarray(table["index_1"], dtype=float)
    i2 = np.asarray(table["index_2"], dtype=float)
    vals = np.asarray(table["values"], dtype=float)
    if vals.size == 0:
        return 0.0
    slew = float(np.clip(slew, i1[0], i1[-1]))
    load = float(np.clip(load, i2[0], i2[-1]))
    # indices
    r = np.searchsorted(i1, slew) - 1
    c = np.searchsorted(i2, load) - 1
    r = int(np.clip(r, 0, len(i1) - 2)) if len(i1) > 1 else 0
    c = int(np.clip(c, 0, len(i2) - 2)) if len(i2) > 1 else 0
    if len(i1) == 1 and len(i2) == 1:
        return float(vals[0, 0])
    if len(i1) == 1:
        y0, y1 = i2[c], i2[min(c + 1, len(i2) - 1)]
        v0, v1 = vals[0, c], vals[0, min(c + 1, vals.shape[1] - 1)]
        t = 0.0 if y1 == y0 else (load - y0) / (y1 - y0)
        return float(v0 + t * (v1 - v0))
    if len(i2) == 1:
        x0, x1 = i1[r], i1[min(r + 1, len(i1) - 1)]
        v0, v1 = vals[r, 0], vals[min(r + 1, vals.shape[0] - 1), 0]
        t = 0.0 if x1 == x0 else (slew - x0) / (x1 - x0)
        return float(v0 + t * (v1 - v0))
    x0, x1 = i1[r], i1[r + 1]
    y0, y1 = i2[c], i2[c + 1]
    q11, q12 = vals[r, c], vals[r, c + 1]
    q21, q22 = vals[r + 1, c], vals[r + 1, c + 1]
    tx = 0.0 if x1 == x0 else (slew - x0) / (x1 - x0)
    ty = 0.0 if y1 == y0 else (load - y0) / (y1 - y0)
    return float(
        q11 * (1 - tx) * (1 - ty)
        + q21 * tx * (1 - ty)
        + q12 * (1 - tx) * ty
        + q22 * tx * ty
    )


def lookup_cell_delay(
    cell: dict,
    related_pin: str,
    out_pin: str,
    slew: float,
    load: float,
    sense: str = "rise",
) -> Tuple[float, float]:
    """Return (delay_ns, out_slew_ns) for rise or fall."""
    pin = cell.get("pins", {}).get(out_pin, {})
    delay_key = "cell_rise" if sense == "rise" else "cell_fall"
    slew_key = "rise_transition" if sense == "rise" else "fall_transition"
    best: Optional[Tuple[float, float]] = None
    for arc in pin.get("timing_arcs", []):
        if arc.get("related_pin") != related_pin:
            continue
        ttype = str(arc.get("timing_type", "combinational")).lower()
        if "constraint" in ttype or ttype.startswith("setup") or ttype.startswith("hold"):
            continue
        if any(x in ttype for x in ("recovery", "removal", "min_pulse", "preset", "clear")):
            continue
        dtable = arc.get(delay_key)
        stable = arc.get(slew_key)
        if dtable:
            delay = interpolate_nldm(dtable, slew, load)
            out_slew = interpolate_nldm(stable, slew, load) if stable else slew
            # GBA: worst when-conditioned arc (mux/scan) when SDF cond is unknown
            if best is None or delay > best[0]:
                best = (delay, out_slew)
    if best is not None:
        return best
    # Fallback constant delay
    return 0.05, max(slew, 0.01)


def lookup_setup_hold(
    cell: dict,
    data_pin: str,
    clock_pin: str,
    data_slew: float,
    clock_slew: float,
    check: str = "setup",
) -> Optional[float]:
    """Return setup/hold/recovery/removal constraint (ns) from liberty, or None."""
    pin = cell.get("pins", {}).get(data_pin, {})
    want = str(check).lower()
    best: Optional[float] = None
    for arc in pin.get("timing_arcs", []):
        if arc.get("related_pin") != clock_pin:
            continue
        ttype = str(arc.get("timing_type", "")).lower()
        if want not in ttype:
            continue
        for field in ("rise_constraint", "fall_constraint"):
            table = arc.get(field)
            if not table:
                continue
            val = interpolate_nldm(table, data_slew, clock_slew)
            if best is None or val > best:
                best = val
    return best


def async_constraint_pins(cell: dict) -> List[str]:
    """Pins with recovery/removal (or set/reset) constraint arcs."""
    out: List[str] = []
    for pname, pinfo in cell.get("pins", {}).items():
        for arc in pinfo.get("timing_arcs", []) or []:
            ttype = str(arc.get("timing_type", "")).lower()
            if "recovery" in ttype or "removal" in ttype:
                if pname not in out:
                    out.append(pname)
                break
    return out


def related_pins_for_output(cell: dict, out_pin: str) -> List[str]:
    """Liberty related_pin values for delay arcs into ``out_pin``."""
    pin = cell.get("pins", {}).get(out_pin, {})
    related: List[str] = []
    for arc in pin.get("timing_arcs", []):
        ttype = str(arc.get("timing_type", "combinational")).lower()
        if "constraint" in ttype or ttype.startswith("setup") or ttype.startswith("hold"):
            continue
        rp = arc.get("related_pin")
        if rp and rp not in related:
            related.append(str(rp))
    return related
