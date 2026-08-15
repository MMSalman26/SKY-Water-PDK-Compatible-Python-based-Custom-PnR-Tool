"""STA fidelity: CRPR, SDC, loop breakers, D2M, SPEF."""

from __future__ import annotations

from pathlib import Path

from pnr_tool.checkers.sdc import parse_sdc_text
from pnr_tool.checkers.sta import _d2m_delay, _topo_order, run_sta
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.report.spef import write_spef

import networkx as nx


def _ff_lib():
    return {
        "is_sequential": True,
        "pins": {
            "CLK": {"direction": "input", "is_clock": True, "capacitance": 0.002},
            "D": {"direction": "input", "capacitance": 0.002},
            "Q": {"direction": "output", "capacitance": 0.0},
        },
    }


def _two_flop_design() -> DesignObject:
    design = DesignObject(name="crpr", die_area=(0, 0, 40, 20))
    design.tech = {
        "layers": {"met2": {"r_per_um": 0.1, "c_per_um": 1e-16, "width_um": 0.14}},
        "vdd": 1.8,
        "width_ref_um": 0.14,
    }
    design.ports = {
        "clk": {"direction": "input", "net": "clk"},
        "d": {"direction": "input", "net": "d"},
        "q": {"direction": "output", "net": "q"},
    }
    design.cells = {
        "f0": {"cell_type": "sky130_fd_sc_hd__dfxtp_1", "pins": {"CLK": "clk", "D": "d", "Q": "n0"}},
        "f1": {"cell_type": "sky130_fd_sc_hd__dfxtp_1", "pins": {"CLK": "clk", "D": "n0", "Q": "q"}},
    }
    design.instances = {
        "f0": {"x": 5.0, "y": 5.0, "orientation": "N", "is_fixed": False, "width": 4.0, "height": 2.72},
        "f1": {"x": 25.0, "y": 5.0, "orientation": "N", "is_fixed": False, "width": 4.0, "height": 2.72},
    }
    design.nets = {
        "clk": {"pins": [("PORT:clk", "PAD"), ("f0", "CLK"), ("f1", "CLK")], "driver": ("PORT:clk", "PAD")},
        "d": {"pins": [("PORT:d", "PAD"), ("f0", "D")], "driver": ("PORT:d", "PAD")},
        "n0": {"pins": [("f0", "Q"), ("f1", "D")], "driver": ("f0", "Q")},
        "q": {"pins": [("f1", "Q"), ("PORT:q", "PAD")], "driver": ("f1", "Q")},
    }
    design.library = {"cells": {"sky130_fd_sc_hd__dfxtp_1": _ff_lib()}, "corners": {}}
    design.routing = {
        "d": [{"layer": "met2", "x1": 0.0, "y1": 5.0, "x2": 5.0, "y2": 5.0}],
        "n0": [{"layer": "met2", "x1": 9.0, "y1": 5.0, "x2": 25.0, "y2": 5.0}],
        "q": [{"layer": "met2", "x1": 29.0, "y1": 5.0, "x2": 40.0, "y2": 5.0}],
        "clk": [{"layer": "met2", "x1": 0.0, "y1": 8.0, "x2": 25.0, "y2": 8.0}],
    }
    design.clock_tree = {
        "new_buffers": {},
        "clock_nets": {
            "clk": {
                "root": "__clkport__",
                "levels": [{"net": "clk", "buffer": "__clkport__", "xy": [0.0, 8.0]}],
                "sinks": {"clk": ["f0", "f1"]},
            }
        },
    }
    return design


def test_crpr_reduces_hold_pessimism():
    design = _two_flop_design()
    cfg = load_config()
    cfg.setdefault("sta", {})
    cfg["sta"]["use_crpr"] = False
    off = run_sta(design, cfg, clock_period_ns=10.0)
    cfg["sta"]["use_crpr"] = True
    on = run_sta(design, cfg, clock_period_ns=10.0)
    hold_off = [e for e in off["endpoints"] if e.get("check") == "hold" and e["endpoint"].startswith("f1/")]
    hold_on = [e for e in on["endpoints"] if e.get("check") == "hold" and e["endpoint"].startswith("f1/")]
    assert hold_off and hold_on
    assert hold_on[0]["slack_ns"] >= hold_off[0]["slack_ns"] - 1e-12
    assert on["use_crpr"] is True
    assert float(hold_on[0].get("crpr_ns") or 0.0) >= float(hold_off[0].get("crpr_ns") or 0.0)


