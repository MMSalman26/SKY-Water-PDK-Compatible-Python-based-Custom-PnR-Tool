"""Batch experiment runner and scoreboard CSV/JSON builders."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import yaml

from pnr_tool.pipeline.run import StageError, run_pipeline
from pnr_tool.report.html_report import generate_from_runs

SCOREBOARD_COLUMNS = [
    "design",
    "algo_name",
    "placement",
    "clock_opt",
    "routing",
    "drc_violations",
    "wns_ps",
    "hold_wns_ps",
    "ir_instances_affected",
    "max_ir_drop",
    "hpwl_um",
    "routed_wl_um",
    "via_count",
    "cts_buffer_count",
    "routing_fallback_count",
    "num_cells",
    "num_nets",
    "die_area_um2",
    "t_placement_s",
    "t_clock_opt_s",
    "t_routing_s",
    "t_drc_s",
    "t_sta_s",
    "t_ir_drop_s",
    "t_total_s",
    "memory_mb",
    "qor_path",
]


def load_manifest(path: Path) -> Dict[str, Any]:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a mapping: {path}")
    designs = data.get("designs") or []
    algorithms = data.get("algorithms") or []
    if not designs:
        raise ValueError("Manifest needs at least one entry under 'designs'")
    if not algorithms:
        raise ValueError("Manifest needs at least one entry under 'algorithms'")
    return data


def _safe_name(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep) or "run"


def _design_stem(entry: Mapping[str, Any]) -> str:
    top = entry.get("top")
    if top:
        return _safe_name(str(top))
    return _safe_name(Path(entry["netlist"]).stem)


def qor_to_scoreboard_row(
    report: Mapping[str, Any],
    *,
    algo_name: str = "",
    qor_path: str = "",
) -> Dict[str, Any]:
    """Flatten a QoR report (schema 1 or 2) into one scoreboard row."""
    checks = report.get("checks") or {}
    drc = checks.get("drc") or {}
    sta = checks.get("sta") or {}
    ir = checks.get("ir_drop") or {}
    metrics = report.get("metrics") or {}
    meta = report.get("meta") or {}
    algos = report.get("algorithms") or {}
    timing = report.get("timing_s") or {}

    # Schema-1 fallbacks from meta
    num_cells = metrics.get("num_cells", meta.get("num_cells"))
    num_nets = metrics.get("num_nets", meta.get("num_nets"))
    die_area = meta.get("die_area")
    die_area_um2 = metrics.get("die_area_um2")
    if die_area_um2 is None and isinstance(die_area, (list, tuple)) and len(die_area) >= 4:
        die_area_um2 = max(0.0, float(die_area[2]) - float(die_area[0])) * max(
            0.0, float(die_area[3]) - float(die_area[1])
        )
    fallbacks = meta.get("routing_fallbacks", [])
    fallback_count = metrics.get("routing_fallback_count")
    if fallback_count is None:
        fallback_count = len(fallbacks) if isinstance(fallbacks, (list, tuple)) else 0

    return {
        "design": report.get("design", ""),
        "algo_name": algo_name or "",
        "placement": algos.get("placement", "default"),
        "clock_opt": algos.get("clock_opt", "default"),
        "routing": algos.get("routing", "default"),
        "drc_violations": drc.get("violations"),
        "wns_ps": sta.get("setup_wns_ps", sta.get("wns_ps")),
        "hold_wns_ps": sta.get("hold_wns_ps"),
        "ir_instances_affected": ir.get("instances_affected", ir.get("violations")),
        "max_ir_drop": ir.get("max_ir_drop"),
        "hpwl_um": metrics.get("hpwl_um"),
        "routed_wl_um": metrics.get("routed_wl_um"),
        "via_count": metrics.get("via_count"),
        "cts_buffer_count": metrics.get("cts_buffer_count"),
        "routing_fallback_count": fallback_count,
        "num_cells": num_cells,
        "num_nets": num_nets,
        "die_area_um2": die_area_um2,
        "t_placement_s": timing.get("placement"),
        "t_clock_opt_s": timing.get("clock_opt"),
        "t_routing_s": timing.get("routing"),
        "t_drc_s": timing.get("drc"),
        "t_sta_s": timing.get("sta"),
        "t_ir_drop_s": timing.get("ir_drop"),
        "t_total_s": timing.get("total"),
        "memory_mb": report.get("memory_mb"),
        "qor_path": qor_path,
    }


def write_scoreboard(
    rows: Sequence[Mapping[str, Any]],
    out_csv: Path,
    out_json: Optional[Path] = None,
) -> Path:
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SCOREBOARD_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in SCOREBOARD_COLUMNS})

    if out_json is None:
        out_json = out_csv.with_suffix(".json")
    out_json = Path(out_json)
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "columns": SCOREBOARD_COLUMNS,
        "rows": [dict(r) for r in rows],
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_csv


def build_scoreboard_from_runs(runs_dir: Path) -> List[Dict[str, Any]]:
    runs_dir = Path(runs_dir)
    rows: List[Dict[str, Any]] = []
    for path in sorted(runs_dir.rglob("*.qor.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Infer algo name from parent folder when under batch/<algo>/<design>/
        algo_name = ""
        try:
            rel = path.relative_to(runs_dir)
            if len(rel.parts) >= 3:
                algo_name = rel.parts[0]
        except ValueError:
            pass
        rows.append(qor_to_scoreboard_row(report, algo_name=algo_name, qor_path=str(path.as_posix())))
    return rows


def run_batch(
    manifest_path: Path,
    out_dir: Path,
    *,
    fetch_if_missing: bool = True,
    layout_images: bool = True,
    refresh_html: bool = True,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    designs: Sequence[Mapping[str, Any]] = manifest["designs"]
    algorithms: Sequence[Mapping[str, Any]] = manifest["algorithms"]
    rows: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for algo in algorithms:
        algo_name = str(algo.get("name") or "unnamed")
        placement = algo.get("placement", "default")
        clock_opt = algo.get("clock_opt", "default")
        routing = algo.get("routing", "default")

        for design_entry in designs:
            netlist = Path(design_entry["netlist"])
            if not netlist.is_absolute():
                # Resolve relative to CWD (caller / project root)
                netlist = Path(netlist)
            design_key = _design_stem(design_entry)
            run_out = out_dir / _safe_name(algo_name) / design_key
            try:
                result = run_pipeline(
                    netlist=netlist,
                    top=design_entry.get("top"),
                    out_dir=run_out,
                    clock_period_ns=float(design_entry.get("clock_period_ns", 10.0)),
                    fetch_if_missing=fetch_if_missing,
                    layout_images=layout_images,
                    placement_algo=placement,
                    clock_algo=clock_opt,
                    routing_algo=routing,
                )
                report = result["report"]
                row = qor_to_scoreboard_row(
                    report,
                    algo_name=algo_name,
                    qor_path=str(Path(result["qor_path"]).as_posix()),
                )
                rows.append(row)
                results.append({"algo": algo_name, "design": design_key, "qor_path": result["qor_path"]})
            except (StageError, FileNotFoundError, OSError, ValueError) as exc:
                errors.append(
                    {
                        "algo": algo_name,
                        "design": design_key,
                        "error": str(exc),
                    }
                )
                print(f"BATCH ERROR [{algo_name}/{design_key}]: {exc}")

    csv_path = write_scoreboard(rows, out_dir / "scoreboard.csv", out_dir / "scoreboard.json")
    manifest_copy = out_dir / "manifest.yaml"
    if Path(manifest_path).resolve() != manifest_copy.resolve():
        manifest_copy.write_text(Path(manifest_path).read_text(encoding="utf-8"), encoding="utf-8")

    html_path = None
    if refresh_html:
        try:
            html_path = generate_from_runs(out_dir, title=f"Batch {out_dir.name}")
        except Exception as exc:
            print(f"HTML dashboard refresh failed: {exc}")

    return {
        "out_dir": out_dir,
        "scoreboard_csv": csv_path,
        "scoreboard_json": out_dir / "scoreboard.json",
        "rows": rows,
        "results": results,
        "errors": errors,
        "html_path": html_path,
    }
