"""CLI: python -m pnr_tool <command>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pnr_tool.algorithms.loader import PluginLoadError
from pnr_tool.config import load_config, project_root
from pnr_tool.pdk.fetch import fetch_pdk, pdk_ready
from pnr_tool.pipeline.batch import build_scoreboard_from_runs, run_batch, write_scoreboard
from pnr_tool.pipeline.run import StageError, run_pipeline
from pnr_tool.report.html_report import find_qor_files, generate_from_runs, load_reports, write_html_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pnr_tool", description="PnR Algorithm Benchmarking Framework")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch-pdk", help="Download SkyWater HD + OpenLane tech assets")
    p_fetch.add_argument("--force", action="store_true", help="Re-download even if cached")
    p_fetch.add_argument("--cache", type=Path, default=None, help="Override cache directory")
    p_fetch.add_argument(
        "--netlist",
        type=Path,
        action="append",
        default=[],
        help="Also fetch LEF/lib for sky130 cells used in this netlist (repeatable)",
    )

    p_run = sub.add_parser("run", help="Run Placement → Clock Opt → Routing → checkers")
    p_run.add_argument("--netlist", type=Path, required=True, help="Structural Verilog netlist")
    p_run.add_argument("--top", type=str, default=None, help="Top module name")
    p_run.add_argument("--config", type=Path, default=None, help="YAML config override")
    p_run.add_argument("--out", type=Path, default=None, help="Output directory")
    p_run.add_argument("--clock-period-ns", type=float, default=10.0)
    p_run.add_argument("--resume-from", type=Path, default=None, help="Checkpoint .pkl to resume")
    p_run.add_argument("--no-fetch", action="store_true", help="Do not auto-fetch PDK")
    p_run.add_argument(
        "--no-layout-images",
        action="store_true",
        help="Skip per-stage layout PNG rendering (enabled by default)",
    )
    p_run.add_argument(
        "--placement",
        type=str,
        default="default",
        help="Placement plugin: alias (default|force_directed|random) or module.path:ClassName",
    )
    p_run.add_argument(
        "--clock-opt",
        type=str,
        default="default",
        help="Clock-opt plugin: alias (default|htree) or module.path:ClassName",
    )
    p_run.add_argument(
        "--routing",
        type=str,
        default="default",
        help="Routing plugin: alias (default|global) or module.path:ClassName",
    )

    p_batch = sub.add_parser("batch", help="Run design × algorithm matrix from a YAML manifest")
    p_batch.add_argument("--manifest", type=Path, required=True, help="experiments.yaml")
    p_batch.add_argument("--out", type=Path, required=True, help="Batch output directory")
    p_batch.add_argument("--no-fetch", action="store_true")
    p_batch.add_argument("--no-layout-images", action="store_true")

    p_score = sub.add_parser("scoreboard", help="Rebuild scoreboard.csv from existing *.qor.json")
    p_score.add_argument("--runs", type=Path, required=True, help="Directory to scan")
    p_score.add_argument("--out", type=Path, default=None, help="Output CSV (default: <runs>/scoreboard.csv)")

    p_html = sub.add_parser("html-report", help="Build an HTML dashboard from QoR JSON runs")
    p_html.add_argument(
        "--runs",
        type=Path,
        default=None,
        help="Directory to scan for *.qor.json (default: ./runs)",
    )
    p_html.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output HTML path (default: <runs>/index.html)",
    )
    p_html.add_argument("--title", type=str, default="PnR QoR Reports")

    args = parser.parse_args(argv)

    if args.cmd == "fetch-pdk":
        cfg = load_config()
        cache = Path(args.cache) if args.cache else Path(cfg["pdk"]["cache_dir"])
        fetch_pdk(cache, force=args.force, extra_netlists=args.netlist or None)
        print("Ready:" if pdk_ready(cache) else "Incomplete:", cache)
        return 0 if pdk_ready(cache) else 1

    if args.cmd == "run":
        try:
            result = run_pipeline(
                netlist=args.netlist,
                top=args.top,
                config_path=args.config,
                out_dir=args.out,
                clock_period_ns=args.clock_period_ns,
                resume_from=args.resume_from,
                fetch_if_missing=not args.no_fetch,
                layout_images=not args.no_layout_images,
                placement_algo=args.placement,
                clock_algo=args.clock_opt,
                routing_algo=args.routing,
            )
        except PluginLoadError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except StageError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        ir = (result["report"].get("checks") or {}).get("ir_drop") or {}
        print(
            "IR instances affected:",
            ir.get("instances_affected", ir.get("violations", "—")),
        )
        print("Algorithms:", result.get("algorithms"))
        # Refresh HTML dashboard if writing into the default runs tree
        runs_root = project_root() / "runs"
        try:
            html_path = generate_from_runs(runs_root)
            print(f"HTML report: {html_path}")
        except Exception:
            pass
        return 0

    if args.cmd == "batch":
        try:
            summary = run_batch(
                args.manifest,
                args.out,
                fetch_if_missing=not args.no_fetch,
                layout_images=not args.no_layout_images,
            )
        except (PluginLoadError, ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Scoreboard: {summary['scoreboard_csv']} ({len(summary['rows'])} rows)")
        if summary["errors"]:
            print(f"Errors: {len(summary['errors'])}", file=sys.stderr)
            return 2
        if summary.get("html_path"):
            print(f"HTML report: {summary['html_path']}")
        return 0

    if args.cmd == "scoreboard":
        runs_dir = Path(args.runs)
        out = Path(args.out) if args.out else runs_dir / "scoreboard.csv"
        rows = build_scoreboard_from_runs(runs_dir)
        if not rows:
            print(f"No *.qor.json found under {runs_dir}", file=sys.stderr)
            write_scoreboard([], out)
            return 1
        path = write_scoreboard(rows, out)
        print(f"Wrote {len(rows)} row(s) -> {path}")
        return 0

    if args.cmd == "html-report":
        runs_dir = Path(args.runs) if args.runs else project_root() / "runs"
        out = Path(args.out) if args.out else runs_dir / "index.html"
        paths = find_qor_files(runs_dir)
        if not paths:
            print(f"No *.qor.json found under {runs_dir}", file=sys.stderr)
            # Still write an empty viewer so the user can open files manually
            write_html_report([], out, title=args.title)
            print(f"Wrote empty viewer: {out}")
            return 1
        reports = load_reports(paths, html_dir=out.parent)
        path = write_html_report(reports, out, title=args.title)
        print(f"Embedded {len(reports)} report(s) -> {path}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
