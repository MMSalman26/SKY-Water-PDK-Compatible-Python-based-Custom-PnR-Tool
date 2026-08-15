"""Robust STA / IR checker unit tests."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.algorithms.power_plan import plan_power
from pnr_tool.checkers.ir_drop import (
    run_ir_drop,
    _instance_currents,
    _instance_ir_drops,
    _seg_resistance,
)
from pnr_tool.checkers.sta import run_sta
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.pipeline.run import run_pipeline
from pnr_tool.report.qor import build_qor_report

FIXTURES = Path(__file__).parent / "fixtures"


def test_power_segments_have_width():
    design = DesignObject(name="p", die_area=(0.0, 0.0, 40.0, 40.0))
    design.tech = {"site_height_um": 2.72}
    grid = plan_power(design, load_config())
    assert all("width_um" in s for s in grid["segments"])
    assert any(s["width_um"] > 0 for s in grid["segments"])


def test_width_aware_resistance():
    layers = {"met5": {"r_per_um": 0.029}}
    r_narrow = _seg_resistance(10.0, "met5", 0.48, layers, 0.48)
    r_wide = _seg_resistance(10.0, "met5", 1.6, layers, 0.48)
    assert r_wide < r_narrow


def test_instance_currents_deterministic():
    cfg = load_config()
    design = DesignObject(name="c", die_area=(0, 0, 20, 20))
    design.tech = {"vdd": 1.8}
    design.library = {
        "cells": {
            "inv": {
                "leakage_power": 10.0,  # nW with default unit
                "pins": {"Y": {"direction": "output", "capacitance": 0.01}},
            }
        }
    }
    design.cells = {"u0": {"cell_type": "inv", "pins": {"Y": "n1"}}}
    design.instances = {"u0": {"x": 1.0, "y": 1.0}}
    design.nets = {"n1": {"driver": ("u0", "Y"), "pins": [("u0", "Y")]}}
    xs, ys, i1, s1 = _instance_currents(design, cfg, 1.8, 10.0)
    _, _, i2, _ = _instance_currents(design, cfg, 1.8, 10.0)
    assert i1.shape == i2.shape
    assert abs(float(i1[0]) - float(i2[0])) < 1e-18
    assert s1["total_a"] > 0
    assert s1["model"] == "leakage_plus_alpha_cv2f"


def test_instance_ir_drops_count_and_pct():
    import numpy as np

    names = ["a", "b", "c"]
    volts = np.array([1.80, 1.70, 1.60])  # threshold 1.71 @ 0.95*1.8
    rows, n = _instance_ir_drops(names, volts, vdd=1.8, threshold=1.71, rail="VPWR")
    assert n == 2
    assert len(rows) == 2
    assert rows[0]["instance"] == "c"
    assert abs(rows[0]["drop_pct"] - (100.0 * 0.2 / 1.8)) < 1e-9
    assert rows[1]["instance"] == "b"


def test_ir_no_negative_voltage_with_floating_stub():
    """Disconnected PDN stubs must not poison the rail solve into negative V."""
    import numpy as np
    from pnr_tool.checkers.ir_drop import _solve_rail

    # Two connected strap nodes + one floating stub node (index 2)
    node_xy = [(0.0, 0.0), (10.0, 0.0), (50.0, 50.0)]
    edges = [(0, 1, 10.0)]  # only 0—1 connected; node 2 floating
    xs = np.array([5.0, 5.0])
    ys = np.array([0.0, 0.0])
    cur = np.array([1e-3, 1e-3])
    rail = _solve_rail(
        node_xy=node_xy,
        edges=edges,
        supply_nodes=[0],
        supply_voltage=1.8,
        currents_xy=(xs, ys, cur),
        external_r=0.0,
        rail="VPWR",
        is_ground=False,
        min_ratio=0.95,
        vdd=1.8,
    )
    assert rail.get("error") is None
    assert int(rail.get("floating_count", 0)) >= 1
    V = np.asarray(rail["V"], dtype=float)
    conn = rail["connected"]
    V_conn = V[conn]
    assert np.all(np.isfinite(V_conn))
    assert float(np.min(V_conn)) >= 0.0
    assert float(np.max(V_conn)) <= 1.8 + 1e-9
    assert float(rail["min_voltage"]) >= 0.0
    assert float(rail["max_drop"]) < 1.0  # physical, not collapsed


def test_ir_dual_rail_power_grid():
    design = DesignObject(name="ir", die_area=(0.0, 0.0, 40.0, 40.0))
    design.tech = {
        "vdd": 1.8,
        "site_height_um": 2.72,
        "layers": {
            "met1": {"r_per_um": 0.125},
            "met4": {"r_per_um": 0.047},
            "met5": {"r_per_um": 0.029},
        },
    }
    design.library = {
        "cells": {
            "inv": {
                "leakage_power": 100.0,
                "pins": {"Y": {"direction": "output", "capacitance": 0.02}},
            }
        }
    }
    design.cells = {f"u{i}": {"cell_type": "inv", "pins": {"Y": f"n{i}"}} for i in range(4)}
    design.instances = {
        f"u{i}": {"x": 5.0 + i * 5, "y": 5.0 + i * 3} for i in range(4)
    }
    design.nets = {
        f"n{i}": {"driver": (f"u{i}", "Y"), "pins": [(f"u{i}", "Y")]} for i in range(4)
    }
    cfg = load_config()
    plan_power(design, cfg)
    ir = run_ir_drop(design, cfg, clock_period_ns=10.0)
    assert ir["ir_mode"] == "power_grid"
    assert "vdd_rail" in ir and "vss_rail" in ir
    assert ir["vss_rail"]["nodes"] > 0
    assert "max_supply_collapse" in ir
    assert ir["total_current_a"] > 0
    assert ir.get("error") is None
    assert "instances_affected" in ir
    assert ir["violation_count"] == ir["instances_affected"]
    assert isinstance(ir.get("instance_drops"), list)
    assert float(ir["min_voltage"]) >= 0.0
    assert float(ir["max_ir_drop"]) >= 0.0
    assert float(ir["min_voltage"]) <= float(ir["vdd"]) + 1e-9
    for row in ir["instance_drops"]:
        assert "instance" in row and "drop_pct" in row
        assert float(row["voltage"]) >= 0.0
        assert float(row["drop_pct"]) <= 100.0 + 1e-9


def test_sta_setup_hold_and_cts_fields(tmp_path):
    result = run_pipeline(
        FIXTURES / "golden_seq.v",
        out_dir=tmp_path / "seq_sta",
        fetch_if_missing=True,
        layout_images=False,
        clock_period_ns=10.0,
    )
    sta = run_sta(result["design"], load_config(), clock_period_ns=10.0)
    assert "setup_wns_ps" in sta and "hold_wns_ps" in sta
    assert sta["wire_model"] == "tree_elmore"
    assert "cts_latency" in sta
    assert "critical_path" in sta
    checks = {e.get("check") for e in sta["endpoints"]}
    assert "setup" in checks and "hold" in checks
    report = result["report"]
    assert "setup_wns_ps" in report["checks"]["sta"]
    assert "hold_wns_ps" in report["checks"]["sta"]
    eps = report["sta_details"]["endpoints"]
    assert any(e.get("check") == "setup" for e in eps)
    assert "vdd_rail" in report["ir_details"]
    assert report["checks"]["ir_drop"].get("via_edges", 0) >= 0
    assert any("tree_elmore" in n or "Elmore" in n for n in report["fidelity_notes"])


def test_qor_ir_sta_shape():
    sta = {
        "setup_wns_ps": 10.0,
        "setup_tns_ps": 0.0,
        "hold_wns_ps": 5.0,
        "hold_tns_ps": 0.0,
        "wns_ps": 10.0,
        "tns_ps": 0.0,
        "corner": "ff",
        "clock_period_ns": 10.0,
        "uncertainty_ns": 0.05,
        "setup_ns": 0.05,
        "hold_ns": 0.02,
        "wire_model": "tree_elmore",
        "cts_latency": {"min_ns": 0.0, "max_ns": 0.1, "mean_ns": 0.05, "skew_ns": 0.1},
        "critical_path": {"endpoint": "f0/D", "pins": ["a/Y", "f0/D"], "slack_ns": 8.0},
        "endpoints": [
            {
                "check": "setup",
                "endpoint": "f0/D",
                "arrival_ns": 1.0,
                "required_ns": 9.0,
                "slack_ns": 8.0,
            }
        ],
        "summary": {
            "setup_endpoints": 1,
            "hold_endpoints": 1,
            "setup_failing": 0,
            "hold_failing": 2,
        },
    }
    ir = {
        "vdd": 1.8,
        "min_voltage": 1.75,
        "max_drop": 0.05,
        "max_ir_drop": 0.05,
        "max_ground_bounce": 0.01,
        "max_supply_collapse": 0.06,
        "violation_count": 2,
        "instances_affected": 2,
        "instance_drops": [
            {"instance": "u0", "drop_pct": 8.0, "drop_v": 0.144, "voltage": 1.656, "rail": "VPWR"},
            {"instance": "u1", "drop_pct": 6.0, "drop_v": 0.108, "voltage": 1.692, "rail": "VPWR"},
        ],
        "violations": [
            {"instance": "u0", "drop_pct": 8.0, "drop_v": 0.144, "voltage": 1.656, "rail": "VPWR"},
        ],
        "ir_mode": "power_grid",
        "via_edges": 4,
        "solve_method": {"vdd": "spsolve", "vss": "spsolve"},
        "total_current_a": 1e-4,
        "floating_nodes": 0,
        "vdd_rail": {"min_voltage": 1.75, "max_drop": 0.05, "nodes": 10, "edges": 12, "instances_affected": 2},
        "vss_rail": {"min_voltage": 0.0, "max_drop": 0.01, "nodes": 8, "edges": 10},
        "currents": {"total_a": 1e-4, "avg_cell_a": 1e-5, "model": "leakage_plus_alpha_cv2f"},
        "grid": {"nodes": 18, "edges": 22, "sample_um": 2.0, "via_edges": 4},
    }
    drc = {"violation_count": 0, "violations": [], "counts_by_type": {}}
    report = build_qor_report("t", drc, sta, ir, load_config())
    assert report["checks"]["sta"]["setup_wns_ps"] == 10.0
    assert report["checks"]["sta"]["setup_violations"] == 0
    assert report["checks"]["sta"]["hold_violations"] == 2
    assert report["checks"]["ir_drop"]["max_supply_collapse"] == 0.06
    assert report["checks"]["ir_drop"]["instances_affected"] == 2
    assert "pass" not in report["checks"]["ir_drop"]
    assert "overall_pass" not in report
    assert report["ir_details"]["vdd_rail"]["nodes"] == 10
    assert report["ir_details"]["instance_drops"][0]["instance"] == "u0"
    assert report["sta_details"]["endpoints"][0]["check"] == "setup"
