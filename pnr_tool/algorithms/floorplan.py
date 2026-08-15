"""Die sizing (init_fp) and boundary I/O pad placement (ioplacer)."""

from __future__ import annotations

import math
from typing import Any, Dict, Tuple

from pnr_tool.design.object import DesignObject

DieArea = Tuple[float, float, float, float]

# Default placeholder until floorplan runs.
_DEFAULT_DIE: DieArea = (0.0, 0.0, 100.0, 100.0)


def die_is_placeholder(die: DieArea) -> bool:
    return tuple(float(v) for v in die) == _DEFAULT_DIE


def estimate_die_area(design: DesignObject, config: Dict[str, Any]) -> DieArea:
    """OpenLane ``init_fp`` analogue: size the die from cell area / utilization.

    Sets ``design.die_area`` and returns it. Safe to call before placement.
    """
    place_cfg = config.get("placement", {})
    util = float(place_cfg.get("die_utilization", 0.6))
    row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
    spacing = float(place_cfg.get("cell_spacing_um", 0.01))

    lib_cells = design.library.get("cells", {})
    total_area = 0.0
    max_w = 0.0
    max_h = row_h
    for info in design.cells.values():
        lib = lib_cells.get(info.get("cell_type", ""), {})
        w = float(lib.get("width", 1.38))
        h = float(lib.get("height", row_h))
        total_area += w * h
        max_w = max(max_w, w)
        max_h = max(max_h, h)

    if total_area <= 0:
        total_area = 100.0

    row_h = max(row_h, max_h)
    side = math.sqrt(max(total_area / max(util, 0.1), 1.0))
    rows = max(1, int(math.ceil(side / row_h)))
    die_h = rows * row_h
    die_w = max(side, total_area / die_h, max_w + 2 * spacing)
    die: DieArea = (0.0, 0.0, float(die_w), float(die_h))
    design.die_area = die
    design.meta["floorplan"] = {
        "utilization": util,
        "row_height_um": row_h,
        "total_cell_area_um2": total_area,
        "num_rows": rows,
    }
    return die


def assign_port_positions(design: DesignObject) -> Dict[str, Tuple[float, float]]:
    """Spread primary ports evenly around the die perimeter (ioplacer analogue).

    Inputs walk the left and bottom edges, outputs the right and top edges, so
    every terminal gets a distinct boundary coordinate instead of collapsing on
    the die origin.
    """
    minx, miny, maxx, maxy = (float(v) for v in design.die_area)
    width = max(maxx - minx, 1e-6)
    height = max(maxy - miny, 1e-6)

    inputs = sorted(n for n, p in design.ports.items() if p.get("direction") == "input")
    outputs = sorted(n for n, p in design.ports.items() if p.get("direction") != "input")

    positions: Dict[str, Tuple[float, float]] = {}
    positions.update(_walk_edges(inputs, (minx, miny), (minx, maxy), (maxx, miny)))
    positions.update(_walk_edges(outputs, (maxx, maxy), (maxx, miny), (minx, maxy)))

    for name, (x, y) in positions.items():
        info = design.ports.setdefault(name, {"direction": "inout"})
        info["x"] = min(max(x, minx), maxx)
        info["y"] = min(max(y, miny), maxy)
    design.meta["port_positions"] = {k: tuple(v) for k, v in positions.items()}
    return positions


def _walk_edges(
    names: list[str],
    corner: Tuple[float, float],
    vertical_far: Tuple[float, float],
    horizontal_far: Tuple[float, float],
) -> Dict[str, Tuple[float, float]]:
    """Distribute ``names`` along the vertical edge first, then the horizontal one."""
    if not names:
        return {}
    cx, cy = corner
    vy = vertical_far[1]
    hx = horizontal_far[0]
    half = (len(names) + 1) // 2
    out: Dict[str, Tuple[float, float]] = {}
    for i, name in enumerate(names[:half]):
        t = (i + 0.5) / half
        out[name] = (cx, cy + (vy - cy) * t)
    rest = names[half:]
    for i, name in enumerate(rest):
        t = (i + 0.5) / max(len(rest), 1)
        out[name] = (cx + (hx - cx) * t, cy)
    return out