def test_sdc_parse_openlane_subset():
    text = """
    create_clock -name clk -period 24.0 [get_ports clk]
    set_clock_uncertainty 0.25 [get_clocks clk]
    set_input_delay -clock clk 2.0 [get_ports {d rst}]
    set_output_delay -clock clk 1.5 [get_ports q]
    set_false_path -from [get_ports rst] -to [get_ports q]
    set_multicycle_path -setup 2 -from [get_cells f0] -to [get_cells f1]
    """
    sdc = parse_sdc_text(text)
    assert sdc["clocks"][0]["period_ns"] == 24.0
    assert sdc["uncertainty_ns"] == 0.25
    assert sdc["input_delays"]
    assert sdc["output_delays"]
    assert sdc["false_paths"]
    assert sdc["multicycle"][0]["cycles"] == 2


def test_sdc_false_path_drops_endpoint():
    design = _two_flop_design()
    cfg = load_config()
    cfg.setdefault("sta", {})
    cfg["sta"]["sdc"] = {
        "clocks": [{"name": "clk", "period_ns": 10.0, "ports": ["clk"]}],
        "input_delays": [],
        "output_delays": [],
        "false_paths": [{"from": ["f0"], "to": ["f1"]}],
        "multicycle": [],
        "uncertainty_ns": 0.05,
    }
    sta = run_sta(design, cfg, clock_period_ns=10.0)
    flop_eps = [e for e in sta["endpoints"] if e["endpoint"].startswith("f1/")]
    assert not flop_eps


def test_loop_breaker_reports_cycles():
    g = nx.DiGraph()
    g.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
    order, broken = _topo_order(g, ["a", "b", "c"])
    assert broken >= 1
    assert set(order) == {"a", "b", "c"}


def test_d2m_delay_ge_elmore():
    assert _d2m_delay(1.0) > 1.0
    design = _two_flop_design()
    cfg = load_config()
    cfg.setdefault("sta", {})
    cfg["sta"]["wire_model"] = "tree_elmore"
    elmore = run_sta(design, cfg, clock_period_ns=10.0)
    cfg["sta"]["wire_model"] = "d2m"
    d2m = run_sta(design, cfg, clock_period_ns=10.0)
    assert d2m["wire_model"] == "d2m"
    # D2M is a longer interconnect delay → setup WNS should not improve
    assert d2m["setup_wns_ps"] <= elmore["setup_wns_ps"] + 1e-6


def test_spef_write(tmp_path):
    design = _two_flop_design()
    path = write_spef(design, tmp_path / "t.spef")
    text = Path(path).read_text(encoding="utf-8")
    assert "*SPEF" in text
    assert "*D_NET" in text
    assert "n0" in text


def test_path_report_has_cell_net_split():
    design = _two_flop_design()
    sta = run_sta(design, load_config(), clock_period_ns=10.0)
    crit = sta.get("critical_path") or {}
    assert "pins" in crit
    if crit.get("stages"):
        assert "cell_delay_ns" in crit
        assert "net_delay_ns" in crit
    assert "use_ceff" in sta
    assert "rc_min_scale" in sta
    assert "loops_broken" in sta


def test_compare_opensta_skips_without_sta(tmp_path, monkeypatch):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "compare_opensta.py"
    spec = importlib.util.spec_from_file_location("compare_opensta", script)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    monkeypatch.setattr(harness.shutil, "which", lambda _n: None)
    design = _two_flop_design()
    result = harness.compare(design, tmp_path / "cmp", 10.0, load_config())
    assert result["opensta"]["skipped"] is True
    assert (tmp_path / "cmp" / "compare.json").exists()
    assert (tmp_path / "cmp" / f"{design.name}.spef").exists()
