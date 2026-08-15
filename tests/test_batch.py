"""Batch runner + scoreboard + QoR schema 2 metrics."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from pnr_tool.pipeline.batch import build_scoreboard_from_runs, run_batch, write_scoreboard
from pnr_tool.pipeline.run import run_pipeline
from pnr_tool.report.html_report import write_html_report, load_reports
from pnr_tool.report.layout_data import build_layout_view
from pnr_tool.report.metrics import compute_hpwl_um, compute_routed_wl_um, count_vias
from pnr_tool.design.object import DesignObject

FIXTURES = Path(__file__).parent / "fixtures"


def test_qor_schema2_metrics(pdk_cache, tmp_path):
    result = run_pipeline(
        netlist=FIXTURES / "golden_three_cell.v",
        out_dir=tmp_path / "qor2",
        layout_images=False,
    )
    report = result["report"]
    assert report["qor_schema"] == 2
    assert set(report["algorithms"]) == {"placement", "clock_opt", "routing"}
    assert "total" in report["timing_s"]
    assert report["timing_s"]["total"] > 0
    metrics = report["metrics"]
    for key in (
        "num_cells",
        "num_nets",
        "die_area_um2",
        "hpwl_um",
        "routed_wl_um",
        "via_count",
        "cts_buffer_count",
        "routing_fallback_count",
    ):
        assert key in metrics
    assert metrics["num_cells"] >= 1
    assert metrics["hpwl_um"] >= 0
    assert (tmp_path / "qor2" / "layout_view.json").exists()


def test_metrics_helpers_smoke():
    design = DesignObject(name="m", die_area=(0, 0, 10, 10))
    design.cells = {"a": {"cell_type": "x"}, "b": {"cell_type": "x"}}
    design.instances = {
        "a": {"x": 0, "y": 0, "width": 1, "height": 1},
        "b": {"x": 4, "y": 2, "width": 1, "height": 1},
    }
    design.nets = {"n0": {"pins": [("a", "Y"), ("b", "A")]}}
    design.routing = {
        "n0": [
            {"layer": "met1", "x1": 0, "y1": 0, "x2": 4, "y2": 0},
            {"layer": "met2", "x1": 4, "y1": 0, "x2": 4, "y2": 2},
        ]
    }
    assert compute_hpwl_um(design) == pytest.approx(6.0)
    assert compute_routed_wl_um(design.routing) == pytest.approx(6.0)
    assert count_vias(design.routing) == 1


def test_batch_two_goldens(pdk_cache, tmp_path):
    manifest = {
        "seed": 1,
        "designs": [
            {"netlist": str(FIXTURES / "golden_three_cell.v"), "top": None, "clock_period_ns": 10},
            {"netlist": str(FIXTURES / "golden_seq.v"), "top": None, "clock_period_ns": 10},
        ],
        "algorithms": [
            {
                "name": "baseline",
                "placement": "default",
                "clock_opt": "default",
                "routing": "default",
            }
        ],
    }
    man_path = tmp_path / "experiments.yaml"
    man_path.write_text(yaml.dump(manifest), encoding="utf-8")
    out = tmp_path / "batch_out"
    summary = run_batch(man_path, out, layout_images=False, refresh_html=True)
    assert len(summary["rows"]) == 2
    assert not summary["errors"]
    csv_path = out / "scoreboard.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert {r["algo_name"] for r in rows} == {"baseline"}
    assert (out / "scoreboard.json").exists()
    assert (out / "index.html").exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Compare" in html
    assert "Layout viewer" in html or "layout-viewer" in html


def test_scoreboard_from_existing(tmp_path):
    report = {
        "qor_schema": 2,
        "design": "toy",
        "algorithms": {"placement": "default", "clock_opt": "default", "routing": "default"},
        "timing_s": {"placement": 0.1, "clock_opt": 0.1, "routing": 0.2, "drc": 0.1, "sta": 0.1, "ir_drop": 0.1, "total": 0.7},
        "memory_mb": 12.5,
        "metrics": {
            "num_cells": 3,
            "num_nets": 2,
            "die_area_um2": 100.0,
            "hpwl_um": 5.0,
            "routed_wl_um": 6.0,
            "via_count": 1,
            "cts_buffer_count": 0,
            "routing_fallback_count": 0,
        },
        "checks": {
            "drc": {"violations": 0},
            "sta": {"wns_ps": 1.0, "hold_wns_ps": 0.0},
            "ir_drop": {"violations": 0, "instances_affected": 0, "max_ir_drop": 0.01},
        },
        "meta": {},
    }
    qor = tmp_path / "baseline" / "toy" / "toy.qor.json"
    qor.parent.mkdir(parents=True)
    qor.write_text(json.dumps(report), encoding="utf-8")
    rows = build_scoreboard_from_runs(tmp_path)
    assert len(rows) == 1
    assert rows[0]["algo_name"] == "baseline"
    assert rows[0]["hpwl_um"] == 5.0
    out = tmp_path / "scoreboard.csv"
    write_scoreboard(rows, out)
    assert out.exists()


def test_layout_view_schema():
    design = DesignObject(name="lv", die_area=(0, 0, 20, 10))
    design.cells = {"u0": {"cell_type": "inv"}}
    design.library = {"cells": {"inv": {"width": 1.0, "height": 2.0}}}
    design.instances = {"u0": {"x": 1, "y": 2, "width": 1, "height": 2}}
    design.routing = {"n0": [{"layer": "met1", "x1": 0, "y1": 0, "x2": 5, "y2": 0}]}
    design.meta["port_positions"] = {"a": (0.0, 5.0)}
    view = build_layout_view(design, stage="routing")
    assert view["schema"] == 2
    assert view["die_area"] == [0.0, 0.0, 20.0, 10.0]
    assert len(view["cells"]) == 1
    assert view["cells"][0]["name"] == "u0"
    assert len(view["segments"]) == 1
    assert view["ports"][0]["name"] == "a"


def test_html_contains_analysis_markers(tmp_path):
    qor = {
        "qor_schema": 2,
        "design": "d",
        "algorithms": {"placement": "default", "clock_opt": "default", "routing": "default"},
        "timing_s": {"total": 1.0, "placement": 0.2, "clock_opt": 0.2, "routing": 0.3, "drc": 0.1, "sta": 0.1, "ir_drop": 0.1},
        "metrics": {"hpwl_um": 1, "routed_wl_um": 2, "via_count": 0},
        "checks": {
            "drc": {"violations": 0},
            "sta": {"wns_ps": 0, "hold_wns_ps": 0},
            "ir_drop": {"violations": 1, "instances_affected": 1, "max_ir_drop": 0.1, "vdd": 1.8, "threshold": 1.71},
        },
        "ir_details": {
            "instances_affected": 1,
            "instance_drops": [{"instance": "u0", "drop_pct": 5.5, "drop_v": 0.1, "voltage": 1.7, "rail": "VPWR"}],
            "max_ir_drop": 0.1,
        },
        "meta": {"num_cells": 1, "num_nets": 1},
        "_layout_view": "layout_view.json",
    }
    path = write_html_report([qor], tmp_path / "index.html")
    text = path.read_text(encoding="utf-8")
    assert "Compare" in text
    assert "data-tab=\"compare\"" in text or 'id="tab-compare"' in text
    assert "layout-viewer" in text or "Layout viewer" in text
    assert "chart-wl-wns" in text or "WL vs Setup WNS" in text or "WL vs WNS" in text
    assert "Timing analysis" in text or "staFilters" in text
    assert ">PASS<" not in text and ">FAIL<" not in text
    assert "passCount" not in text
    assert "Drop %" in text
    assert "Instances affected" in text
    assert "Power integrity" in text or "max_supply_collapse" in text
