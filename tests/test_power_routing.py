"""Power planning, pin terminals, IR mode, stubs."""

from __future__ import annotations

from pathlib import Path

import pytest

from pnr_tool.algorithms.pins import instance_pin_xy
from pnr_tool.algorithms.power_plan import plan_power
from pnr_tool.checkers.ir_drop import run_ir_drop
from pnr_tool.config import load_config, project_root
from pnr_tool.pipeline.run import run_pipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_power_plan_creates_vdd_vss_segments():
    from pnr_tool.design.object import DesignObject

    design = DesignObject(name="p", die_area=(0.0, 0.0, 40.0, 40.0))
    design.tech = {"site_height_um": 2.72}
    grid = plan_power(design, load_config())
    segs = grid["segments"]
    assert len(segs) > 8
    nets = {s["net"] for s in segs}
    assert "VPWR" in nets and "VGND" in nets
    assert design.power_grid is grid


def test_golden_seq_power_ir_and_stubs(tmp_path):
    result = run_pipeline(
        FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "gs",
        fetch_if_missing=True,
        layout_images=False,
    )
    design = result["design"]
    assert design.power_grid and design.power_grid.get("segments")
    assert any(s.get("role") == "stub" for segs in design.routing.values() for s in segs)
    ir = run_ir_drop(design, load_config())
    assert ir.get("ir_mode") == "power_grid"
    report = result["report"]
    assert report["checks"]["ir_drop"].get("ir_mode") == "power_grid"
    assert report["timing_s"].get("power_plan", 0) >= 0


def test_pin_terminal_not_cell_origin():
    """LEF pin center differs from instance origin for inv_2."""
    from pnr_tool.design.object import DesignObject

    design = DesignObject(name="t", die_area=(0, 0, 20, 20))
    design.library = {
        "cells": {
            "sky130_fd_sc_hd__inv_2": {
                "width": 1.38,
                "height": 2.72,
                "pins": {
                    "A": {
                        "direction": "input",
                        "rects": [{"layer": "li1", "x1": 0.1, "y1": 1.0, "x2": 0.4, "y2": 1.3}],
                    },
                    "Y": {
                        "direction": "output",
                        "rects": [{"layer": "li1", "x1": 0.9, "y1": 1.0, "x2": 1.2, "y2": 1.3}],
                    },
                },
            }
        }
    }
    design.cells = {"u0": {"cell_type": "sky130_fd_sc_hd__inv_2", "pins": {"A": "n0", "Y": "n1"}}}
    design.instances = {
        "u0": {"x": 5.0, "y": 3.0, "orientation": "N", "is_fixed": False, "width": 1.38, "height": 2.72}
    }
    a = instance_pin_xy(design, "u0", "A")
    assert a is not None
    assert abs(a[0] - 5.0) > 0.05 or abs(a[1] - 3.0) > 0.05


def test_alu_pipeline_smoke(tmp_path):
    alu = project_root() / "designs" / "alu" / "ALU.v"
    if not alu.exists():
        pytest.skip("ALU netlist missing")
    result = run_pipeline(
        alu,
        top="ALU",
        out_dir=tmp_path / "alu",
        clock_period_ns=10.0,
        fetch_if_missing=True,
        layout_images=False,
    )
    d = result["design"]
    assert len(d.power_grid.get("segments", [])) > 10
    assert len(d.routing) > 100
    assert result["report"]["checks"]["ir_drop"].get("ir_mode") == "power_grid"
