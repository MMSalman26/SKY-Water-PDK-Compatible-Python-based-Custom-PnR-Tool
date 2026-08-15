"""Regression tests for the bug fixes landed alongside the layout images."""

from __future__ import annotations

from pathlib import Path

import pytest

from pnr_tool.algorithms.floorplan import assign_port_positions
from pnr_tool.algorithms.legalize import RowLegalizer
from pnr_tool.checkers.drc import run_drc
from pnr_tool.config import load_config
from pnr_tool.design.graph import NetlistSanityError, sanity_check
from pnr_tool.design.object import DesignObject
from pnr_tool.pdk.fetch import _is_optional_lib

FIXTURES = Path(__file__).parent / "fixtures"


def test_legalizer_never_overlaps_or_leaves_die():
    legalizer = RowLegalizer((0.0, 0.0, 20.0, 8.16), 2.72)
    placed = []
    for i in range(18):
        x, y = legalizer.reserve(2.0, target_x=5.0, target_y=(i % 3) * 2.72)
        placed.append((x, y))
        assert 0.0 <= x and x + 2.0 <= legalizer.maxx
        assert y in (0.0, 2.72, 5.44)
    for i, (x1, y1) in enumerate(placed):
        for x2, y2 in placed[i + 1 :]:
            if y1 == y2:
                assert x1 + 2.0 <= x2 or x2 + 2.0 <= x1


def test_legalizer_avoids_existing_instances():
    instances = {
        "big": {"x": 0.0, "y": 0.0, "width": 9.0, "height": 2.72},
    }
    legalizer = RowLegalizer.from_instances(instances, (0.0, 0.0, 10.0, 5.44), 2.72)
    x, y = legalizer.reserve(2.0, target_x=1.0, target_y=0.0)
    # Row 0 has only 1 um left, so the buffer must move to the free row.
    assert y == 2.72
    assert x + 2.0 <= 10.0


def test_ports_spread_around_die_boundary():
    design = DesignObject(name="p", die_area=(0.0, 0.0, 40.0, 20.0))
    design.ports = {
        "a": {"direction": "input"},
        "b": {"direction": "input"},
        "c": {"direction": "input"},
        "y": {"direction": "output"},
    }
    positions = assign_port_positions(design)
    assert len(positions) == 4
    # No two ports may collapse onto the same coordinate (was all (0, 0)).
    assert len(set(positions.values())) == 4
    for x, y in positions.values():
        on_edge = x in (0.0, 40.0) or y in (0.0, 20.0)
        assert on_edge, (x, y)


def test_spacing_check_scans_beyond_the_old_400_segment_cap():
    design = DesignObject(name="sp", die_area=(0.0, 0.0, 5000.0, 10.0))
    design.tech = {"layers": {"met3": {"min_spacing_um": 0.5}}}
    design.library = {"cells": {}}
    # 900 wide-apart segments, then one close pair far past index 400.
    design.routing = {
        f"n{i}": [{"layer": "met3", "x1": 0.0, "y1": float(i * 10), "x2": 4000.0, "y2": float(i * 10)}]
        for i in range(900)
    }
    design.routing["near"] = [
        {"layer": "met3", "x1": 0.0, "y1": 8990.1, "x2": 4000.0, "y2": 8990.1}
    ]
    report = run_drc(design, {"routing": {"grid_pitch_um": 2.0}})
    spacing = [v for v in report["violations"] if v["type"] == "spacing"]
    assert spacing, "close pair beyond the old truncation limit was missed"
    assert {spacing[0]["net_a"], spacing[0]["net_b"]} == {"n899", "near"}


def test_coincident_segments_are_shorts_not_spacing():
    design = DesignObject(name="sh", die_area=(0.0, 0.0, 20.0, 20.0))
    design.tech = {"layers": {"met2": {"min_spacing_um": 0.14}}}
    design.library = {"cells": {}}
    design.routing = {
        "a": [{"layer": "met2", "x1": 2.0, "y1": 0.0, "x2": 2.0, "y2": 4.0}],
        "b": [{"layer": "met2", "x1": 2.0, "y1": 4.0, "x2": 2.0, "y2": 0.0}],
    }
    counts = run_drc(design, {"routing": {"grid_pitch_um": 2.0}})["counts_by_type"]
    assert counts.get("short") == 1
    assert "spacing" not in counts


def test_drc_reports_counts_by_type():
    design = DesignObject(name="c", die_area=(0.0, 0.0, 20.0, 20.0))
    design.instances = {
        "a": {"x": 0.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 2.0, "height": 2.0},
        "b": {"x": 1.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 2.0, "height": 2.0},
    }
    design.library = {"cells": {}}
    report = run_drc(design, {"routing": {"grid_pitch_um": 2.0}})
    assert report["counts_by_type"] == {"overlap": 1}
    assert report["violation_count"] == 1


def test_sanity_check_reports_combinational_loop():
    design = DesignObject(name="loop")
    design.library = {
        "cells": {
            "INV": {
                "pins": {"A": {"direction": "input"}, "Y": {"direction": "output"}},
                "is_sequential": False,
            }
        }
    }
    design.cells = {
        "u0": {"cell_type": "INV", "pins": {"A": "n1", "Y": "n0"}},
        "u1": {"cell_type": "INV", "pins": {"A": "n0", "Y": "n1"}},
    }
    design.nets = {
        "n0": {"pins": [("u0", "Y"), ("u1", "A")], "drivers": [("u0", "Y")], "driver": ("u0", "Y")},
        "n1": {"pins": [("u1", "Y"), ("u0", "A")], "drivers": [("u1", "Y")], "driver": ("u1", "Y")},
    }
    with pytest.raises(NetlistSanityError):
        sanity_check(design)


def test_acyclic_netlist_passes_sanity_check():
    design = DesignObject(name="chain")
    design.library = {
        "cells": {
            "INV": {
                "pins": {"A": {"direction": "input"}, "Y": {"direction": "output"}},
                "is_sequential": False,
            }
        }
    }
    design.cells = {
        "u0": {"cell_type": "INV", "pins": {"A": "in", "Y": "n0"}},
        "u1": {"cell_type": "INV", "pins": {"A": "n0", "Y": "out"}},
    }
    design.ports = {"in": {"direction": "input"}, "out": {"direction": "output"}}
    design.nets = {
        "in": {"pins": [("PORT:in", "PAD"), ("u0", "A")], "drivers": [], "driver": ("PORT:in", "PAD")},
        "n0": {"pins": [("u0", "Y"), ("u1", "A")], "drivers": [("u0", "Y")], "driver": ("u0", "Y")},
        "out": {"pins": [("u1", "Y"), ("PORT:out", "PAD")], "drivers": [("u1", "Y")], "driver": ("u1", "Y")},
    }
    assert sanity_check(design) == []


@pytest.mark.parametrize(
    "stem, optional",
    [
        ("sky130_fd_sc_hd__fill_1", True),
        ("sky130_fd_sc_hd__decap_4", True),
        ("sky130_fd_sc_hd__tapvpwrvgnd_1", True),
        ("sky130_fd_sc_hd__inv_2", False),
        ("sky130_fd_sc_hd__dfxtp_1", False),
    ],
)
def test_physical_cells_marked_as_optional_downloads(stem, optional):
    assert _is_optional_lib(stem) is optional
