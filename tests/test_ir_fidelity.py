"""IR fidelity upgrades: follow-pin taps, via R, sources, SPICE, coupled, J, reports."""

from __future__ import annotations

import numpy as np

from pnr_tool.checkers.ir_drop import (
    _build_rail_graph,
    _build_rail_graph_ex,
    _instance_currents,
    _rail_vdd,
    _seg_resistance,
    _solve_rail,
    run_ir_drop,
    write_ir_spice,
)
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.report.html_report import write_html_report
from pnr_tool.report.qor import build_qor_report


def _tiny_pdn(*, n_inst: int = 4, via_r: bool = True) -> DesignObject:
    design = DesignObject(name="irf", die_area=(0.0, 0.0, 40.0, 40.0))
    met1 = {"r_per_um": 0.125}
    met4 = {"r_per_um": 0.047}
    met5 = {"r_per_um": 0.029}
    if via_r:
        met1["via_r_ohm"] = 50.0
        met4["via_r_ohm"] = 5.0
        met5["via_r_ohm"] = 3.0
    design.tech = {
        "vdd": 1.8,
        "site_height_um": 2.72,
        "layers": {"met1": met1, "met4": met4, "met5": met5},
    }
    design.library = {
        "cells": {
            "inv": {
                "leakage_power": 5.0e4,
                "pins": {"Y": {"direction": "output", "capacitance": 0.05}},
            }
        }
    }
    design.cells = {f"u{i}": {"cell_type": "inv", "pins": {"Y": f"n{i}"}} for i in range(n_inst)}
    design.instances = {f"u{i}": {"x": 4.0 + i * 6.0, "y": 9.0} for i in range(n_inst)}
    design.nets = {
        f"n{i}": {"driver": (f"u{i}", "Y"), "pins": [(f"u{i}", "Y")]} for i in range(n_inst)
    }
    design.power_grid = {
        "vdd_net": "VPWR",
        "vss_net": "VGND",
        "vdd_layer": "met5",
        "vss_layer": "met4",
        "width_ref_um": 0.48,
        "segments": [
            {"net": "VPWR", "layer": "met5", "role": "ring", "x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 0.0, "width_um": 1.6},
            {"net": "VPWR", "layer": "met5", "role": "ring", "x1": 40.0, "y1": 0.0, "x2": 40.0, "y2": 40.0, "width_um": 1.6},
            {"net": "VPWR", "layer": "met5", "role": "ring", "x1": 40.0, "y1": 40.0, "x2": 0.0, "y2": 40.0, "width_um": 1.6},
            {"net": "VPWR", "layer": "met5", "role": "ring", "x1": 0.0, "y1": 40.0, "x2": 0.0, "y2": 0.0, "width_um": 1.6},
            {"net": "VPWR", "layer": "met5", "role": "strap", "x1": 20.0, "y1": 0.0, "x2": 20.0, "y2": 40.0, "width_um": 0.8},
            {"net": "VPWR", "layer": "met1", "role": "follow_pin", "x1": 0.0, "y1": 10.0, "x2": 40.0, "y2": 10.0, "width_um": 0.48},
            {"net": "VGND", "layer": "met4", "role": "ring", "x1": 2.0, "y1": 2.0, "x2": 38.0, "y2": 2.0, "width_um": 1.6},
            {"net": "VGND", "layer": "met4", "role": "ring", "x1": 38.0, "y1": 2.0, "x2": 38.0, "y2": 38.0, "width_um": 1.6},
            {"net": "VGND", "layer": "met4", "role": "ring", "x1": 38.0, "y1": 38.0, "x2": 2.0, "y2": 38.0, "width_um": 1.6},
            {"net": "VGND", "layer": "met4", "role": "ring", "x1": 2.0, "y1": 38.0, "x2": 2.0, "y2": 2.0, "width_um": 1.6},
            {"net": "VGND", "layer": "met4", "role": "strap", "x1": 0.0, "y1": 20.0, "x2": 40.0, "y2": 20.0, "width_um": 0.8},
            {"net": "VGND", "layer": "met1", "role": "follow_pin", "x1": 0.0, "y1": 12.72, "x2": 40.0, "y2": 12.72, "width_um": 0.48},
        ],
    }
    return design


def test_follow_pin_tap_uses_met1_and_straps_drop_more_than_ring():
    design = _tiny_pdn()
    cfg = load_config()
    segs = [s for s in design.power_grid["segments"] if s["net"] == "VPWR"]
    g = _build_rail_graph_ex(segs, design, cfg, 2.0)
    assert g["via_n"] >= 1
    assert g["follow_pin_nodes"]
    xs = np.array([5.0])
    ys = np.array([10.0])
    cur = np.array([2e-3])
    rail = _solve_rail(
        node_xy=g["node_xy"],
        edges=g["edges"],
        supply_nodes=g["ring_nodes"] or [0],
        supply_voltage=1.8,
        currents_xy=(xs, ys, cur),
        external_r=0.0,
        rail="VPWR",
        is_ground=False,
        min_ratio=0.95,
        vdd=1.8,
        node_layers=g["node_layers"],
        follow_layer="met1",
        tap_mode="follow_pin",
        follow_pin_nodes=g["follow_pin_nodes"],
        edge_meta=g["edge_meta"],
    )
    assert rail.get("error") is None
    inj = rail.get("injection_nodes") or []
    assert inj
    for nid in inj:
        assert g["node_layers"][int(nid)] == "met1"

    cfg_ring = load_config()
    cfg_ring["ir_drop"]["source_type"] = "ring"
    cfg_straps = load_config()
    cfg_straps["ir_drop"]["source_type"] = "straps"
    ir_ring = run_ir_drop(design, cfg_ring, clock_period_ns=10.0)
    ir_straps = run_ir_drop(design, cfg_straps, clock_period_ns=10.0)
    assert ir_ring.get("error") is None and ir_straps.get("error") is None
    assert float(ir_straps["max_ir_drop"]) >= float(ir_ring["max_ir_drop"]) - 1e-12


def test_per_layer_via_r_uses_lower_layer_not_global_fallback():
    design = _tiny_pdn(via_r=True)
    cfg = load_config()
    segs = [s for s in design.power_grid["segments"] if s["net"] == "VPWR"]
    _xy, edges, _ring, via_n = _build_rail_graph(segs, design, cfg, 2.0)
    assert via_n >= 1
    via_gs = [float(e[2]) for e in edges[-via_n:]]
    assert via_gs
    # lower layer at met1/met5 crossing is met1 → 50 Ω, not global 5 Ω
    assert all(abs(g - 1.0 / 50.0) < 1e-9 for g in via_gs)

    design_fb = _tiny_pdn(via_r=False)
    _xy2, edges2, _r2, via_n2 = _build_rail_graph(segs, design_fb, cfg, 2.0)
    assert via_n2 >= 1
    via_gs_fb = [float(e[2]) for e in edges2[-via_n2:]]
    assert all(abs(g - 1.0 / 5.0) < 1e-9 for g in via_gs_fb)
    assert abs(via_gs[0] - via_gs_fb[0]) > 1e-6


def test_corner_vdd_ss_is_1v60():
    design = _tiny_pdn()
    cfg = load_config()
    cfg["ir_drop"]["corner"] = "ss"
    assert abs(_rail_vdd(design, cfg) - 1.60) < 1e-12
    ir = run_ir_drop(design, cfg, clock_period_ns=10.0)
    assert abs(float(ir["vdd"]) - 1.60) < 1e-12
    assert ir.get("corner") == "ss"


def test_current_density_and_unclamped_diagnostics():
    design = _tiny_pdn()
    cfg = load_config()
    ir = run_ir_drop(design, cfg, clock_period_ns=10.0)
    assert ir.get("error") is None
    assert "max_j_ma_per_um" in ir
    assert "j_violations" in ir
    assert isinstance(ir.get("j_histogram"), list)
    assert "min_voltage_raw" in ir
    assert "solver_residual" in ir
    vdd = float(ir["vdd"])
    assert 0.0 <= float(ir["min_voltage"]) <= vdd + 1e-9
    assert ir.get("instance_heatmap") is not None


def test_source_types_ring_straps_bumps():
    design = _tiny_pdn()
    cfg = load_config()
    for st, extra in (("ring", {}), ("straps", {}), ("bumps", {"bump_n": 2})):
        c = load_config()
        c["ir_drop"]["source_type"] = st
        c["ir_drop"].update(extra)
        ir = run_ir_drop(design, c, clock_period_ns=10.0)
        assert ir.get("error") is None, st
        assert ir.get("source_type") == st
        assert int(ir.get("source_count") or 0) >= 1
        if st == "bumps":
            assert int(ir["source_count"]) >= 1


def test_write_ir_spice_has_r_and_sources(tmp_path):
    design = _tiny_pdn()
    ir = run_ir_drop(design, load_config(), clock_period_ns=10.0)
    path = write_ir_spice(ir, tmp_path / "irf_ir.sp")
    text = path.read_text(encoding="utf-8")
    assert path.exists()
    assert any(line.startswith("R") for line in text.splitlines())
    assert any(line.startswith("V") or line.startswith("I") for line in text.splitlines())
    assert ".op" in text and ".end" in text


def test_internal_power_increases_current():
    design = _tiny_pdn(n_inst=1)
    cfg = load_config()
    _, _, i0, s0 = _instance_currents(design, cfg, 1.8, 10.0)
    design.library["cells"]["inv"]["internal_power"] = 1e-6
    _, _, i1, s1 = _instance_currents(design, cfg, 1.8, 10.0)
    assert s0["model"] == "leakage_plus_alpha_cv2f"
    assert s1["model"] == "leakage_plus_alpha_cv2f"
    assert s1.get("internal_power_used") is True
    assert float(i1[0]) > float(i0[0])


def test_coupled_returns_collapse():
    design = _tiny_pdn()
    cfg = load_config()
    cfg["ir_drop"]["coupled"] = True
    ir = run_ir_drop(design, cfg, clock_period_ns=10.0)
    assert ir.get("error") is None
    assert ir.get("coupled") is True
    assert "max_supply_collapse" in ir
    assert float(ir["max_supply_collapse"]) >= 0.0
    assert float(ir["min_voltage"]) >= 0.0


def test_temperature_scale_increases_r():
    layers = {"met5": {"r_per_um": 0.029}}
    r0 = _seg_resistance(10.0, "met5", 0.48, layers, 0.48)
    r1 = _seg_resistance(10.0, "met5", 0.48, layers, 0.48, 1.0 + 0.0039 * 75.0)
    assert r1 > r0
    assert abs(r0 - _seg_resistance(10.0, "met5", 0.48, layers, 0.48, 1.0)) < 1e-18


def test_qor_and_html_include_new_ir_fields(tmp_path):
    design = _tiny_pdn(n_inst=2)
    ir = run_ir_drop(design, load_config(), clock_period_ns=10.0)
    sta = {
        "setup_wns_ps": 1.0,
        "setup_tns_ps": 0.0,
        "hold_wns_ps": 1.0,
        "hold_tns_ps": 0.0,
        "wns_ps": 1.0,
        "tns_ps": 0.0,
        "corner": "ff",
        "endpoints": [],
        "summary": {"setup_failing": 0, "hold_failing": 0},
    }
    drc = {"violation_count": 0, "violations": [], "counts_by_type": {}}
    report = build_qor_report(
        "irf", drc, sta, ir, load_config(), meta={"die_area": list(design.die_area)}
    )
    chk = report["checks"]["ir_drop"]
    assert "source_type" in chk
    assert "corner" in chk
    assert "min_voltage_raw" in chk
    assert "max_j_ma_per_um" in chk
    det = report["ir_details"]
    assert "instance_heatmap" in det
    assert "j_histogram" in det
    assert any("follow-pin" in n or "source_type" in n for n in report["fidelity_notes"])
    html_path = write_html_report([report], tmp_path / "ir.html")
    html = html_path.read_text(encoding="utf-8")
    assert "renderIrHeatmap" in html
    assert "min_voltage_raw" in html
    assert "ir-heatmap" in html
