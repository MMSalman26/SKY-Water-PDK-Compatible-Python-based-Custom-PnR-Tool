"""Minimal legal placer used by plugin-loader tests."""

from __future__ import annotations

import math
from typing import Any, Dict

from pnr_tool.algorithms.base import PlacementAlgorithm
from pnr_tool.algorithms.legalize import RowLegalizer
from pnr_tool.design.object import DesignObject


class DummyPlacement(PlacementAlgorithm):
    """Place cells left-to-right on successive rows (deterministic, legal)."""

    def execute(self, design: DesignObject, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        place_cfg = config.get("placement", {})
        util = float(place_cfg.get("die_utilization", 0.6))
        row_h = float(place_cfg.get("row_height_um", design.tech.get("site_height_um", 2.72)))
        spacing = float(place_cfg.get("cell_spacing_um", 0.01))

        cells = list(design.cells.keys())
        if not cells:
            design.die_area = (0.0, 0.0, 10.0, row_h)
            return {}

        lib_cells = design.library.get("cells", {})
        widths = []
        heights = []
        for name in cells:
            lib = lib_cells.get(design.cells[name]["cell_type"], {})
            widths.append(float(lib.get("width", 1.38)))
            heights.append(float(lib.get("height", row_h)))

        row_h = max(row_h, max(heights))
        total_area = sum(w * h for w, h in zip(widths, heights))
        side = math.sqrt(max(total_area / max(util, 0.1), 1.0))
        rows = max(1, int(math.ceil(side / row_h)))
        die_h = rows * row_h
        die_w = max(side, total_area / die_h, max(widths) + 2 * spacing)
        design.die_area = (0.0, 0.0, die_w, die_h)

        legalizer = RowLegalizer((0.0, 0.0, die_w, die_h), row_h, spacing=spacing)
        instances: Dict[str, Dict[str, Any]] = {}
        for name, w, h in zip(cells, widths, heights):
            x, y = legalizer.reserve(w, 0.0, 0.0)
            instances[name] = {
                "x": float(x),
                "y": float(y),
                "orientation": "N",
                "is_fixed": False,
                "width": float(w),
                "height": float(h),
            }
        design.die_area = legalizer.die_area
        return instances
