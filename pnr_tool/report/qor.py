"""QoR JSON report with checker stats (schema version 2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


QOR_SCHEMA_VERSION = 2


def build_qor_report(
    design_name: str,
    drc: Dict[str, Any],
    sta: Dict[str, Any],
    ir: Dict[str, Any],
    config: Dict[str, Any],
    meta: Dict[str, Any] | None = None,
    *,
    algorithms: Mapping[str, str] | None = None,
    timing_s: Mapping[str, float] | None = None,
    memory_mb: Optional[float] = None,
    metrics: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    thr = config.get("thresholds", {})
    wns_lim = float(thr.get("sta_wns_ps", 0.0))
    hold_lim = float(thr.get("sta_hold_wns_ps", wns_lim))
    ir_ratio = float(thr.get("ir_min_vdd_ratio", 0.95))
    drc_lim = int(thr.get("drc_max_violations", 0))
    sta_cfg = config.get("sta", {})

    setup_wns = float(sta.get("setup_wns_ps", sta.get("wns_ps", 0.0)))
    setup_tns = float(sta.get("setup_tns_ps", sta.get("tns_ps", 0.0)) or 0.0)
    hold_wns = float(sta.get("hold_wns_ps", 0.0))
    hold_tns = float(sta.get("hold_tns_ps", 0.0) or 0.0)
    drc_v = int(drc.get("violation_count", 0))
    ir_v = int(ir.get("instances_affected", ir.get("violation_count", 0)))
    vdd = float(ir.get("vdd", 1.8))
    min_v = float(ir.get("min_voltage", vdd))
    max_ir = float(ir.get("max_ir_drop", ir.get("max_drop", vdd - min_v)) or 0.0)
    max_gnd = float(ir.get("max_ground_bounce", 0.0) or 0.0)
    collapse = float(ir.get("max_supply_collapse", max_ir + max_gnd) or 0.0)
    floating = int(ir.get("floating_nodes", 0) or 0)
    instance_drops = list(ir.get("instance_drops") or ir.get("violations") or [])

    cts = sta.get("cts_latency") or {"min_ns": 0.0, "max_ns": 0.0, "mean_ns": 0.0, "skew_ns": 0.0}
    use_cts = bool(sta_cfg.get("use_cts_latency", True))
    wire_model = sta.get("wire_model", sta_cfg.get("wire_model", "tree_elmore"))

    endpoints = list(sta.get("endpoints") or [])
    summary = sta.get("summary") or {
        "setup_endpoints": sum(1 for e in endpoints if e.get("check") == "setup"),
        "hold_endpoints": sum(1 for e in endpoints if e.get("check") == "hold"),
        "setup_failing": sum(
            1 for e in endpoints if e.get("check") == "setup" and float(e.get("slack_ns", 0)) < 0
        ),
        "hold_failing": sum(
            1 for e in endpoints if e.get("check") == "hold" and float(e.get("slack_ns", 0)) < 0
        ),
    }
    setup_violations = int(summary.get("setup_failing", 0) or 0)
    hold_violations = int(summary.get("hold_failing", 0) or 0)

    checks = {
        "drc": {
            "violations": drc_v,
            "limit": drc_lim,
            "spatial_backend": drc.get("spatial_backend"),
            "counts_by_type": drc.get("counts_by_type", {}),
            "pass_on": drc.get("pass_on", ["overlap", "short", "open", "spacing"]),
            "violation_count_all": drc.get("violation_count_all", drc_v),
            "geometry": drc.get("geometry") or {},
            "note": (
                "Width-aware metal/via DRC + CRPR-style connectivity; "
                "gcell shorts are router congestion, not GDS signoff"
            ),
        },
        "sta": {
            "setup_violations": setup_violations,
            "hold_violations": hold_violations,
            "wns_ps": setup_wns,
            "tns_ps": setup_tns,
            "setup_wns_ps": setup_wns,
            "setup_tns_ps": setup_tns,
            "hold_wns_ps": hold_wns,
            "hold_tns_ps": hold_tns,
            "limit_wns_ps": wns_lim,
            "limit_hold_wns_ps": hold_lim,
            "corner": sta.get("corner", "ff"),
            "corners_loaded": sta.get("corners_loaded") or [],
            "use_crpr": sta.get("use_crpr", True),
            "use_ceff": sta.get("use_ceff", True),
            "rc_min_scale": sta.get("rc_min_scale"),
            "rc_max_scale": sta.get("rc_max_scale"),
            "loops_broken": sta.get("loops_broken", 0),
            "clock_period_ns": sta.get("clock_period_ns"),
            "uncertainty_ns": sta.get("uncertainty_ns", sta_cfg.get("uncertainty_ns")),
            "setup_ns": sta.get("setup_ns", sta_cfg.get("setup_ns")),
            "hold_ns": sta.get("hold_ns", sta_cfg.get("hold_ns")),
            "cts_latency": cts,
            "wire_model": wire_model,
            "note": (
                "Dual-corner NLDM (setup ss / hold ff) + CRPR + min/max Elmore; "
                "QoR ranker, not signoff"
            ),
        },
        "ir_drop": {
            "violations": ir_v,
            "instances_affected": ir_v,
            "min_voltage": min_v,
            "min_voltage_raw": ir.get("min_voltage_raw", min_v),
            "solver_residual": ir.get("solver_residual"),
            "vdd": vdd,
            "threshold": ir_ratio * vdd,
            "max_ir_drop": max_ir,
            "max_ground_bounce": max_gnd,
            "max_supply_collapse": collapse,
            "total_current_a": ir.get("total_current_a"),
            "floating_nodes": floating,
            "ir_mode": ir.get("ir_mode", "synthetic_fallback"),
            "via_edges": ir.get("via_edges", 0),
            "solve_method": ir.get("solve_method"),
            "source_type": ir.get("source_type", "straps"),
            "corner": ir.get("corner", "tt"),
            "coupled": bool(ir.get("coupled", False)),
            "max_j_ma_per_um": ir.get("max_j_ma_per_um", 0.0),
            "j_violations": ir.get("j_violations", 0),
            "note": ir.get(
                "note",
                "Static DC MNA; violations = instances below VDD threshold; not signoff",
            ),
        },
    }

    algo = {
        "placement": (algorithms or {}).get("placement", "default"),
        "clock_opt": (algorithms or {}).get("clock_opt", "default"),
        "routing": (algorithms or {}).get("routing", "default"),
    }
    timing = {
        "placement": float((timing_s or {}).get("placement", 0.0)),
        "power_plan": float((timing_s or {}).get("power_plan", 0.0)),
        "tap": float((timing_s or {}).get("tap", 0.0)),
        "decap": float((timing_s or {}).get("decap", 0.0)),
        "clock_opt": float((timing_s or {}).get("clock_opt", 0.0)),
        "routing": float((timing_s or {}).get("routing", 0.0)),
        "drc": float((timing_s or {}).get("drc", 0.0)),
        "sta": float((timing_s or {}).get("sta", 0.0)),
        "ir_drop": float((timing_s or {}).get("ir_drop", 0.0)),
        "total": float((timing_s or {}).get("total", 0.0)),
    }

    return {
        "qor_schema": QOR_SCHEMA_VERSION,
        "design": design_name,
        "algorithms": algo,
        "timing_s": timing,
        "memory_mb": memory_mb,
        "metrics": dict(metrics or {}),
        "checks": checks,
        "drc_details": {
            "counts_by_type": drc.get("counts_by_type", {}),
            "sample_violations": drc.get("violations", [])[:20],
            "geometry": drc.get("geometry") or {},
        },
        "sta_details": {
            "endpoints": endpoints[:50],
            "summary": summary,
            "cts_latency": cts,
            "wire_model": wire_model,
            "critical_path": sta.get("critical_path") or {},
            "corners_loaded": sta.get("corners_loaded") or [],
            "sdc": sta.get("sdc") or {},
        },
        "ir_details": {
            "max_drop": ir.get("max_drop", max_ir),
            "max_ir_drop": max_ir,
            "max_ground_bounce": max_gnd,
            "max_supply_collapse": collapse,
            "instances_affected": ir_v,
            "instance_drops": instance_drops[:200],
            "instance_heatmap": list(ir.get("instance_heatmap") or [])[:500],
            "worst_node_by_layer": ir.get("worst_node_by_layer") or {},
            "grid": ir.get("grid"),
            "vdd_rail": ir.get("vdd_rail"),
            "vss_rail": ir.get("vss_rail"),
            "via_edges": ir.get("via_edges", 0),
            "solve_method": ir.get("solve_method"),
            "currents": ir.get("currents")
            or {
                "total_a": ir.get("total_current_a"),
                "avg_cell_a": ir.get("avg_cell_current_a"),
            },
            "floating_nodes": ir.get("floating_sample") or floating,
            "floating_count": floating,
            "sample_violations": instance_drops[:40],
            "error": ir.get("error"),
            "ir_mode": ir.get("ir_mode"),
            "source_type": ir.get("source_type", "straps"),
            "corner": ir.get("corner", "tt"),
            "coupled": bool(ir.get("coupled", False)),
            "min_voltage_raw": ir.get("min_voltage_raw", min_v),
            "solver_residual": ir.get("solver_residual"),
            "max_j_ma_per_um": ir.get("max_j_ma_per_um", 0.0),
            "j_violations": ir.get("j_violations", 0),
            "j_histogram": ir.get("j_histogram") or [],
            "j_max_limit": ir.get("j_max_limit"),
        },
        "meta": meta or {},
        "fidelity_notes": [
            "DRC: width-aware shorts/spacing, vias, T-junction opens; gcell shorts separate; not Magic/KLayout signoff",
            f"STA: dual-corner NLDM (ss setup / ff hold) + {wire_model}; CRPR "
            f"{'on' if sta.get('use_crpr', True) else 'off'}",
            f"STA: per-sink CTS {'on' if use_cts else 'off'}; Ceff "
            f"{'on' if sta.get('use_ceff', True) else 'off'}; not signoff",
            "IR: static DC MNA (PDNSim-inspired), not Voltus signoff; follow-pin taps; "
            f"source_type={ir.get('source_type', 'straps')}; no vector/dynamic IR",
            "IR: violations = instances with local VDD below threshold; drop % = 100·(VDD−V)/VDD",
            "IR: currents from liberty leakage + α·C·V²·f (physical cells: leakage only)",
        ],
    }


def write_qor_report(report: Dict[str, Any], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
