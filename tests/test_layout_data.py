"""layout_view.json export: pins + cell metadata."""

from __future__ import annotations

from pnr_tool.design.object import DesignObject
from pnr_tool.report.layout_data import build_layout_view, _parse_cell_meta


def test_parse_cell_meta_drive_strength():
    meta = _parse_cell_meta("sky130_fd_sc_hd__inv_2")
    assert meta["kind"] == "inv"
    assert meta["family"] == "inv"
    assert meta["drive_strength"] == 2


def test_layout_view_includes_pins_and_meta():
    design = DesignObject(name="pinme", die_area=(0.0, 0.0, 20.0, 10.88))
    design.library = {
        "cells": {
            "sky130_fd_sc_hd__inv_2": {
                "width": 1.38,
                "height": 2.72,
                "pins": {
                    "A": {
                        "direction": "input",
                        "use": "SIGNAL",
                        "rects": [{"layer": "li1", "x1": 0.1, "y1": 1.0, "x2": 0.4, "y2": 1.3}],
                    },
                    "Y": {
                        "direction": "output",
                        "use": "SIGNAL",
                        "rects": [{"layer": "li1", "x1": 0.9, "y1": 1.0, "x2": 1.2, "y2": 1.3}],
                    },
                    "VPWR": {"direction": "inout", "use": "POWER", "rects": []},
                },
            }
        }
    }
    design.cells = {"u0": {"cell_type": "sky130_fd_sc_hd__inv_2", "pins": {}}}
    design.instances = {
        "u0": {"x": 2.0, "y": 1.0, "orientation": "N", "is_fixed": False, "width": 1.38, "height": 2.72}
    }
    view = build_layout_view(design, stage="placement")
    assert view["schema"] == 2
    cell = view["cells"][0]
    assert cell["kind"] == "inv"
    assert cell["drive_strength"] == 2
    assert cell["cell_type"].endswith("inv_2")
    names = {p["name"] for p in cell["pins"]}
    assert "A" in names and "Y" in names
    a = next(p for p in cell["pins"] if p["name"] == "A")
    assert abs(a["x"] - (2.0 + 0.25)) < 1e-6
    assert a["direction"] == "input"
