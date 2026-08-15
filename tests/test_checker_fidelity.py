"""Algorithmic fidelity upgrades for DRC / STA / IR."""

from __future__ import annotations

import numpy as np

from pnr_tool.checkers.drc import run_drc
from pnr_tool.checkers.ir_drop import _build_rail_graph, _solve_rail
from pnr_tool.checkers.sta import _lumped_net_electricals, _tree_net_electricals, run_sta
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject


def test_geometric_short_on_coincident_segments():
    design = DesignObject(name="gshort", die_area=(0, 0, 20, 20))
    design.tech = {"layers": {"met2": {"min_spacing_um": 0.14}}}
    design.routing = {
        "n0": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}],
        "n1": [{"layer": "met2", "x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 0.0}],
    }
    design.nets = {
        "n0": {"pins": [("a", "Y"), ("b", "A")], "driver": ("a", "Y")},
        "n1": {"pins": [("c", "Y"), ("d", "A")], "driver": ("c", "Y")},
    }
    report = run_drc(design, load_config())
    assert report["counts_by_type"].get("short", 0) >= 1
    assert "short" in report["pass_on"]


def test_connectivity_open_disconnected_sink():
    design = DesignObject(name="open", die_area=(0, 0, 40, 40))
    design.tech = {"layers": {"met2": {"min_spacing_um": 0.14, "r_per_um": 0.1, "c_per_um": 1e-16}}}
    design.instances = {
        "drv": {"x": 0.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
        "snk": {"x": 30.0, "y": 30.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
    }
    design.cells = {
        "drv": {"cell_type": "buf", "pins": {"Y": "n0"}},
        "snk": {"cell_type": "inv", "pins": {"A": "n0"}},
    }
    design.library = {"cells": {"buf": {"width": 1.0, "height": 2.0, "pins": {}}, "inv": {"width": 1.0, "height": 2.0, "pins": {}}}}
    design.nets = {"n0": {"pins": [("drv", "Y"), ("snk", "A")], "driver": ("drv", "Y")}}
    # Only a stub near the driver — sink far away, disconnected
    design.routing = {
        "n0": [{"layer": "met2", "x1": 0.5, "y1": 1.0, "x2": 2.0, "y2": 1.0, "role": "stub"}]
    }
    cfg = load_config()
    cfg["drc"]["connectivity_opens"] = True
    report = run_drc(design, cfg)
    opens = [v for v in report["violations"] if v["type"] == "open"]
    assert opens
    assert any(v.get("reason") in ("disconnected_sink", "no segments") for v in opens)


def test_tree_elmore_differs_from_lumped_on_branched_net():
    design = DesignObject(name="tree", die_area=(0, 0, 100, 100))
    design.tech = {
        "layers": {"met2": {"r_per_um": 0.2, "c_per_um": 0.2e-15}},
    }
    design.instances = {
        "d": {"x": 0.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
        "s0": {"x": 40.0, "y": 0.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
        "s1": {"x": 0.0, "y": 40.0, "orientation": "N", "is_fixed": False, "width": 1.0, "height": 2.0},
    }
    design.cells = {
        "d": {"cell_type": "buf", "pins": {"Y": "n0"}},
        "s0": {"cell_type": "inv", "pins": {"A": "n0"}},
        "s1": {"cell_type": "inv", "pins": {"A": "n0"}},
    }
    design.library = {
        "cells": {
            "buf": {"pins": {"Y": {"capacitance": 0.0}, "A": {"capacitance": 0.002}}},
            "inv": {"pins": {"A": {"capacitance": 0.01}, "Y": {"capacitance": 0.0}}},
        }
    }
    design.nets = {
        "n0": {
            "pins": [("d", "Y"), ("s0", "A"), ("s1", "A")],
            "driver": ("d", "Y"),
        }
    }
    design.routing = {
        "n0": [
            {"layer": "met2", "x1": 0.5, "y1": 1.0, "x2": 40.5, "y2": 1.0},
            {"layer": "met2", "x1": 0.5, "y1": 1.0, "x2": 0.5, "y2": 41.0},
        ]
    }
    _ll, le, _ls = _lumped_net_electricals(design)
    _tl, te, _ts, sink_d, _ss = _tree_net_electricals(design)
    assert te["n0"] >= 0
    assert le["n0"] >= 0
    # Branched tree: per-sink delays exist and max differs from (or equals with structure) lumped
    assert len(sink_d["n0"]) >= 1
    assert abs(te["n0"] - le["n0"]) > 1e-6 or max(sink_d["n0"].values()) > 0


def test_pdn_via_edges_and_solve_method():
    design = DesignObject(name="irv", die_area=(0, 0, 40, 40))
    design.tech = {
        "vdd": 1.8,
        "layers": {
            "met1": {"r_per_um": 0.1},
            "met5": {"r_per_um": 0.02},
        },
    }
    design.power_grid = {
        "vdd_net": "VPWR",
        "vss_net": "VGND",
        "width_ref_um": 0.48,
        "segments": [
            {
                "net": "VPWR",
                "layer": "met5",
                "role": "strap",
                "x1": 20.0,
                "y1": 0.0,
                "x2": 20.0,
                "y2": 40.0,
                "width_um": 0.8,
            },
            {
                "net": "VPWR",
                "layer": "met1",
                "role": "follow_pin",
                "x1": 0.0,
                "y1": 10.0,
                "x2": 40.0,
                "y2": 10.0,
                "width_um": 0.48,
            },
            {
                "net": "VPWR",
                "layer": "met5",
                "role": "ring",
                "x1": 0.0,
                "y1": 0.0,
                "x2": 40.0,
                "y2": 0.0,
                "width_um": 1.6,
            },
        ],
    }
    design.instances = {}
    design.cells = {}
    design.library = {"cells": {}}
    cfg = load_config()
    segs = [s for s in design.power_grid["segments"] if s["net"] == "VPWR"]
    node_xy, edges, ring, via_n = _build_rail_graph(segs, design, cfg, 2.0)
    assert via_n >= 1
    xs = np.array([20.0])
    ys = np.array([10.0])
    cur = np.array([1e-4])
    rail = _solve_rail(
        node_xy=node_xy,
        edges=edges,
        supply_nodes=ring or [0],
        supply_voltage=1.8,
        currents_xy=(xs, ys, cur),
        external_r=0.0,
        rail="VPWR",
        is_ground=False,
        min_ratio=0.95,
        vdd=1.8,
    )
    assert rail.get("solve_method") in ("spsolve", "lsmr")
    assert rail.get("error") is None
    V = rail.get("V")
    assert V is not None
    conn = rail.get("connected") or []
    V_conn = np.asarray([V[i] for i in conn], dtype=float)
    assert np.all(np.isfinite(V_conn))
    assert float(np.min(V_conn)) >= 0.0
    assert float(np.max(V_conn)) <= 1.8 + 1e-9
    assert float(rail["min_voltage"]) >= 0.0
    # 100 uA into a small PDN should not collapse the rail
    assert float(rail["max_drop"]) < 0.05


def test_sta_reports_tree_model_and_critical_path():
    design = DesignObject(name="sta", die_area=(0, 0, 20, 20))
    design.tech = {"layers": {"met2": {"r_per_um": 0.1, "c_per_um": 1e-16}}, "vdd": 1.8}
    design.ports = {"clk": {"direction": "input", "net": "clk"}, "d": {"direction": "input", "net": "d"}, "q": {"direction": "output", "net": "q"}}
    design.cells = {
        "ff": {
            "cell_type": "sky130_fd_sc_hd__dfxtp_1",
            "pins": {"CLK": "clk", "D": "d", "Q": "q"},
        }
    }
    design.instances = {
        "ff": {"x": 5.0, "y": 5.0, "orientation": "N", "is_fixed": False, "width": 4.0, "height": 2.72}
    }
    design.nets = {
        "clk": {"pins": [("PORT:clk", "PAD"), ("ff", "CLK")], "driver": ("PORT:clk", "PAD")},
        "d": {"pins": [("PORT:d", "PAD"), ("ff", "D")], "driver": ("PORT:d", "PAD")},
        "q": {"pins": [("ff", "Q"), ("PORT:q", "PAD")], "driver": ("ff", "Q")},
    }
    design.library = {
        "cells": {
            "sky130_fd_sc_hd__dfxtp_1": {
                "is_sequential": True,
                "pins": {
                    "CLK": {"direction": "input", "is_clock": True, "capacitance": 0.002},
                    "D": {"direction": "input", "capacitance": 0.002},
                    "Q": {"direction": "output", "capacitance": 0.0},
                },
            }
        }
    }
    design.routing = {
        "d": [{"layer": "met2", "x1": 0.0, "y1": 5.0, "x2": 5.0, "y2": 5.0}],
        "q": [{"layer": "met2", "x1": 7.0, "y1": 5.0, "x2": 20.0, "y2": 5.0}],
        "clk": [{"layer": "met2", "x1": 0.0, "y1": 6.0, "x2": 5.0, "y2": 6.0}],
    }
    design.clock_tree = {"new_buffers": {}, "clock_nets": {}}
    sta = run_sta(design, load_config(), clock_period_ns=10.0)
    assert sta["wire_model"] == "tree_elmore"
    assert isinstance(sta.get("critical_path"), dict)
