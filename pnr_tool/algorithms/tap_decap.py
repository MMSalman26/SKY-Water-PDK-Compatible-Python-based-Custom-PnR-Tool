"""OpenLane-style tapcell then decap insertion (no filler cells)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pnr_tool.design.object import DesignObject


def insert_taps(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    """Insert well taps on a pitch (+ optional endcaps). No fillers."""
    tcfg = config.get("tap", {}) or {}
    if not bool(tcfg.get("enable", True)):
        summary = {"taps": 0, "enabled": False}
        design.meta["tap"] = summary
        return summary

    place_cfg = config.get("placement", {})
    row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
    spacing = float(place_cfg.get("cell_spacing_um", 0.01))
    minx, miny, maxx, maxy = (float(v) for v in design.die_area)
    rows = max(1, int(round((maxy - miny) / row_h)))

    lib = design.library.get("cells", {})
    tap_name = str(tcfg.get("cell", "sky130_fd_sc_hd__tapvpwrvgnd_1"))
    pitch = float(tcfg.get("pitch_um", 15.0))
    endcap = bool(tcfg.get("endcap", True))

    _ensure_physical_lib(lib, [tap_name], row_h, design.tech)
    tap_w = float(lib.get(tap_name, {}).get("width", 0.46))
    occupied = _row_occupancy(design, lib, rows, minx, miny, row_h, spacing)

    taps = 0
    tap_id = 0
    for r in range(rows):
        y = miny + r * row_h
        if tap_name not in lib or pitch <= 0:
            continue
        x = minx
        while x + tap_w <= maxx + 1e-9:
            if _fits(occupied[r], x, tap_w):
                _place_physical(
                    design, f"TAP_{tap_id}", tap_name, x, y, tap_w, row_h, physical="tap"
                )
                occupied[r] = _merge_intervals(occupied[r] + [(x, x + tap_w + spacing)])
                taps += 1
                tap_id += 1
            x += pitch
        if endcap:
            for ex in (minx, max(minx, maxx - tap_w)):
                if _fits(occupied[r], ex, tap_w):
                    _place_physical(
                        design, f"TAP_{tap_id}", tap_name, ex, y, tap_w, row_h, physical="tap"
                    )
                    occupied[r] = _merge_intervals(occupied[r] + [(ex, ex + tap_w + spacing)])
                    taps += 1
                    tap_id += 1

    summary = {"taps": taps, "enabled": True}
    design.meta["tap"] = summary
    return summary


def insert_decaps(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    """Insert decap cells into remaining row gaps. Never inserts filler cells."""
    tcfg = config.get("tap", {}) or {}
    dcfg = config.get("decap", {}) or {}
    # Prefer dedicated decap: block; fall back to legacy tap.decap_* keys
    enable = bool(dcfg.get("enable", tcfg.get("decap_enable", True)))
    if not enable:
        summary = {"decaps": 0, "enabled": False}
        design.meta["decap"] = summary
        return summary

    place_cfg = config.get("placement", {})
    row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
    spacing = float(place_cfg.get("cell_spacing_um", 0.01))
    minx, miny, maxx, maxy = (float(v) for v in design.die_area)
    rows = max(1, int(round((maxy - miny) / row_h)))

    lib = design.library.get("cells", {})
    fill_ratio = float(dcfg.get("fill_ratio", tcfg.get("decap_fill_ratio", 0.5)))
    decap_names = list(
        dcfg.get(
            "cells",
            tcfg.get(
                "decap_cells",
                [
                    "sky130_fd_sc_hd__decap_12",
                    "sky130_fd_sc_hd__decap_8",
                    "sky130_fd_sc_hd__decap_4",
                ],
            ),
        )
        or []
    )
    # Explicitly ignore any legacy fill_cells config — never insert fillers.
    _ensure_physical_lib(lib, decap_names, row_h, design.tech)
    decap_opts = [(n, float(lib[n]["width"])) for n in decap_names if n in lib]
    decap_opts.sort(key=lambda t: -t[1])

    occupied = _row_occupancy(design, lib, rows, minx, miny, row_h, spacing)
    decaps = 0
    phy_id = 0

    for r in range(rows):
        y = miny + r * row_h
        gaps = _free_gaps(occupied[r], minx, maxx)
        total_gap = sum(g[1] - g[0] for g in gaps)
        budget = total_gap * max(0.0, min(1.0, fill_ratio))
        used = 0.0
        for g0, g1 in gaps:
            if used >= budget - 1e-9:
                break
            cursor = g0
            while cursor + 0.4 <= g1 + 1e-9 and used < budget:
                rem = g1 - cursor
                placed = False
                for cname, cw in decap_opts:
                    if cw <= rem + 1e-9:
                        _place_physical(
                            design,
                            f"DECAP_{phy_id}",
                            cname,
                            cursor,
                            y,
                            cw,
                            row_h,
                            physical="decap",
                        )
                        phy_id += 1
                        decaps += 1
                        occupied[r] = _merge_intervals(
                            occupied[r] + [(cursor, cursor + cw + spacing)]
                        )
                        cursor += cw + spacing
                        used += cw
                        placed = True
                        break
                if not placed:
                    break

    summary = {"decaps": decaps, "enabled": True}
    design.meta["decap"] = summary
    # Combined summary for older report readers
    design.meta["tap_decap"] = {
        "taps": int((design.meta.get("tap") or {}).get("taps", 0)),
        "decaps": decaps,
        "fills": 0,
        "enabled": True,
    }
    return summary


def insert_tap_decap(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility helper: taps then decaps (still no fillers)."""
    taps = insert_taps(design, config)
    decaps = insert_decaps(design, config)
    return {
        "taps": int(taps.get("taps", 0)),
        "decaps": int(decaps.get("decaps", 0)),
        "fills": 0,
        "enabled": bool(taps.get("enabled") or decaps.get("enabled")),
    }


