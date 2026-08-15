"""Uniform random placement legalized across the full die (optional alias)."""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from pnr_tool.algorithms.base import PlacementAlgorithm
from pnr_tool.algorithms.floorplan import die_is_placeholder, estimate_die_area
from pnr_tool.algorithms.legalize import RowLegalizer
from pnr_tool.design.object import DesignObject


class RandomPlacement(PlacementAlgorithm):
    """Scatter cells uniformly across the die, then row-legalize in random order."""

    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        place_cfg = config.get("placement", {})
        row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
        spacing = float(place_cfg.get("cell_spacing_um", 0.01))
        seed = int(place_cfg.get("seed", 42))

        cells = list(design.cells.keys())
        n = len(cells)
        if n == 0:
            return {}

        if die_is_placeholder(design.die_area):
            estimate_die_area(design, config)

        lib_cells = design.library.get("cells", {})
        widths = np.empty(n)
        heights = np.empty(n)
        for i, c in enumerate(cells):
            lib = lib_cells.get(design.cells[c]["cell_type"], {})
            widths[i] = float(lib.get("width", 1.38))
            heights[i] = float(lib.get("height", row_h))

        row_h = max(row_h, float(np.max(heights)))
        minx, miny, maxx, maxy = (float(v) for v in design.die_area)
        die_w = max(maxx - minx, float(np.max(widths)) + 2 * spacing)
        die_h = max(maxy - miny, row_h)
        design.die_area = (minx, miny, minx + die_w, miny + die_h)
        rows = max(1, int(round(die_h / row_h)))

        rng = np.random.default_rng(seed)
        xs = minx + rng.uniform(0, die_w, size=n)
        ys = miny + (rng.integers(0, rows, size=n).astype(float)) * row_h

        legalizer = RowLegalizer((minx, miny, minx + die_w, miny + die_h), row_h, spacing=spacing)
        order = rng.permutation(n)
        for i in order:
            lx, ly = legalizer.reserve(float(widths[i]), float(xs[i]), float(ys[i]))
            xs[i] = lx
            ys[i] = ly

        if legalizer.die_area[2] > design.die_area[2] + 1e-6:
            design.die_area = legalizer.die_area
            design.meta["die_expanded_by_legalizer"] = True
        else:
            design.meta["die_expanded_by_legalizer"] = False

        instances: Dict[str, Dict[str, Any]] = {}
        for i, c in enumerate(cells):
            instances[c] = {
                "x": float(xs[i]),
                "y": float(ys[i]),
                "orientation": "N",
                "is_fixed": False,
                "width": float(widths[i]),
                "height": float(heights[i]),
            }
        return instances
