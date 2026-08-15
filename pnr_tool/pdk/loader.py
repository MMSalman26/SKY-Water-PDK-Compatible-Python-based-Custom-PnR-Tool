"""Load merged library (geometry + timing) and tech from PDK cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from .fetch import FF_CORNER, SS_CORNER, TT_CORNER
from .lef_parser import parse_lef_dir
from .lib_parser import parse_lib_json_dir
from .tech import load_tech

CORNER_ALIASES = {
    "ff": FF_CORNER,
    "ss": SS_CORNER,
    "tt": TT_CORNER,
}


def _merge_lef_timing(
    lef_cells: Dict[str, Any],
    lib_cells: Dict[str, Any],
    tech: Dict[str, Any],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    names = set(lef_cells) | set(lib_cells)
    for name in names:
        geom = lef_cells.get(name, {})
        timing = lib_cells.get(name, {})
        pins: Dict[str, Any] = {}
        for pname, pinfo in geom.get("pins", {}).items():
            pins[pname] = {
                "direction": pinfo.get("direction", "input"),
                "use": pinfo.get("use", "SIGNAL"),
                "rects": pinfo.get("rects", []),
                "capacitance": 0.0,
                "is_clock": False,
                "timing_arcs": [],
            }
        for pname, pinfo in timing.get("pins", {}).items():
            entry = pins.setdefault(
                pname,
                {
                    "direction": pinfo.get("direction", "input"),
                    "use": "SIGNAL",
                    "rects": [],
                    "capacitance": 0.0,
                    "is_clock": False,
                    "timing_arcs": [],
                },
            )
            entry["direction"] = pinfo.get("direction", entry["direction"])
            entry["capacitance"] = pinfo.get("capacitance", 0.0)
            entry["is_clock"] = pinfo.get("is_clock", False)
            entry["function"] = pinfo.get("function")
            entry["timing_arcs"] = pinfo.get("timing_arcs", [])

        width = float(geom.get("width", 0.0) or 0.0)
        height = float(geom.get("height", 0.0) or tech.get("site_height_um", 2.72))
        if width <= 0:
            area = float(timing.get("area", 0.0) or 0.0)
            width = area / height if height > 0 and area > 0 else tech.get("site_width_um", 0.46) * 3

        merged[name] = {
            "width": width,
            "height": height,
            "area": float(timing.get("area", width * height)),
            "leakage_power": float(timing.get("leakage_power", 0.0) or 0.0),
            "is_sequential": bool(timing.get("is_sequential", False)),
            "pins": pins,
            "obs": list(geom.get("obs") or []),
        }
    return merged


def load_library_and_tech(cache_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cache_dir = Path(cache_dir)
    cells_root = cache_dir / "sky130_fd_sc_hd"
    if not cells_root.exists():
        raise FileNotFoundError(
            f"PDK cache incomplete at {cache_dir}. Run: python -m pnr_tool fetch-pdk"
        )

    lef_cells = parse_lef_dir(cells_root)
    tech = load_tech(cache_dir)

    corners: Dict[str, Dict[str, Any]] = {}
    for alias, substr in CORNER_ALIASES.items():
        parsed = parse_lib_json_dir(cells_root, corner_substr=substr)
        if parsed:
            corners[alias] = _merge_lef_timing(lef_cells, parsed, tech)

    ff_cells = corners.get("ff") or _merge_lef_timing(lef_cells, {}, tech)
    library = {
        "name": "sky130_fd_sc_hd",
        "corner": FF_CORNER if "ff" in corners else "unknown",
        "cells": ff_cells,
        "corners": corners,
    }
    return library, tech


def ensure_cells(library: dict, cell_names: Iterable[str], tech: dict | None = None) -> dict:
    """Add stub entries for missing cells so placement can proceed."""
    tech = tech or {}
    site_h = float(tech.get("site_height_um", 2.72))
    site_w = float(tech.get("site_width_um", 0.46))
    cells = library.setdefault("cells", {})
    corners = library.setdefault("corners", {})
    for name in cell_names:
        if name in cells:
            continue
        drive = 1
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            drive = max(1, int(parts[1]))
        width = site_w * max(3, drive * 2)
        stub = {
            "width": width,
            "height": site_h,
            "area": width * site_h,
            "leakage_power": 1e-3,
            "is_sequential": any(x in name for x in ("dfxtp", "dfrtp", "sdfxtp", "dlxtp")),
            "pins": {
                "A": {"direction": "input", "use": "SIGNAL", "capacitance": 0.002, "is_clock": False, "timing_arcs": [], "rects": []},
                "Y": {"direction": "output", "use": "SIGNAL", "capacitance": 0.0, "is_clock": False, "timing_arcs": [], "rects": []},
                "X": {"direction": "output", "use": "SIGNAL", "capacitance": 0.0, "is_clock": False, "timing_arcs": [], "rects": []},
                "Q": {"direction": "output", "use": "SIGNAL", "capacitance": 0.0, "is_clock": False, "timing_arcs": [], "rects": []},
                "D": {"direction": "input", "use": "SIGNAL", "capacitance": 0.002, "is_clock": False, "timing_arcs": [], "rects": []},
                "CLK": {"direction": "input", "use": "SIGNAL", "capacitance": 0.002, "is_clock": True, "timing_arcs": [], "rects": []},
            },
            "obs": [],
            "stub": True,
        }
        cells[name] = stub
        for cdict in corners.values():
            cdict.setdefault(name, stub)
    return library