def _row_occupancy(
    design: DesignObject,
    lib: Dict[str, Any],
    rows: int,
    minx: float,
    miny: float,
    row_h: float,
    spacing: float,
) -> List[List[Tuple[float, float]]]:
    occupied: List[List[Tuple[float, float]]] = [[] for _ in range(rows)]
    for name, inst in design.instances.items():
        r = int(round((float(inst["y"]) - miny) / row_h))
        r = max(0, min(rows - 1, r))
        x0 = float(inst["x"])
        ctype = design.cells.get(name, {}).get("cell_type", "")
        w = float(inst.get("width", lib.get(ctype, {}).get("width", 1.0)))
        occupied[r].append((x0, x0 + w + spacing))
    for r in range(rows):
        occupied[r] = _merge_intervals(occupied[r])
    return occupied


def _ensure_physical_lib(
    lib: Dict[str, Any], names: List[str], row_h: float, tech: Dict[str, Any]
) -> None:
    site_w = float(tech.get("site_width_um", 0.46))
    width_hints = {
        "tapvpwrvgnd_1": 1 * site_w,
        "decap_3": 3 * site_w,
        "decap_4": 4 * site_w,
        "decap_6": 6 * site_w,
        "decap_8": 8 * site_w,
        "decap_12": 12 * site_w,
    }
    for name in names:
        if name in lib and float(lib[name].get("width", 0)) > 0:
            continue
        suffix = name.split("__")[-1] if "__" in name else name
        w = width_hints.get(suffix, 2 * site_w)
        lib[name] = {
            "width": w,
            "height": row_h,
            "area": w * row_h,
            "leakage_power": 0.0,
            "is_sequential": False,
            "pins": {},
            "stub": True,
            "physical": True,
        }


def _place_physical(
    design: DesignObject,
    name: str,
    cell_type: str,
    x: float,
    y: float,
    width: float,
    height: float,
    physical: str,
) -> None:
    design.cells[name] = {
        "cell_type": cell_type,
        "pins": {},
        "physical": physical,
    }
    design.instances[name] = {
        "x": float(x),
        "y": float(y),
        "orientation": "N",
        "is_fixed": True,
        "width": float(width),
        "height": float(height),
        "physical": physical,
    }


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [intervals[0]]
    for a, b in intervals[1:]:
        la, lb = out[-1]
        if a <= lb + 1e-9:
            out[-1] = (la, max(lb, b))
        else:
            out.append((a, b))
    return out


def _fits(occupied: List[Tuple[float, float]], x: float, w: float) -> bool:
    x1 = x + w
    for a, b in occupied:
        if x1 <= a + 1e-9 or x >= b - 1e-9:
            continue
        return False
    return True


def _free_gaps(
    occupied: List[Tuple[float, float]], minx: float, maxx: float
) -> List[Tuple[float, float]]:
    gaps: List[Tuple[float, float]] = []
    cursor = minx
    for a, b in occupied:
        if a - cursor > 0.4:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if maxx - cursor > 0.4:
        gaps.append((cursor, maxx))
    return gaps
