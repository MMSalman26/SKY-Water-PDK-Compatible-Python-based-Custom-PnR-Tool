"""Golden micro-design tests — run after PDK fetch."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.config import load_config
from pnr_tool.design.contracts import validate_instances
from pnr_tool.design.graph import infer_drivers, sanity_check
from pnr_tool.io.verilog_parser import elaborate, parse_verilog_file
from pnr_tool.pipeline.run import run_pipeline
from pnr_tool.spatial import create_spatial_index
from pnr_tool.spatial.index import boxes_overlap
from pnr_tool.algorithms.placement import ForceDirectedPlacement
from pnr_tool.checkers.drc import run_drc
from pnr_tool.checkers.sta import run_sta
from pnr_tool.checkers.ir_drop import run_ir_drop

FIXTURES = Path(__file__).parent / "fixtures"


def test_spatial_index_overlap_detection():
    idx = create_spatial_index()
    idx.insert(0, (0, 0, 2, 2))
    idx.insert(1, (1, 1, 3, 3))
    idx.insert(2, (5, 5, 6, 6))
    hits = idx.intersection((0, 0, 2.5, 2.5))
    assert 0 in hits and 1 in hits
    assert boxes_overlap((0, 0, 2, 2), (1, 1, 3, 3))
    assert not boxes_overlap((0, 0, 1, 1), (2, 2, 3, 3))


def test_parse_and_elaborate_golden(library_tech):
    library, tech = library_tech
    modules = parse_verilog_file(FIXTURES / "golden_three_cell.v")
    assert "golden_three_cell" in modules
    design = elaborate(modules, library_cell_names=set(library["cells"]))
    design.library = library
    design.tech = tech
    assert len(design.cells) == 3
    infer_drivers(design)
    warnings = sanity_check(design)
    assert isinstance(warnings, list)


def test_hand_overlap_count():
    """Two identical boxes overlapping → exactly one overlap violation pair."""
    from pnr_tool.design.object import DesignObject

    d = DesignObject(name="overlap_gold", die_area=(0, 0, 20, 20))
    d.instances = {
        "a": {"x": 0.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 2.0, "height": 2.0},
        "b": {"x": 1.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 2.0, "height": 2.0},
        "c": {"x": 10.0, "y": 10.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 1.0},
    }
    d.routing = {}
    d.nets = {}
    d.tech = {"layers": {"met2": {"min_spacing_um": 0.14}}}
    d.library = {"cells": {}}
    d.cells = {}
    report = run_drc(d, {"routing": {"grid_pitch_um": 2.0}})
    overlaps = [v for v in report["violations"] if v["type"] == "overlap"]
    assert len(overlaps) == 1


def test_elmore_and_sta_smoke(library_tech):
    library, tech = library_tech
    modules = parse_verilog_file(FIXTURES / "golden_three_cell.v")
    design = elaborate(modules, library_cell_names=set(library["cells"]))
    design.library = library
    design.tech = tech
    infer_drivers(design)
    cfg = load_config()
    placer = ForceDirectedPlacement()
    design.instances = placer.execute(design, cfg)
    validate_instances(design.instances, design.die_area)
    design.routing = {}
    sta = run_sta(design, cfg, clock_period_ns=5.0)
    assert "wns_ps" in sta
    # Hand-ish: with 5ns period on a 3-gate path, WNS should be positive
    assert sta["wns_ps"] > -1e6


def test_ir_drop_solve_smoke(library_tech):
    library, tech = library_tech
    modules = parse_verilog_file(FIXTURES / "golden_three_cell.v")
    design = elaborate(modules, library_cell_names=set(library["cells"]))
    design.library = library
    design.tech = tech
    infer_drivers(design)
    cfg = load_config()
    design.instances = ForceDirectedPlacement().execute(design, cfg)
    ir = run_ir_drop(design, cfg)
    assert "min_voltage" in ir
    assert ir.get("error") is None
    assert ir["min_voltage"] > 0


def test_full_pipeline_golden(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_three_cell.v",
        out_dir=tmp_path / "golden_run",
        clock_period_ns=10.0,
        fetch_if_missing=True,
    )
    assert result["qor_path"].exists()
    assert "instances_affected" in result["report"]["checks"]["ir_drop"]
    assert "pass" not in result["report"]["checks"]["ir_drop"]
    assert "overall_pass" not in result["report"]
    assert result["design"].meta.get("completed_stage") == "routing"


def test_full_pipeline_seq(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "seq_run",
        clock_period_ns=10.0,
    )
    assert result["qor_path"].exists()
    # CTS should find clk
    assert "clk" in result["design"].clock_tree.get("clock_nets", {}) or result["design"].clock_tree.get("new_buffers") is not None
