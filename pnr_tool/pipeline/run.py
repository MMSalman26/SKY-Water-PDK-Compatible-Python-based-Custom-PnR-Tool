"""End-to-end pipeline: elaborate → power → place → tap → decap → clock → route → checkers → QoR."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pnr_tool.algorithms.base import ClockOptAlgorithm, PlacementAlgorithm, RoutingAlgorithm
from pnr_tool.algorithms.floorplan import assign_port_positions, estimate_die_area
from pnr_tool.algorithms.loader import load_clock_opt, load_placement, load_routing
from pnr_tool.algorithms.power_plan import plan_power
from pnr_tool.algorithms.tap_decap import insert_decaps, insert_taps
from pnr_tool.checkers.drc import run_drc
from pnr_tool.checkers.ir_drop import run_ir_drop, write_ir_spice
from pnr_tool.checkers.sta import run_sta
from pnr_tool.config import load_config, project_root
from pnr_tool.design.contracts import (
    ContractError,
    validate_clock_tree,
    validate_instances,
    validate_routing,
)
from pnr_tool.design.graph import build_timing_graph, infer_drivers, sanity_check
from pnr_tool.design.object import DesignObject
from pnr_tool.io.verilog_parser import elaborate, parse_verilog_file
from pnr_tool.pdk.fetch import fetch_pdk, pdk_ready
from pnr_tool.pdk.loader import ensure_cells, load_library_and_tech
from pnr_tool.pipeline.memory import MemoryTracker
from pnr_tool.report.layout_data import write_layout_view
from pnr_tool.report.layout_plot import write_stage_layout
from pnr_tool.report.metrics import build_scorecard_metrics
from pnr_tool.report.layout_cif import write_cif
from pnr_tool.report.qor import build_qor_report, write_qor_report
from pnr_tool.report.spef import write_spef

AlgoIn = Union[str, PlacementAlgorithm, ClockOptAlgorithm, RoutingAlgorithm, None]


class StageError(RuntimeError):
    pass


def run_pipeline(
    netlist: Path,
    top: Optional[str] = None,
    config_path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    clock_period_ns: float = 10.0,
    resume_from: Optional[Path] = None,
    fetch_if_missing: bool = True,
    layout_images: bool = True,
    placement_algo: AlgoIn = None,
    clock_algo: AlgoIn = None,
    routing_algo: AlgoIn = None,
) -> Dict[str, Any]:
    config = load_config(config_path)
    if not layout_images:
        config.setdefault("report", {})["layout_images"] = False
    cache = Path(config["pdk"]["cache_dir"])
    out_dir = Path(out_dir or project_root() / "runs" / Path(netlist).stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_cfg = config.get("checkpoint", {}) or {}
    ckpt_raw = ckpt_cfg.get("dir", "checkpoints")
    ckpt_dir = Path(ckpt_raw) if ckpt_raw is not None else Path("checkpoints")
    if not ckpt_dir.is_absolute():
        ckpt_dir = out_dir / ckpt_dir
    ckpt_enabled = bool(ckpt_cfg.get("enabled", True))

    placer, placement_id = load_placement(placement_algo)
    cts, clock_id = load_clock_opt(clock_algo)
    router, routing_id = load_routing(routing_algo)
    algo_ids = {"placement": placement_id, "clock_opt": clock_id, "routing": routing_id}

    if fetch_if_missing:
        if not pdk_ready(cache):
            print("PDK cache missing/incomplete — fetching...")
        fetch_pdk(cache, extra_netlists=[netlist])

    library, tech = load_library_and_tech(cache)
    lib_names = set(library.get("cells", {}))

    mem = MemoryTracker()
    t_run0 = time.perf_counter()
    timing_s: Dict[str, float] = {
        "placement": 0.0,
        "power_plan": 0.0,
        "tap": 0.0,
        "decap": 0.0,
        "clock_opt": 0.0,
        "routing": 0.0,
        "drc": 0.0,
        "sta": 0.0,
        "ir_drop": 0.0,
        "total": 0.0,
    }

    if resume_from:
        design = DesignObject.load_checkpoint(resume_from)
        design.library = library
        design.tech = tech
        stage = design.meta.get("completed_stage", "elaborated")
        print(f"Resumed from {resume_from} (stage={stage})")
    else:
        modules = parse_verilog_file(netlist)
        design = elaborate(
            modules,
            top=top,
            library_cell_names=lib_names,
            strip_physical=True,
            strip_power_pins=True,
        )
        used = {info["cell_type"] for info in design.cells.values()}
        ensure_cells(library, used, tech)
        design.library = library
        design.tech = tech
        infer_drivers(design)
        warnings = sanity_check(design)
        for w in warnings:
            print(f"SANITY WARN: {w}")
        build_timing_graph(design)
        design.meta["completed_stage"] = "elaborated"
        if ckpt_enabled:
            design.checkpoint(ckpt_dir, "elaborated")
        stage = "elaborated"

    # Floorplan die + Power planning (OpenLane: init_fp → ioplacer → pdngen)
    if stage in ("elaborated",):
        try:
            die_before = design.die_area
            estimate_die_area(design, config)
            assign_port_positions(design)
            t0 = time.perf_counter()
            grid = plan_power(design, config)
            timing_s["power_plan"] = time.perf_counter() - t0
            mem.sample()
            design.meta["completed_stage"] = "power_plan"
            design.meta["die_before_place"] = list(design.die_area)
            if ckpt_enabled:
                design.checkpoint(ckpt_dir, "power_plan")
            write_stage_layout(design, "power", out_dir, config)
            write_layout_view(design, "power", out_dir, config)
            stage = "power_plan"
            nseg = len(grid.get("segments") or [])
            print(f"PowerPlan: {nseg} VDD/VSS segments, die={design.die_area} (was {die_before})")
        except Exception as exc:
            raise StageError(f"Power planning failed: {exc}") from exc

    # Placement (RePlAce + OpenDP inspired)
    if stage in ("power_plan",):
        # Legacy checkpoint: power_plan with instances already present → skip place
        if design.instances:
            print("Placement: skipped (instances already present in checkpoint)")
            stage = "placement"
            design.meta["completed_stage"] = "placement"
        else:
            try:
                die_planned = tuple(float(v) for v in design.die_area)
                t0 = time.perf_counter()
                instances = placer.execute(design, config)
                timing_s["placement"] = time.perf_counter() - t0
                mem.sample()
                validate_instances(instances, design.die_area)
                design.instances = instances
                assign_port_positions(design)
                # Rebuild PDN if legalizer expanded the die
                new_die = tuple(float(v) for v in design.die_area)
                if new_die != die_planned:
                    plan_power(design, config)
                    print(f"PowerPlan rebuilt after die expansion: {die_planned} → {new_die}")
                design.meta["completed_stage"] = "placement"
                if ckpt_enabled:
                    design.checkpoint(ckpt_dir, "placement")
                write_stage_layout(design, "placement", out_dir, config)
                write_layout_view(design, "placement", out_dir, config)
                stage = "placement"
                print(f"Placement ({placement_id}): {len(instances)} instances, die={design.die_area}")
            except ContractError as exc:
                raise StageError(f"Placement contract failed: {exc}") from exc
            except Exception as exc:
                raise StageError(f"Placement algorithm '{placement_id}' failed: {exc}") from exc

    # Legacy resume: old runs stopped at placement before power existed in new order
    if stage == "placement" and not design.power_grid:
        plan_power(design, config)

    # Tap insertion (no fillers)
    if stage in ("placement",):
        try:
            t0 = time.perf_counter()
            tap_summary = insert_taps(design, config)
            timing_s["tap"] = time.perf_counter() - t0
            mem.sample()
            validate_instances(design.instances, design.die_area)
            design.meta["completed_stage"] = "tap"
            if ckpt_enabled:
                design.checkpoint(ckpt_dir, "tap")
            stage = "tap"
            print(f"Tap: taps={tap_summary.get('taps', 0)}")
        except ContractError as exc:
            raise StageError(f"Tap contract failed: {exc}") from exc
        except Exception as exc:
            raise StageError(f"Tap insertion failed: {exc}") from exc

    # Legacy combined tap_decap checkpoint → already past both physical stages
    if stage == "tap_decap":
        stage = "decap"

    # Decap insertion (no fillers) — after taps so gaps account for tap occupancy
    if stage in ("tap",):
        try:
            t0 = time.perf_counter()
            decap_summary = insert_decaps(design, config)
            timing_s["decap"] = time.perf_counter() - t0
            mem.sample()
            validate_instances(design.instances, design.die_area)
            design.meta["completed_stage"] = "decap"
            if ckpt_enabled:
                design.checkpoint(ckpt_dir, "decap")
            stage = "decap"
            print(f"Decap: decaps={decap_summary.get('decaps', 0)} (no fillers)")
        except ContractError as exc:
            raise StageError(f"Decap contract failed: {exc}") from exc
        except Exception as exc:
            raise StageError(f"Decap insertion failed: {exc}") from exc

    # Clock opt (TritonCTS inspired) — after tap/decap so legalizer sees physical occupancy
    if stage in ("decap",):
        try:
            t0 = time.perf_counter()
            clock_tree = cts.execute(design, config)
            timing_s["clock_opt"] = time.perf_counter() - t0
            mem.sample()
            validate_clock_tree(clock_tree)
            design.clock_tree = clock_tree
            infer_drivers(design)
            validate_instances(design.instances, design.die_area)
            assign_port_positions(design)
            design.meta["completed_stage"] = "clock_opt"
            if ckpt_enabled:
                design.checkpoint(ckpt_dir, "clock_opt")
            write_stage_layout(design, "cts", out_dir, config)
            write_layout_view(design, "cts", out_dir, config)
            stage = "clock_opt"
            print(f"ClockOpt ({clock_id}): {len(clock_tree.get('new_buffers', {}))} buffers")
        except ContractError as exc:
            raise StageError(f"ClockOpt contract failed: {exc}") from exc
        except Exception as exc:
            raise StageError(f"ClockOpt algorithm '{clock_id}' failed: {exc}") from exc

    # Routing (FastRoute inspired)
    if stage in ("clock_opt",):
        try:
            t0 = time.perf_counter()
            routing = router.execute(design, config)
            timing_s["routing"] = time.perf_counter() - t0
            mem.sample()
            validate_routing(routing)
            design.routing = routing
            design.meta["completed_stage"] = "routing"
            if ckpt_enabled:
                design.checkpoint(ckpt_dir, "routing")
            write_stage_layout(design, "routing", out_dir, config)
            write_layout_view(design, "routing", out_dir, config)
            stage = "routing"
            print(f"Routing ({routing_id}): {len(routing)} nets")
        except ContractError as exc:
            raise StageError(f"Routing contract failed: {exc}") from exc
        except Exception as exc:
            raise StageError(f"Routing algorithm '{routing_id}' failed: {exc}") from exc

    print("Running DRC / STA / IR Drop ...")
    t0 = time.perf_counter()
    drc = run_drc(design, config)
    timing_s["drc"] = time.perf_counter() - t0
    mem.sample()
    if bool((config.get("drc") or {}).get("write_cif", True)):
        write_cif(design, out_dir / f"{design.name}.cif", config)

    t0 = time.perf_counter()
    sta = run_sta(design, config, clock_period_ns=clock_period_ns)
    timing_s["sta"] = time.perf_counter() - t0
    mem.sample()
    if bool((config.get("sta") or {}).get("write_spef", True)):
        write_spef(design, out_dir / f"{design.name}.spef")

    t0 = time.perf_counter()
    ir = run_ir_drop(design, config, clock_period_ns=clock_period_ns)
    timing_s["ir_drop"] = time.perf_counter() - t0
    mem.sample()
    if bool((config.get("ir_drop") or {}).get("write_spice", True)):
        write_ir_spice(ir, out_dir / f"{design.name}_ir.sp")

    timing_s["total"] = time.perf_counter() - t_run0
    metrics = build_scorecard_metrics(design)

    report = build_qor_report(
        design.name,
        drc,
        sta,
        ir,
        config,
        meta={
            "num_cells": len(design.cells),
            "num_nets": len(design.nets),
            "die_area": design.die_area,
            "routing_fallbacks": design.meta.get("routing_fallbacks", []),
        },
        algorithms=algo_ids,
        timing_s=timing_s,
        memory_mb=mem.peak_delta_mb(),
        metrics=metrics,
    )
    qor_path = write_qor_report(report, out_dir / f"{design.name}.qor.json")
    ir_n = ((report.get("checks") or {}).get("ir_drop") or {}).get("instances_affected", 0)
    print(f"QoR written to {qor_path} (ir_instances_affected={ir_n})")
    return {"design": design, "report": report, "qor_path": qor_path, "algorithms": algo_ids}
