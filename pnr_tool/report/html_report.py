"""Build a self-contained HTML dashboard from one or more QoR JSON reports."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pnr_tool.report.layout_data import LAYOUT_VIEW_FILE, STAGE_VIEW_FILES
from pnr_tool.report.layout_plot import STAGE_FILES


def find_qor_files(root: Path) -> List[Path]:
    root = Path(root)
    if root.is_file() and root.name.endswith(".qor.json"):
        return [root]
    return sorted(root.rglob("*.qor.json"))


# Per-stage layout JSON size budget for embedding (file:// cannot fetch siblings).
_MAX_EMBED_LAYOUT_BYTES = 5_000_000


def load_reports(
    paths: Sequence[Path], html_dir: Optional[Path] = None
) -> List[dict[str, Any]]:
    reports: List[dict[str, Any]] = []
    for path in paths:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_source"] = str(path.as_posix())
        data["_source_name"] = path.name
        if html_dir is not None:
            html_dir = Path(html_dir)
            data["_layout_images"] = _layout_links(path.parent, html_dir)
            data["_layout_view"] = _rel_if_exists(path.parent / LAYOUT_VIEW_FILE, html_dir)
            data["_layout_views"] = _layout_view_links(path.parent, html_dir)
            data["_layout_embedded"] = _embed_layout_views(path.parent)
        reports.append(data)
    return reports


def _embed_layout_views(run_dir: Path) -> Dict[str, Any]:
    """Inline stage layout JSON so the viewer works without HTTP fetch."""
    embedded: Dict[str, Any] = {}
    total = 0
    # Prefer routing first so the default stage always works when space is tight.
    order = ["routing", "power", "cts", "placement"]
    for stage in order:
        filename = STAGE_VIEW_FILES.get(stage)
        if not filename:
            continue
        fpath = run_dir / filename
        if not fpath.exists():
            continue
        size = fpath.stat().st_size
        if total + size > _MAX_EMBED_LAYOUT_BYTES and embedded:
            continue
        if size > _MAX_EMBED_LAYOUT_BYTES and not embedded:
            # Still try to embed a single large routing view if it's the only option.
            if stage != "routing":
                continue
        try:
            embedded[stage] = json.loads(fpath.read_text(encoding="utf-8"))
            total += size
        except (OSError, json.JSONDecodeError):
            continue
    return embedded


def _rel_if_exists(target: Path, html_dir: Path) -> Optional[str]:
    if not target.exists():
        return None
    try:
        rel = os.path.relpath(target, html_dir)
    except ValueError:
        rel = str(target)
    return Path(rel).as_posix()


def _layout_links(run_dir: Path, html_dir: Path) -> Dict[str, str]:
    """Stage -> path of each layout PNG, relative to the dashboard location."""
    links: Dict[str, str] = {}
    for stage, filename in STAGE_FILES.items():
        image = run_dir / filename
        link = _rel_if_exists(image, html_dir)
        if link:
            links[stage] = link
    return links


def _layout_view_links(run_dir: Path, html_dir: Path) -> Dict[str, str]:
    links: Dict[str, str] = {}
    for stage, filename in STAGE_VIEW_FILES.items():
        link = _rel_if_exists(run_dir / filename, html_dir)
        if link:
            links[stage] = link
    # Prefer canonical layout_view.json as routing if present
    canon = _rel_if_exists(run_dir / LAYOUT_VIEW_FILE, html_dir)
    if canon:
        links.setdefault("routing", canon)
    return links


def _find_scoreboard(html_dir: Path) -> Optional[str]:
    for name in ("scoreboard.csv", "scoreboard.json"):
        link = _rel_if_exists(html_dir / name, html_dir)
        if link:
            return link if name.endswith(".csv") else None
    # Walk one level for batch trees that put scoreboard at root while HTML is same dir
    csv = html_dir / "scoreboard.csv"
    return _rel_if_exists(csv, html_dir)


def write_html_report(
    reports: Sequence[Mapping[str, Any]],
    out_path: Path,
    title: str = "PnR QoR Reports",
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Prevent embedded JSON / strings from prematurely closing the page <script>.
    payload = (
        json.dumps(list(reports), indent=2)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scoreboard = _find_scoreboard(out_path.parent) or ""
    scoreboard_style = "" if scoreboard else "display:none"
    html = (
        _TEMPLATE.replace("__TITLE__", title)
        .replace("__GENERATED__", generated)
        .replace("__REPORTS_JSON__", payload)
        .replace("__SCOREBOARD_CSV__", scoreboard)
        .replace("__SCOREBOARD_STYLE__", scoreboard_style)
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path


def generate_from_runs(
    runs_dir: Path,
    out_path: Path | None = None,
    title: str = "PnR QoR Reports",
) -> Path:
    runs_dir = Path(runs_dir)
    out_path = Path(out_path) if out_path else runs_dir / "index.html"
    paths = find_qor_files(runs_dir)
    reports = load_reports(paths, html_dir=out_path.parent)
    return write_html_report(reports, out_path, title=title)


# Large self-contained dashboard template (Compare + Layout viewer + QoR detail).
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    html[data-theme="dark"] {
      --bg0: #0f1419;
      --bg1: #1a222c;
      --bg2: #243040;
      --line: #334155;
      --text: #e7eef7;
      --muted: #94a3b8;
      --pass: #3dba7a;
      --fail: #e35d6a;
      --warn: #d4a24c;
      --accent: #5ec8d8;
      --header-bg: rgba(15, 20, 25, 0.9);
      --aside-bg: rgba(26, 34, 44, 0.85);
      --chip-pass-bg: rgba(61, 186, 122, 0.18);
      --chip-fail-bg: rgba(227, 93, 106, 0.18);
      --chip-type-bg: rgba(94, 200, 216, 0.14);
      --glow-a: #1c3a44;
      --glow-b: #2a2438;
      --row-alt: rgba(255, 255, 255, 0.03);
      --shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    }
    html[data-theme="light"] {
      --bg0: #f4f1ea;
      --bg1: #ffffff;
      --bg2: #ebe6dc;
      --line: #d5d0c6;
      --text: #1c2430;
      --muted: #5f6b7a;
      --pass: #1f8f57;
      --fail: #c23b4a;
      --warn: #b07a20;
      --accent: #0f6e7c;
      --header-bg: rgba(255, 255, 255, 0.92);
      --aside-bg: rgba(255, 255, 255, 0.88);
      --chip-pass-bg: rgba(31, 143, 87, 0.12);
      --chip-fail-bg: rgba(194, 59, 74, 0.12);
      --chip-type-bg: rgba(15, 110, 124, 0.1);
      --glow-a: #dce9e7;
      --glow-b: #efe4d4;
      --row-alt: rgba(0, 0, 0, 0.025);
      --shadow: 0 8px 24px rgba(28, 36, 48, 0.08);
    }
    :root {
      --mono: "IBM Plex Mono", "Cascadia Mono", "Consolas", monospace;
      --sans: "IBM Plex Sans", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      color: var(--text);
      background:
        radial-gradient(1200px 600px at 10% -10%, var(--glow-a) 0%, transparent 55%),
        radial-gradient(900px 500px at 100% 0%, var(--glow-b) 0%, transparent 50%),
        var(--bg0);
      min-height: 100vh;
      transition: background 0.2s ease, color 0.2s ease;
    }
    header {
      padding: 1.25rem 1.75rem 0.85rem;
      border-bottom: 1px solid var(--line);
      background: var(--header-bg);
      backdrop-filter: blur(8px);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    header h1 { margin: 0 0 0.35rem; font-size: 1.45rem; letter-spacing: 0.02em; font-weight: 600; }
    header .sub { color: var(--muted); font-size: 0.9rem; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-top: 0.85rem; }
    .btn, .file-btn {
      appearance: none; border: 1px solid var(--line); background: var(--bg2); color: var(--text);
      padding: 0.45rem 0.85rem; border-radius: 6px; font: inherit; cursor: pointer; text-decoration: none;
    }
    .btn:hover, .file-btn:hover { border-color: var(--accent); color: var(--accent); }
    .file-btn input { display: none; }
    .theme-toggle { display: inline-flex; align-items: center; gap: 0.4rem; }
    .stats { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-left: auto; color: var(--muted); font-size: 0.85rem; }
    .stat strong { color: var(--text); }
    .tabs { display: flex; gap: 0.35rem; margin-top: 0.85rem; flex-wrap: wrap; }
    .tab {
      border: 1px solid var(--line); background: transparent; color: var(--muted);
      padding: 0.4rem 0.9rem; border-radius: 6px 6px 0 0; cursor: pointer; font: inherit;
    }
    .tab.active { background: var(--bg1); color: var(--accent); border-bottom-color: var(--bg1); }
    main { display: grid; grid-template-columns: 280px 1fr; min-height: calc(100vh - 180px); }
    @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
    aside { border-right: 1px solid var(--line); background: var(--aside-bg); padding: 1rem; overflow: auto; }
    .run-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; }
    .run-item {
      width: 100%; text-align: left; border: 1px solid var(--line); background: var(--bg1); color: var(--text);
      border-radius: 8px; padding: 0.7rem 0.8rem; cursor: pointer; font: inherit;
    }
    .run-item.active { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
    .run-item .name { font-weight: 600; display: block; margin-bottom: 0.25rem; }
    .run-item .path { color: var(--muted); font-size: 0.75rem; font-family: var(--mono); word-break: break-all; }
    .badge {
      display: inline-block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em;
      padding: 0.15rem 0.45rem; border-radius: 999px; margin-top: 0.35rem;
    }
    .badge.pass { background: var(--chip-pass-bg); color: var(--pass); }
    .badge.fail { background: var(--chip-fail-bg); color: var(--fail); }
    .type-chip {
      display: inline-block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.03em;
      text-transform: uppercase; padding: 0.12rem 0.4rem; border-radius: 4px;
      background: var(--chip-type-bg); color: var(--accent); white-space: nowrap;
    }
    section.content { padding: 1.25rem 1.5rem 2rem; overflow: auto; }
    .empty {
      color: var(--muted); border: 1px dashed var(--line); border-radius: 10px;
      padding: 2rem; text-align: center;
    }
    .hero { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; justify-content: space-between; margin-bottom: 1.25rem; }
    .hero h2 { margin: 0 0 0.35rem; font-size: 1.35rem; }
    .hero .meta { color: var(--muted); font-family: var(--mono); font-size: 0.8rem; }
    .cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.85rem; margin-bottom: 1.25rem; }
    @media (max-width: 900px) { .cards { grid-template-columns: 1fr; } }
    .card {
      background: var(--bg1); border: 1px solid var(--line); border-radius: 10px;
      padding: 0.95rem 1rem; box-shadow: var(--shadow);
    }
    .card h3 {
      margin: 0 0 0.55rem; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--muted); display: flex; justify-content: space-between; align-items: center;
    }
    .metric { font-size: 1.35rem; font-weight: 650; margin-bottom: 0.35rem; }
    .metric.pass { color: var(--pass); }
    .metric.fail { color: var(--fail); }
    .kv { display: grid; gap: 0.25rem; font-size: 0.85rem; color: var(--muted); }
    .kv span b { color: var(--text); font-weight: 600; }
    .panel {
      background: var(--bg1); border: 1px solid var(--line); border-radius: 10px;
      margin-bottom: 0.9rem; overflow: hidden; box-shadow: var(--shadow);
    }
    .panel-head {
      display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
      padding: 0.8rem 1rem; border-bottom: 1px solid var(--line); background: var(--bg2);
    }
    .panel-head h3 { margin: 0; font-size: 0.95rem; font-weight: 650; }
    .panel-body { padding: 0.85rem 1rem 1rem; }
    .summary-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.65rem; margin-bottom: 0.85rem;
    }
    .summary-item {
      border: 1px solid var(--line); border-radius: 8px; padding: 0.65rem 0.75rem; background: var(--bg0);
    }
    .summary-item .label {
      display: block; color: var(--muted); font-size: 0.75rem; text-transform: uppercase;
      letter-spacing: 0.04em; margin-bottom: 0.25rem;
    }
    .summary-item .value { font-family: var(--mono); font-size: 0.92rem; font-weight: 600; color: var(--text); word-break: break-word; }
    .ir-heatmap-wrap { display: flex; gap: 1rem; align-items: flex-start; flex-wrap: wrap; margin: 0.75rem 0; }
    .ir-heatmap { border: 1px solid var(--line); border-radius: 8px; background: var(--bg0); padding: 0.35rem; }
    .ir-heatmap svg { display: block; width: min(100%, 360px); height: auto; }
    .ir-legend { font-size: 0.75rem; color: var(--muted); max-width: 220px; }
    .table-wrap { overflow: auto; max-height: 420px; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
    th, td { border-bottom: 1px solid var(--line); padding: 0.5rem 0.55rem; text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; background: var(--bg2); color: var(--muted); font-weight: 650; z-index: 1; cursor: pointer; }
    th:hover { color: var(--accent); }
    tbody tr:nth-child(even) { background: var(--row-alt); }
    td.mono { font-family: var(--mono); font-size: 0.8rem; }
    .muted { color: var(--muted); }
    .ok-text { color: var(--pass); font-weight: 600; }
    .bad-text { color: var(--fail); font-weight: 600; }
    .filter-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0 0 0.75rem; }
    .filter-chip {
      border: 1px solid var(--line); background: var(--bg0); color: var(--muted);
      padding: 0.28rem 0.7rem; border-radius: 999px; font: inherit; font-size: 0.8rem; cursor: pointer;
    }
    .filter-chip.active { border-color: var(--accent); color: var(--accent); background: rgba(15, 110, 124, 0.1); }
    .rail-vpwr { color: #e35d6a; font-weight: 700; }
    .rail-vgnd { color: #5b8def; font-weight: 700; }
    .method-note { color: var(--muted); font-size: 0.8rem; margin: 0.65rem 0 0; line-height: 1.45; }
    .mode-pill {
      display: inline-block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.03em;
      padding: 0.12rem 0.45rem; border-radius: 4px; background: var(--chip-type-bg); color: var(--accent);
    }
    details.raw {
      background: var(--bg1); border: 1px solid var(--line); border-radius: 10px;
      margin-bottom: 0.75rem; padding: 0.65rem 0.9rem;
    }
    details.raw summary { cursor: pointer; font-weight: 600; color: var(--accent); }
    pre {
      margin: 0.65rem 0 0; padding: 0.75rem; overflow: auto; background: var(--bg0);
      border-radius: 8px; font-family: var(--mono); font-size: 0.78rem; line-height: 1.45; max-height: 360px;
    }
    .notes { color: var(--muted); font-size: 0.85rem; margin-top: 0.75rem; }
    .notes li { margin: 0.2rem 0; }
    .layout-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.85rem; }
    figure.layout-card {
      margin: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--bg0); overflow: hidden;
    }
    figure.layout-card figcaption {
      padding: 0.5rem 0.65rem; font-size: 0.78rem; font-weight: 650; letter-spacing: 0.04em;
      text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line); background: var(--bg2);
    }
    figure.layout-card a { display: block; line-height: 0; }
    figure.layout-card img { width: 100%; height: auto; display: block; background: #0f1419; }
    figure.layout-card .missing {
      padding: 1.5rem 0.75rem; text-align: center; color: var(--muted); font-size: 0.82rem; line-height: 1.4;
    }
    .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 0.85rem; margin-top: 0.85rem; }
    .chart-box {
      border: 1px solid var(--line); border-radius: 8px; background: var(--bg0); padding: 0.65rem 0.75rem;
    }
    .chart-box h4 { margin: 0 0 0.5rem; font-size: 0.85rem; color: var(--muted); font-weight: 650; }
    .chart-box canvas, .chart-box svg { width: 100%; height: 220px; display: block; }
    /* EDA layout cockpit */
    .layout-viewer#layoutViewerShell {
      display: flex; flex-direction: column; background: #0a0e14; border: 1px solid #1e2a38;
      border-radius: 8px; overflow: hidden; box-shadow: 0 12px 40px rgba(0,0,0,0.45);
    }
    .layout-viewer#layoutViewerShell:fullscreen,
    .layout-viewer#layoutViewerShell:-webkit-full-screen {
      border-radius: 0; width: 100vw; height: 100vh; background: #070a0f;
    }
    .layout-viewer#layoutViewerShell:fullscreen .png-fallback,
    .layout-viewer#layoutViewerShell:-webkit-full-screen .png-fallback { display: none !important; }
    .eda-titlebar {
      display: flex; align-items: center; justify-content: space-between; gap: 0.75rem;
      padding: 0.45rem 0.75rem; background: linear-gradient(180deg, #1a2430 0%, #121820 100%);
      border-bottom: 1px solid #243040; font-size: 0.78rem;
    }
    .eda-titlebar .brand {
      font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #7dd3e8;
      font-family: var(--mono); font-size: 0.72rem;
    }
    .eda-titlebar .readout { color: #94a3b8; font-family: var(--mono); font-size: 0.72rem; }
    .eda-toolbar {
      display: flex; flex-wrap: wrap; gap: 0.45rem 0.65rem; align-items: center;
      padding: 0.5rem 0.65rem; background: #0f1520; border-bottom: 1px solid #1e2a38;
    }
    .eda-toolbar .group {
      display: inline-flex; flex-wrap: wrap; gap: 0.3rem; align-items: center;
      padding-right: 0.55rem; margin-right: 0.15rem; border-right: 1px solid #243040;
    }
    .eda-toolbar .group:last-child { border-right: none; }
    .eda-toolbar label.field {
      font-size: 0.72rem; color: #8b9bb0; display: inline-flex; align-items: center; gap: 0.3rem;
      font-family: var(--mono); text-transform: uppercase; letter-spacing: 0.04em;
    }
    .eda-toolbar select, .eda-toolbar input[type="range"] {
      background: #162030; color: #e7eef7; border: 1px solid #2a3a4c; border-radius: 4px;
      font: inherit; font-size: 0.78rem; padding: 0.2rem 0.35rem;
    }
    .eda-toolbar input[type="range"] { width: 72px; padding: 0; accent-color: #5ec8d8; }
    .layer-chip {
      appearance: none; border: 1px solid #2a3a4c; background: #121a24; color: #c8d3e0;
      border-radius: 999px; padding: 0.18rem 0.55rem 0.18rem 0.35rem; cursor: pointer;
      font-family: var(--mono); font-size: 0.7rem; font-weight: 650; display: inline-flex;
      align-items: center; gap: 0.3rem; transition: border-color 0.12s, opacity 0.12s, background 0.12s;
    }
    .layer-chip .swatch {
      width: 9px; height: 9px; border-radius: 2px; box-shadow: 0 0 0 1px rgba(0,0,0,0.4);
    }
    .layer-chip.off { opacity: 0.38; filter: grayscale(0.4); }
    .layer-chip.on { border-color: var(--chip-c, #5ec8d8); background: #152030; }
    .obj-tog {
      appearance: none; border: 1px solid #2a3a4c; background: #121a24; color: #94a3b8;
      border-radius: 4px; padding: 0.2rem 0.5rem; cursor: pointer; font-size: 0.72rem;
      font-family: var(--mono); font-weight: 650; letter-spacing: 0.03em;
    }
    .obj-tog.on { color: #e7eef7; border-color: #3d5a6e; background: #1a2838; }
    .eda-btn {
      appearance: none; border: 1px solid #2f4558; background: linear-gradient(180deg, #243447, #1a2634);
      color: #dbe7f3; border-radius: 4px; padding: 0.28rem 0.65rem; cursor: pointer;
      font-size: 0.75rem; font-weight: 650; font-family: var(--sans);
    }
    .eda-btn:hover { border-color: #5ec8d8; color: #7dd3e8; }
    .eda-btn.primary { border-color: #3a7a88; background: linear-gradient(180deg, #1e4a55, #163740); color: #b8eef7; }
    .eda-canvas-wrap {
      position: relative; flex: 1; min-height: min(38vh, 360px); max-height: min(42vh, 400px);
      background: #05080c;
    }
    .layout-viewer#layoutViewerShell:fullscreen .eda-canvas-wrap,
    .layout-viewer#layoutViewerShell:-webkit-full-screen .eda-canvas-wrap {
      min-height: 0; max-height: none; flex: 1;
    }
    #layoutCanvas {
      width: 100%; height: 100%; min-height: min(38vh, 360px); display: block;
      background: #05080c; cursor: crosshair; touch-action: none;
    }
    .layout-viewer#layoutViewerShell:fullscreen #layoutCanvas,
    .layout-viewer#layoutViewerShell:-webkit-full-screen #layoutCanvas { min-height: 100%; }
    .eda-tooltip {
      position: absolute; top: 10px; right: 10px; min-width: 200px; max-width: 280px;
      pointer-events: none; font-family: var(--mono); font-size: 0.72rem; color: #e7eef7;
      background: rgba(12, 20, 30, 0.92); border: 1px solid #3a7a88; border-radius: 6px;
      padding: 0.55rem 0.7rem; box-shadow: 0 8px 24px rgba(0,0,0,0.45); display: none;
    }
    .eda-tooltip.visible { display: block; }
    .eda-tooltip .tt-title { color: #7dd3e8; font-weight: 700; margin-bottom: 0.35rem; letter-spacing: 0.03em; }
    .eda-tooltip .tt-row { color: #b8c6d6; margin: 0.12rem 0; }
    .eda-tooltip .tt-row b { color: #f0f4f8; font-weight: 650; }
    #layoutCanvas.panning { cursor: grabbing; }
    .eda-hud {
      position: absolute; left: 10px; bottom: 10px; right: 10px;
      display: flex; flex-wrap: wrap; gap: 0.65rem 1.1rem; pointer-events: none;
      font-family: var(--mono); font-size: 0.72rem; color: #c5d4e4;
      background: rgba(8, 14, 22, 0.82); border: 1px solid #243040; border-radius: 6px;
      padding: 0.4rem 0.65rem; backdrop-filter: blur(6px);
    }
    .eda-hud b { color: #7dd3e8; font-weight: 650; }
    .eda-hud .sel { color: #e8a33d; }
    .viewer-note {
      font-size: 0.72rem; color: #6b7c90; margin: 0; padding: 0.4rem 0.75rem;
      border-top: 1px solid #1e2a38; background: #0c121a; font-family: var(--mono);
    }
    .png-fallback { margin-top: 0.9rem; }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <header>
    <h1>__TITLE__</h1>
    <div class="sub">Generated __GENERATED__ · layout viewer uses embedded stage data (works with file://)</div>
    <div class="toolbar">
      <label class="file-btn">Open QoR JSON<input id="fileInput" type="file" accept=".json,application/json" multiple /></label>
      <button class="btn" id="clearExtra" type="button">Clear loaded files</button>
      <button class="btn theme-toggle" id="themeToggle" type="button" aria-label="Toggle color theme">
        <span class="icon" id="themeIcon">☀</span>
        <span id="themeLabel">Light mode</span>
      </button>
      <a class="btn" id="scoreboardLink" href="__SCOREBOARD_CSV__" download style="__SCOREBOARD_STYLE__">Scoreboard CSV</a>
      <div class="stats">
        <div class="stat">Reports: <strong id="count">0</strong></div>
      </div>
    </div>
    <div class="tabs" role="tablist">
      <button type="button" class="tab active" data-tab="detail" id="tab-detail">QoR detail</button>
      <button type="button" class="tab" data-tab="compare" id="tab-compare">Compare</button>
      <button type="button" class="tab" data-tab="viewer" id="tab-viewer">Layout viewer</button>
    </div>
  </header>
  <main>
    <aside>
      <ul class="run-list" id="runList"></ul>
    </aside>
    <section class="content" id="content">
      <div class="empty">Select a run on the left, or open a <code>.qor.json</code> file.</div>
    </section>
  </main>
  <script>
    const EMBEDDED = __REPORTS_JSON__;
    const SCOREBOARD_CSV = "__SCOREBOARD_CSV__";
    let reports = Array.isArray(EMBEDDED) ? EMBEDDED.slice() : [];
    let extras = [];
    let activeIdx = 0;
    let activeTab = "detail";
    let compareSort = { key: "design", dir: 1 };

    const LAYER_COLORS = {
      met1: "#5ec8d8", met2: "#e0674f", met3: "#7fc97f", met4: "#c286d8", met5: "#e8d44d", li1: "#9aa5b1"
    };

    const viewerState = {
      data: null,
      stage: "routing",
      scale: 1,
      ox: 0,
      oy: 0,
      dragging: false,
      lastX: 0,
      lastY: 0,
      layers: { met1: true, met2: true, met3: true, met4: true, met5: true, li1: true },
      showCells: true,
      showBuffers: true,
      showPorts: true,
      showPins: true,
      showPinNames: true,
      showPowerPins: false,
      showVdd: true,
      showVss: true,
      showGrid: true,
      metalOpacity: 0.92,
      cellOpacity: 0.72,
      cursorUm: null,
      hoverCell: null,
      designName: "",
      dpr: 1,
      cssW: 900,
      cssH: 560,
    };
    let viewerKeyHandler = null;
    let viewerResizeObs = null;

    const el = (id) => document.getElementById(id);

    function allReports() { return reports.concat(extras); }

    function fmt(n, digits = 3) {
      if (n === null || n === undefined || Number.isNaN(n)) return "—";
      if (typeof n !== "number") return String(n);
      return Math.abs(n) >= 1000 ? n.toFixed(1) : n.toFixed(digits);
    }

    function fmtList(arr, digits = 3) {
      if (!Array.isArray(arr) || !arr.length) return "—";
      return arr.map((x) => (typeof x === "number" ? fmt(x, digits) : String(x))).join(", ");
    }

    function typeChip(type) {
      const t = escapeHtml(type || "unknown");
      return `<span class="type-chip ${t}">${t}</span>`;
    }

    function escapeHtml(s) {
      return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function updateStats() {
      const all = allReports();
      el("count").textContent = String(all.length);
    }

    function applyTheme(theme) {
      const next = theme === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("pnr-qor-theme", next); } catch (_) {}
      el("themeIcon").textContent = next === "dark" ? "☀" : "☾";
      el("themeLabel").textContent = next === "dark" ? "Light mode" : "Dark mode";
      if (activeTab === "compare") renderCompare();
      if (activeTab === "viewer") drawViewer();
    }

    function initTheme() {
      let theme = "dark";
      try {
        const saved = localStorage.getItem("pnr-qor-theme");
        if (saved === "light" || saved === "dark") theme = saved;
        else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) theme = "light";
      } catch (_) {}
      applyTheme(theme);
    }

    function initScoreboardLink() {
      const link = el("scoreboardLink");
      if (!SCOREBOARD_CSV) {
        link.classList.add("hidden");
        link.removeAttribute("href");
      } else {
        link.classList.remove("hidden");
        link.style.cssText = "";
        link.href = SCOREBOARD_CSV;
      }
    }

    function setTab(tab) {
      activeTab = tab;
      document.querySelectorAll(".tab").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tab);
      });
      renderActive();
    }

    function renderList() {
      const all = allReports();
      const list = el("runList");
      if (!all.length) {
        list.innerHTML = `<li class="empty" style="padding:1rem;border-style:dashed;">No reports found</li>`;
        el("content").innerHTML = `<div class="empty">No QoR reports embedded. Use <b>Open QoR JSON</b> to load one.</div>`;
        updateStats();
        return;
      }
      list.innerHTML = all.map((r, i) => `
        <li>
          <button type="button" class="run-item ${i === activeIdx ? "active" : ""}" data-idx="${i}">
            <span class="name">${escapeHtml(r.design || r._source_name || "unnamed")}</span>
            <span class="path">${escapeHtml(r._source || r._source_name || "loaded file")}</span>
          </button>
        </li>
      `).join("");
      list.querySelectorAll(".run-item").forEach(btn => {
        btn.addEventListener("click", () => {
          activeIdx = Number(btn.dataset.idx);
          renderList();
          renderActive();
        });
      });
      updateStats();
      renderActive();
    }

    function renderActive() {
      if (activeTab === "compare") renderCompare();
      else if (activeTab === "viewer") renderViewer();
      else renderDetail();
    }

    function checkCard(name, check, report) {
      if (!check) return "";
      let body = "";
      if (name === "drc") {
        body = `
          <div class="metric">${check.violations ?? "—"}</div>
          <div class="kv">
            <span>Violations: <b>${check.violations ?? "—"}</b></span>
            <span>Spatial backend: <b>${escapeHtml(check.spatial_backend || "—")}</b></span>
          </div>`;
      } else if (name === "sta") {
        const summary = ((report || {}).sta_details || {}).summary || {};
        const setupV = check.setup_violations ?? summary.setup_failing;
        const holdV = check.hold_violations ?? summary.hold_failing;
        const setupWns = check.setup_wns_ps ?? check.wns_ps;
        const holdWns = check.hold_wns_ps;
        body = `
          <div class="metric">${setupV ?? "—"} / ${holdV ?? "—"}</div>
          <div class="kv">
            <span>Setup violations: <b>${setupV ?? "—"}</b></span>
            <span>Hold violations: <b>${holdV ?? "—"}</b></span>
            <span>Setup WNS: <b>${fmt(setupWns, 2)} ps</b></span>
            <span>Hold WNS: <b>${fmt(holdWns, 2)} ps</b></span>
            <span>Corner: <b>${escapeHtml((check.corners_loaded || []).join("/") || check.corner || "ff")}</b></span>
            <span>Period: <b>${fmt(check.clock_period_ns, 3)} ns</b></span>
          </div>`;
      } else if (name === "ir_drop") {
        const affected = check.instances_affected ?? check.violations ?? "—";
        const vdd = Number(check.vdd || 0);
        const dropPct = vdd > 0 ? 100 * Number(check.max_ir_drop || 0) / vdd : null;
        body = `
          <div class="metric">${affected}</div>
          <div class="kv">
            <span>Instances affected: <b>${affected}</b></span>
            <span>Max drop: <b>${fmt(check.max_ir_drop, 6)} V</b> (${dropPct == null ? "—" : fmt(dropPct, 2)}%)</span>
            <span>Min V: <b>${fmt(check.min_voltage, 6)} V</b></span>
            <span>Min V raw: <b>${fmt(check.min_voltage_raw, 6)} V</b></span>
            <span>Threshold: <b>${fmt(check.threshold, 6)} V</b></span>
            <span>Ground bounce: <b>${fmt(check.max_ground_bounce, 6)} V</b></span>
            <span>J max / viol: <b>${fmt(check.max_j_ma_per_um, 3)} mA/µm / ${check.j_violations ?? 0}</b></span>
            <span>Source / corner: <b>${escapeHtml(check.source_type || "—")} / ${escapeHtml(check.corner || "tt")}</b></span>
            <span>Total I: <b>${fmt(check.total_current_a, 6)} A</b></span>
            <span>Mode: <b class="mode-pill">${escapeHtml(check.ir_mode || "—")}</b></span>
          </div>`;
      }
      const title = name === "ir_drop" ? "ir drop" : name === "sta" ? "sta" : name.replace("_", " ");
      return `<article class="card"><h3>${title}</h3>${body}</article>`;
    }

    function renderStaPanel(r) {
      const check = (r.checks || {}).sta || {};
      const details = r.sta_details || {};
      const endpoints = details.endpoints || [];
      const summary = details.summary || {};
      const cts = check.cts_latency || details.cts_latency || {};
      const wireModel = check.wire_model || details.wire_model || "—";
      const crit = details.critical_path || {};
      const critPins = Array.isArray(crit.pins) ? crit.pins.join(" → ") : "";
      const stages = Array.isArray(crit.stages) ? crit.stages : [];
      const corners = (check.corners_loaded || []).join("/") || (check.corner || "ff");

      const stageRows = stages.map(s => `
        <tr>
          <td class="mono">${escapeHtml(s.pin || "—")}</td>
          <td class="mono">${fmt(s.arrival_ns, 4)}</td>
          <td class="mono">${fmt(s.slew_ns, 4)}</td>
          <td class="mono">${fmt(s.cell_delay_ns, 4)}</td>
          <td class="mono">${fmt(s.net_delay_ns, 4)}</td>
          <td class="mono">${escapeHtml(s.sense || "—")}</td>
        </tr>`).join("");
      const stageTable = stages.length ? `<div class="table-wrap" style="margin-top:0.6rem"><table>
          <thead><tr><th>Pin</th><th>Arrival</th><th>Slew</th><th>Cell</th><th>Net</th><th>Sense</th></tr></thead>
          <tbody>${stageRows}</tbody>
        </table></div>` : "";

      const summaryHtml = `
        <div class="summary-grid">
          <div class="summary-item"><span class="label">Period</span><span class="value">${fmt(check.clock_period_ns, 3)} ns</span></div>
          <div class="summary-item"><span class="label">Corners</span><span class="value">${escapeHtml(corners)}</span></div>
          <div class="summary-item"><span class="label">Setup WNS</span><span class="value ${(check.setup_wns_ps ?? check.wns_ps) < 0 ? "bad-text" : "ok-text"}">${fmt(check.setup_wns_ps ?? check.wns_ps, 2)} ps</span></div>
          <div class="summary-item"><span class="label">Hold WNS</span><span class="value ${check.hold_wns_ps < 0 ? "bad-text" : "ok-text"}">${fmt(check.hold_wns_ps, 2)} ps</span></div>
          <div class="summary-item"><span class="label">CTS skew</span><span class="value">${fmt(cts.skew_ns, 4)} ns</span></div>
          <div class="summary-item"><span class="label">Uncertainty</span><span class="value">${fmt(check.uncertainty_ns, 3)} ns</span></div>
          <div class="summary-item"><span class="label">CRPR</span><span class="value">${check.use_crpr === false ? "off" : "on"}</span></div>
          <div class="summary-item"><span class="label">Ceff</span><span class="value">${check.use_ceff === false ? "off" : "on"}</span></div>
          <div class="summary-item"><span class="label">Wire model</span><span class="value">${escapeHtml(wireModel)}</span></div>
          <div class="summary-item"><span class="label">Loops broken</span><span class="value">${check.loops_broken ?? 0}</span></div>
          <div class="summary-item"><span class="label">Failing setup</span><span class="value">${summary.setup_failing ?? "—"}</span></div>
          <div class="summary-item"><span class="label">Failing hold</span><span class="value">${summary.hold_failing ?? "—"}</span></div>
        </div>
        ${crit.endpoint ? `<p class="muted" style="margin-top:0.6rem">Critical path (${escapeHtml(crit.endpoint)}, slack ${fmt(crit.slack_ns, 4)} ns, cell ${fmt(crit.cell_delay_ns, 4)} / net ${fmt(crit.net_delay_ns, 4)} ns, CRPR ${fmt(crit.crpr_ns, 4)} ns): <span class="mono">${escapeHtml(critPins || "—")}</span></p>${stageTable}` : ""}`;

      if (!endpoints.length) {
        return `<section class="panel" id="staPanel">
          <div class="panel-head"><h3>Timing analysis</h3><span class="muted">${escapeHtml(check.corner || "ff")} corner</span></div>
          <div class="panel-body">
            ${summaryHtml}
            <p class="muted">No sequential / port endpoints listed for this report.</p>
          </div>
        </section>`;
      }

      // Keep endpoint JSON in a JS global (not a nested script tag in innerHTML).
      window.__STA_ENDPOINTS__ = endpoints;
      return `<section class="panel" id="staPanel">
        <div class="panel-head"><h3>Timing analysis</h3><span class="muted">${escapeHtml(check.corner || "ff")} · setup + hold</span></div>
        <div class="panel-body">
          ${summaryHtml}
          <div class="filter-chips" id="staFilters">
            <button type="button" class="filter-chip active" data-filter="all">All</button>
            <button type="button" class="filter-chip" data-filter="setup">Setup</button>
            <button type="button" class="filter-chip" data-filter="hold">Hold</button>
            <button type="button" class="filter-chip" data-filter="failing">Failing only</button>
          </div>
          <div id="staEndpointTable"></div>
        </div>
      </section>`;
    }

    function bindStaFilters() {
      const tableHost = document.getElementById("staEndpointTable");
      const chips = document.getElementById("staFilters");
      if (!tableHost || !chips) return;
      const endpoints = Array.isArray(window.__STA_ENDPOINTS__) ? window.__STA_ENDPOINTS__ : [];

      function render(filter) {
        let rows = endpoints.slice();
        if (filter === "setup") rows = rows.filter(e => (e.check || "setup") === "setup");
        else if (filter === "hold") rows = rows.filter(e => e.check === "hold");
        else if (filter === "failing") {
          rows = rows.filter(e => Number(e.slack_ns) < 0);
          rows.sort((a, b) => Number(a.slack_ns) - Number(b.slack_ns));
        }
        if (!rows.length) {
          tableHost.innerHTML = `<p class="muted">No endpoints for this filter.</p>`;
          return;
        }
        const body = rows.map(e => `
          <tr>
            <td>${typeChip(e.check || "setup")}</td>
            <td class="mono">${escapeHtml(e.endpoint || "—")}</td>
            <td class="mono">${fmt(e.arrival_ns, 4)}</td>
            <td class="mono">${fmt(e.required_ns, 4)}</td>
            <td class="mono ${Number(e.slack_ns) < 0 ? "bad-text" : "ok-text"}">${fmt(e.slack_ns, 4)}</td>
            <td class="mono">${fmt(e.launch_latency_ns, 4)}</td>
            <td class="mono">${fmt(e.capture_latency_ns, 4)}</td>
            <td class="mono">${fmt(e.crpr_ns, 4)}</td>
            <td class="mono">${escapeHtml(e.path_sense || "—")}</td>
          </tr>`).join("");
        tableHost.innerHTML = `<div class="table-wrap"><table>
          <thead><tr>
            <th>Check</th><th>Endpoint</th><th>Arrival</th><th>Required</th><th>Slack</th>
            <th>Launch lat</th><th>Capture lat</th><th>CRPR</th><th>Sense</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table></div>`;
      }

      chips.querySelectorAll(".filter-chip").forEach(btn => {
        btn.addEventListener("click", () => {
          chips.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          render(btn.getAttribute("data-filter") || "all");
        });
      });
      render("all");
    }

    function drcDetail(v) {
      const t = v.type || "unknown";
      if (t === "overlap") {
        return `Cells <b>${escapeHtml(v.a || "?")}</b> and <b>${escapeHtml(v.b || "?")}</b> overlap
          ${v.rule ? ` <span class="muted">[${escapeHtml(v.rule)}]</span>` : ""}<br>
          <span class="muted mono">A: [${fmtList(v.bbox_a)}] · B: [${fmtList(v.bbox_b)}]</span>`;
      }
      if (t === "short") {
        return `Nets <b>${escapeHtml(v.net_a || "?")}</b> / <b>${escapeHtml(v.net_b || "?")}</b> short on <b>${escapeHtml(v.layer || "—")}</b>
          ${v.rule ? ` <span class="muted">[${escapeHtml(v.rule)}]</span>` : ""}<br>
          <span class="muted mono">Segment: [${fmtList(v.segment || v.bbox_a)}]</span>`;
      }
      if (t === "short_gcell") {
        return `Gcell congestion: nets <b>${escapeHtml(v.net_a || "?")}</b> / <b>${escapeHtml(v.net_b || "?")}</b> on <b>${escapeHtml(v.layer || "—")}</b>
          ${v.rule ? ` <span class="muted">[${escapeHtml(v.rule)}]</span>` : ""}<br>
          <span class="muted mono">Edge: [${fmtList(v.segment)}]</span>`;
      }
      if (t === "spacing") {
        return `Nets <b>${escapeHtml(v.net_a || "?")}</b> / <b>${escapeHtml(v.net_b || "?")}</b> on <b>${escapeHtml(v.layer || "—")}</b>
          ${v.rule ? ` <span class="muted">[${escapeHtml(v.rule)}]</span>` : ""}<br>
          <span class="muted mono">Distance ${fmt(v.distance, 4)} um &lt; required ${fmt(v.required, 4)} um${v.same_net ? " (same-net)" : ""}</span>`;
      }
      if (t === "open") {
        return `Net <b>${escapeHtml(v.net || "?")}</b> open — ${escapeHtml(v.reason || "no route")}
          ${v.rule ? ` <span class="muted">[${escapeHtml(v.rule)}]</span>` : ""}`;
      }
      if (t === "enclosure") {
        return `Enclosure <b>${escapeHtml(v.rule || "enclosure")}</b> net <b>${escapeHtml(v.net || "?")}</b>
          ${v.endpoint ? ` pin ${escapeHtml(v.endpoint)}` : ""} — ${escapeHtml(v.reason || "")}`;
      }
      if (t === "min_width") {
        return `Min width on <b>${escapeHtml(v.layer || "—")}</b> net <b>${escapeHtml(v.net || "?")}</b>
          (${fmt(v.width, 4)} &lt; ${fmt(v.required, 4)} um)`;
      }
      if (t === "offgrid") {
        return `Off manufacturing grid (${fmt(v.grid_um, 4)} um) net <b>${escapeHtml(v.net || "?")}</b>
          <span class="mono">(${fmt(v.x, 4)}, ${fmt(v.y, 4)})</span>`;
      }
      if (t === "obs_short") {
        return `Wire <b>${escapeHtml(v.net_a || "?")}</b> vs OBS <b>${escapeHtml(v.net_b || "?")}</b> on <b>${escapeHtml(v.layer || "—")}</b>`;
      }
      if (t === "via") {
        return `Via ${escapeHtml(v.rule || "")}: nets <b>${escapeHtml(v.net_a || "?")}</b> / <b>${escapeHtml(v.net_b || "?")}</b>`;
      }
      return `<span class="mono">${escapeHtml(JSON.stringify(v))}</span>`;
    }

    function renderDrcPanel(r) {
      const details = r.drc_details || {};
      const viols = details.sample_violations || [];
      let counts = details.counts_by_type;
      if (!counts || !Object.keys(counts).length) {
        counts = {};
        viols.forEach(v => { const t = v.type || "unknown"; counts[t] = (counts[t] || 0) + 1; });
      }
      const summary = Object.keys(counts).length
        ? Object.entries(counts).map(([k, n]) =>
            `<div class="summary-item"><span class="label">${escapeHtml(k)}</span><span class="value">${n}</span></div>`
          ).join("")
        : `<div class="summary-item"><span class="label">Samples</span><span class="value ok-text">0</span></div>`;

      let body;
      if (!viols.length) {
        body = `<p class="ok-text">No DRC sample violations in this report.</p>`;
      } else {
        const rows = viols.map((v, i) => `
          <tr>
            <td class="mono">${i + 1}</td>
            <td>${typeChip(v.type)}</td>
            <td>${drcDetail(v)}</td>
          </tr>`).join("");
        body = `<div class="table-wrap"><table>
          <thead><tr><th>#</th><th>Type</th><th>Details</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
      }

      return `<section class="panel">
        <div class="panel-head">
          <h3>DRC violations</h3>
          <span class="muted">Showing ${viols.length} sample(s)</span>
        </div>
        <div class="panel-body">
          <div class="summary-grid">${summary}</div>
          ${body}
        </div>
      </section>`;
    }

    const LAYOUT_STAGES = [
      ["power", "Power Plan"],
      ["placement", "Placement"],
      ["cts", "Clock Opt (CTS)"],
      ["routing", "Routing"],
    ];
    const POWER_COLORS = { VPWR: "#e35d6a", VGND: "#5b8def" };

    function renderLayoutPanel(r) {
      const images = r._layout_images || {};
      const available = LAYOUT_STAGES.filter(([key]) => images[key]).length;
      const cards = LAYOUT_STAGES.map(([key, label]) => {
        const src = images[key];
        const body = src
          ? `<a href="${escapeHtml(src)}" target="_blank" rel="noopener">
               <img src="${escapeHtml(src)}" alt="${escapeHtml(label)} layout" loading="lazy" />
             </a>`
          : `<div class="missing">No image<br><span class="muted">not available for this report</span></div>`;
        return `<figure class="layout-card"><figcaption>${escapeHtml(label)}</figcaption>${body}</figure>`;
      }).join("");

      const note = available
        ? `Showing ${available} of ${LAYOUT_STAGES.length} stage(s) · click to open full size`
        : `Reports opened from disk cannot resolve image paths`;

      return `<section class="panel">
        <div class="panel-head">
          <h3>Layout (PNG)</h3>
          <span class="muted">${note}</span>
        </div>
        <div class="panel-body">
          <div class="layout-grid">${cards}</div>
        </div>
      </section>`;
    }

    function irDropColor(pct) {
      const t = Math.max(0, Math.min(1, Number(pct || 0) / 5));
      const r = Math.round(34 + t * (220 - 34));
      const g = Math.round(197 + t * (38 - 197));
      const b = Math.round(94 + t * (38 - 94));
      return `rgb(${r},${g},${b})`;
    }

    function renderIrHeatmap(ir, meta) {
      const pts = ir.instance_heatmap || [];
      if (!pts.length) return "";
      const die = (meta && Array.isArray(meta.die_area) && meta.die_area.length >= 4)
        ? meta.die_area.map(Number)
        : [0, 0, 100, 100];
      const [x0, y0, x1, y1] = die;
      const w = 320, h = 240, pad = 10;
      const dx = (x1 - x0) || 1, dy = (y1 - y0) || 1;
      const sx = (x) => pad + ((Number(x) - x0) / dx) * (w - 2 * pad);
      const sy = (y) => h - pad - ((Number(y) - y0) / dy) * (h - 2 * pad);
      const circles = pts.map((p) => {
        const cx = sx(p.x), cy = sy(p.y);
        if (!Number.isFinite(cx) || !Number.isFinite(cy)) return "";
        return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3.2" fill="${irDropColor(p.drop_pct)}"><title>${escapeHtml(String(p.instance || ""))} ${fmt(p.drop_pct, 2)}%</title></circle>`;
      }).join("");
      return `<div class="ir-heatmap-wrap">
        <div class="ir-heatmap">
          <svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="IR drop heatmap">${circles}</svg>
        </div>
        <div class="ir-legend">Instance drop % heatmap (green → 0%, red → 5%+). Cap ${pts.length} samples, including non-violators.</div>
      </div>`;
    }

    function renderIrPanel(r) {
      const check = (r.checks || {}).ir_drop || {};
      const ir = r.ir_details || {};
      const grid = ir.grid || {};
      const currents = ir.currents || {};
      const vddRail = ir.vdd_rail || {};
      const vssRail = ir.vss_rail || {};
      const layers = Array.isArray(grid.layers) ? grid.layers.join(", ") : "—";
      const drops = ir.instance_drops || ir.sample_violations || [];
      const affected = ir.instances_affected ?? check.instances_affected ?? check.violations ?? drops.length;
      const hasError = !!ir.error;
      const mode = ir.ir_mode || check.ir_mode || "—";
      const floating = Array.isArray(ir.floating_nodes)
        ? ir.floating_nodes.length
        : (ir.floating_count ?? check.floating_nodes ?? 0);
      const vdd = Number(check.vdd || 1.8);
      const maxDropPct = vdd > 0 ? 100 * Number(ir.max_ir_drop ?? check.max_ir_drop ?? 0) / vdd : null;
      const jMax = ir.max_j_ma_per_um ?? check.max_j_ma_per_um;
      const jViol = ir.j_violations ?? check.j_violations ?? 0;

      const summary = `
        <div class="summary-grid">
          <div class="summary-item"><span class="label">Instances affected</span><span class="value">${affected}</span></div>
          <div class="summary-item"><span class="label">Max drop</span><span class="value">${fmt(ir.max_ir_drop ?? check.max_ir_drop, 6)} V (${maxDropPct == null ? "—" : fmt(maxDropPct, 2)}%)</span></div>
          <div class="summary-item"><span class="label">Min V / threshold</span><span class="value">${fmt(check.min_voltage, 6)} / ${fmt(check.threshold, 6)} V</span></div>
          <div class="summary-item"><span class="label">Min V raw</span><span class="value">${fmt(ir.min_voltage_raw ?? check.min_voltage_raw, 6)} V</span></div>
          <div class="summary-item"><span class="label">Residual</span><span class="value">${fmt(ir.solver_residual ?? check.solver_residual, 3)}</span></div>
          <div class="summary-item"><span class="label">Mode</span><span class="value"><span class="mode-pill">${escapeHtml(mode)}</span></span></div>
          <div class="summary-item"><span class="label">Source / corner</span><span class="value">${escapeHtml(String(ir.source_type || check.source_type || "—"))} / ${escapeHtml(String(ir.corner || check.corner || "tt"))}</span></div>
          <div class="summary-item"><span class="label">J max / flags</span><span class="value">${fmt(jMax, 3)} mA/µm · ${jViol} viol</span></div>
          <div class="summary-item"><span class="label">Total current</span><span class="value">${fmt(currents.total_a ?? check.total_current_a, 6)} A</span></div>
          <div class="summary-item"><span class="label">Activity α</span><span class="value">${fmt(currents.activity_factor, 3)}</span></div>
          <div class="summary-item"><span class="label">Via edges</span><span class="value">${ir.via_edges ?? check.via_edges ?? grid.via_edges ?? "—"}</span></div>
          <div class="summary-item"><span class="label">Floating nodes</span><span class="value">${floating}</span></div>
          <div class="summary-item"><span class="label">Solver</span><span class="value">${hasError ? "Error" : escapeHtml(typeof (ir.solve_method || check.solve_method) === "object" ? JSON.stringify(ir.solve_method || check.solve_method) : (ir.solve_method || check.solve_method || "OK"))}</span></div>
        </div>
        ${hasError ? `<p class="bad-text">Error: ${escapeHtml(ir.error)}</p>` : ""}
        <div class="summary-grid">
          <div class="summary-item">
            <span class="label rail-vpwr">VPWR</span>
            <span class="value">min ${fmt(vddRail.min_voltage ?? check.min_voltage, 6)} V · drop ${fmt(vddRail.max_drop ?? ir.max_ir_drop, 6)} V</span>
          </div>
          <div class="summary-item">
            <span class="label rail-vgnd">VGND</span>
            <span class="value">bounce ${fmt(vssRail.max_drop ?? ir.max_ground_bounce, 6)} V</span>
          </div>
          ${layers !== "—" ? `<div class="summary-item"><span class="label">Layers</span><span class="value">${escapeHtml(layers)}</span></div>` : ""}
        </div>
        ${renderIrHeatmap(ir, r.meta || {})}
      `;

      let body;
      if (!drops.length) {
        body = `<p class="muted">No instances below the IR voltage threshold in this sample.</p>`;
      } else {
        const rows = drops.map((v, i) => {
          const pct = v.drop_pct !== undefined
            ? Number(v.drop_pct)
            : (vdd > 0 && v.drop_v !== undefined ? 100 * Number(v.drop_v) / vdd : null);
          return `
          <tr>
            <td class="mono">${i + 1}</td>
            <td class="mono">${escapeHtml(v.instance || "—")}</td>
            <td class="mono">${pct == null ? "—" : fmt(pct, 2) + "%"}</td>
            <td class="mono">${v.drop_v !== undefined ? fmt(v.drop_v, 6) + " V" : "—"}</td>
            <td class="mono">${v.voltage !== undefined ? fmt(v.voltage, 6) + " V" : "—"}</td>
            <td class="mono">${escapeHtml(String(v.rail || "—"))}</td>
          </tr>`;
        }).join("");
        body = `<div class="table-wrap"><table>
          <thead><tr><th>#</th><th>Instance</th><th>Drop %</th><th>Drop (V)</th><th>Voltage</th><th>Rail</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
        <p class="muted" style="margin-top:0.5rem">Showing ${drops.length} of ${affected} affected instance(s), sorted by drop %.</p>`;
      }

      return `<section class="panel">
        <div class="panel-head">
          <h3>Power integrity (IR)</h3>
          <span class="muted">Instance local VDD · static DC MNA</span>
        </div>
        <div class="panel-body">
          ${summary}
          ${body}
          <p class="method-note">Drop % = 100·(VDD−V<sub>inst</sub>)/VDD at the follow-pin tap. Count = instances below the configured VDD threshold. Static DC MNA, not signoff; no vector / dynamic IR.</p>
        </div>
      </section>`;
    }

    function renderDetail() {
      const all = allReports();
      const r = all[activeIdx];
      if (!r) return;
      const checks = r.checks || {};
      const meta = r.meta || {};
      const metrics = r.metrics || {};
      const timing = r.timing_s || {};
      const algos = r.algorithms || {};
      const die = Array.isArray(meta.die_area) ? meta.die_area.map(x => fmt(x, 3)).join(", ") : "—";
      el("content").innerHTML = `
        <div class="hero">
          <div>
            <h2>${escapeHtml(r.design || "design")}</h2>
            <div class="meta">${escapeHtml(r._source || r._source_name || "")}</div>
          </div>
          <div class="kv" style="text-align:right">
            <span>Schema: <b>${r.qor_schema ?? 1}</b></span>
            <span>Cells: <b>${metrics.num_cells ?? meta.num_cells ?? "—"}</b></span>
            <span>Nets: <b>${metrics.num_nets ?? meta.num_nets ?? "—"}</b></span>
            <span>HPWL: <b>${fmt(metrics.hpwl_um, 2)} um</b></span>
            <span>Routed WL: <b>${fmt(metrics.routed_wl_um, 2)} um</b></span>
            <span>Die: <b>${die}</b></span>
            <span>Place / CTS / Route: <b>${escapeHtml(algos.placement || "—")} / ${escapeHtml(algos.clock_opt || "—")} / ${escapeHtml(algos.routing || "—")}</b></span>
            <span>Runtime: <b>${fmt(timing.total, 3)} s</b></span>
            <span>Route fallbacks: <b>${metrics.routing_fallback_count ?? (meta.routing_fallbacks || []).length}</b></span>
          </div>
        </div>
        <div class="cards">
          ${checkCard("drc", checks.drc, r)}
          ${checkCard("sta", checks.sta, r)}
          ${checkCard("ir_drop", checks.ir_drop, r)}
        </div>
        ${renderStaPanel(r)}
        ${renderLayoutPanel(r)}
        ${renderDrcPanel(r)}
        ${renderIrPanel(r)}
        <details class="raw">
          <summary>Full report JSON</summary>
          <pre>${escapeHtml(JSON.stringify(r, null, 2))}</pre>
        </details>
        <ul class="notes">
          ${(r.fidelity_notes || []).map(n => `<li>${escapeHtml(n)}</li>`).join("")}
        </ul>
      `;
      bindStaFilters();
    }

    function runLabel(r, i) {
      const algo = (r.algorithms && r.algorithms.placement) ? r.algorithms.placement : "";
      return `${r.design || r._source_name || ("run" + i)}${algo && algo !== "default" ? " [" + algo + "]" : ""}`;
    }

    function compareRows() {
      return allReports().map((r, i) => {
        const m = r.metrics || {};
        const t = r.timing_s || {};
        const c = r.checks || {};
        return {
          idx: i,
          design: r.design || r._source_name || ("run" + i),
          placement: (r.algorithms || {}).placement || "—",
          wns_ps: (c.sta || {}).setup_wns_ps ?? (c.sta || {}).wns_ps,
          hold_wns_ps: (c.sta || {}).hold_wns_ps,
          ir_instances: (c.ir_drop || {}).instances_affected ?? (c.ir_drop || {}).violations,
          max_ir_drop: (c.ir_drop || {}).max_ir_drop,
          drc_violations: (c.drc || {}).violations,
          hpwl_um: m.hpwl_um,
          routed_wl_um: m.routed_wl_um,
          t_total_s: t.total,
          t_placement_s: t.placement || 0,
          t_clock_opt_s: t.clock_opt || 0,
          t_routing_s: t.routing || 0,
          t_checkers_s: (t.drc || 0) + (t.sta || 0) + (t.ir_drop || 0),
          label: runLabel(r, i),
        };
      });
    }

    function renderCompare() {
      let rows = compareRows();
      const key = compareSort.key;
      const dir = compareSort.dir;
      rows = rows.slice().sort((a, b) => {
        const av = a[key], bv = b[key];
        if (av === bv) return 0;
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        if (typeof av === "string") return dir * av.localeCompare(bv);
        return dir * (av < bv ? -1 : 1);
      });

      const th = (k, label) =>
        `<th data-sort="${k}">${label}${compareSort.key === k ? (compareSort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;

      const tableRows = rows.map(r => `
        <tr data-idx="${r.idx}" style="cursor:pointer">
          <td>${escapeHtml(r.design)}</td>
          <td class="mono">${escapeHtml(r.placement)}</td>
          <td class="mono">${fmt(r.wns_ps, 2)}</td>
          <td class="mono">${fmt(r.hold_wns_ps, 2)}</td>
          <td class="mono">${r.ir_instances ?? "—"}</td>
          <td class="mono">${fmt(r.max_ir_drop, 6)}</td>
          <td class="mono">${r.drc_violations ?? "—"}</td>
          <td class="mono">${fmt(r.hpwl_um, 2)}</td>
          <td class="mono">${fmt(r.routed_wl_um, 2)}</td>
          <td class="mono">${fmt(r.t_total_s, 3)}</td>
        </tr>`).join("");

      el("content").innerHTML = `
        <section class="panel">
          <div class="panel-head"><h3>Compare</h3><span class="muted">Click a column to sort · click a row to open QoR detail</span></div>
          <div class="panel-body">
            <div class="table-wrap"><table id="compareTable">
              <thead><tr>
                ${th("design", "Design")}
                ${th("placement", "Placement")}
                ${th("wns_ps", "Setup WNS")}
                ${th("hold_wns_ps", "Hold WNS")}
                ${th("ir_instances", "IR instances")}
                ${th("max_ir_drop", "Max IR (V)")}
                ${th("drc_violations", "DRC")}
                ${th("hpwl_um", "HPWL")}
                ${th("routed_wl_um", "Routed WL")}
                ${th("t_total_s", "Runtime (s)")}
              </tr></thead>
              <tbody>${tableRows || `<tr><td colspan="10" class="muted">No runs</td></tr>`}</tbody>
            </table></div>
            <div class="charts">
              <div class="chart-box"><h4>WL vs Setup WNS</h4><canvas id="chart-wl-wns" width="400" height="220"></canvas></div>
              <div class="chart-box"><h4>WL vs Max IR drop</h4><canvas id="chart-wl-ir" width="400" height="220"></canvas></div>
              <div class="chart-box"><h4>Runtime stacked (s)</h4><canvas id="chart-runtime" width="400" height="220"></canvas></div>
              <div class="chart-box"><h4>DRC violations by run</h4><canvas id="chart-drc" width="400" height="220"></canvas></div>
            </div>
          </div>
        </section>`;

      el("compareTable").querySelectorAll("th[data-sort]").forEach(thEl => {
        thEl.addEventListener("click", () => {
          const k = thEl.getAttribute("data-sort");
          if (compareSort.key === k) compareSort.dir *= -1;
          else { compareSort.key = k; compareSort.dir = 1; }
          renderCompare();
        });
      });
      el("compareTable").querySelectorAll("tbody tr[data-idx]").forEach(tr => {
        tr.addEventListener("click", () => {
          activeIdx = Number(tr.getAttribute("data-idx"));
          setTab("detail");
          renderList();
        });
      });

      drawScatter(el("chart-wl-wns"), rows, "wns_ps");
      drawScatter(el("chart-wl-ir"), rows, "max_ir_drop");
      drawStackedRuntime(el("chart-runtime"), rows);
      drawBar(el("chart-drc"), rows.map(r => r.label), rows.map(r => Number(r.drc_violations || 0)), "violations");
    }

    function cssVar(name, fallback) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    }

    function drawScatter(canvas, rows, yKey) {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const W = canvas.width, H = canvas.height;
      ctx.clearRect(0, 0, W, H);
      const pad = 36;
      const yField = yKey || "wns_ps";
      const xs = rows.map(r => Number(r.routed_wl_um ?? r.hpwl_um ?? 0));
      const ys = rows.map(r => Number(r[yField] ?? 0));
      if (!rows.length) return;
      const xmin = Math.min(...xs), xmax = Math.max(...xs);
      const ymin = Math.min(...ys), ymax = Math.max(...ys);
      const dx = (xmax - xmin) || 1, dy = (ymax - ymin) || 1;
      const accent = cssVar("--accent", "#5ec8d8");
      const muted = cssVar("--muted", "#94a3b8");
      const text = cssVar("--text", "#e7eef7");
      ctx.strokeStyle = muted; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad, H - pad); ctx.lineTo(W - 10, H - pad); ctx.lineTo(W - 10, H - pad); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(pad, H - pad); ctx.lineTo(pad, 10); ctx.stroke();
      ctx.fillStyle = muted; ctx.font = "11px sans-serif";
      ctx.fillText("WL (um)", W / 2 - 20, H - 8);
      ctx.save(); ctx.translate(12, H / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("WNS (ps)", 0, 0); ctx.restore();
      rows.forEach((r, i) => {
        const x = pad + ((xs[i] - xmin) / dx) * (W - pad - 16);
        const y = (H - pad) - ((ys[i] - ymin) / dy) * (H - pad - 16);
        ctx.fillStyle = accent;
        ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = text; ctx.font = "10px sans-serif";
        ctx.fillText(String(i + 1), x + 6, y - 4);
      });
      ctx.strokeStyle = accent;
    }

    function drawStackedRuntime(canvas, rows) {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const W = canvas.width, H = canvas.height;
      ctx.clearRect(0, 0, W, H);
      const pad = 36;
      const n = rows.length;
      if (!n) return;
      const totals = rows.map(r => (r.t_placement_s || 0) + (r.t_clock_opt_s || 0) + (r.t_routing_s || 0) + (r.t_checkers_s || 0));
      const maxT = Math.max(...totals, 1e-9);
      const barW = Math.min(40, (W - pad - 20) / n * 0.7);
      const colors = [cssVar("--accent", "#5ec8d8"), "#e0674f", "#7fc97f", cssVar("--warn", "#d4a24c")];
      const muted = cssVar("--muted", "#94a3b8");
      ctx.strokeStyle = muted; ctx.beginPath(); ctx.moveTo(pad, H - pad); ctx.lineTo(W - 10, H - pad); ctx.stroke();
      rows.forEach((r, i) => {
        const x = pad + (i + 0.5) * ((W - pad - 20) / n) - barW / 2;
        let y = H - pad;
        const parts = [r.t_placement_s || 0, r.t_clock_opt_s || 0, r.t_routing_s || 0, r.t_checkers_s || 0];
        parts.forEach((v, pi) => {
          const h = (v / maxT) * (H - pad - 16);
          y -= h;
          ctx.fillStyle = colors[pi];
          ctx.fillRect(x, y, barW, h);
        });
        ctx.fillStyle = muted; ctx.font = "10px sans-serif";
        ctx.fillText(String(i + 1), x + barW / 2 - 3, H - pad + 12);
      });
      ctx.fillStyle = muted; ctx.font = "10px sans-serif";
      ctx.fillText("place / cts / route / checkers", pad, 14);
    }

    function drawBar(canvas, labels, values, ylabel) {
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const W = canvas.width, H = canvas.height;
      ctx.clearRect(0, 0, W, H);
      const pad = 36;
      const n = values.length;
      if (!n) return;
      const maxV = Math.max(...values, 1);
      const barW = Math.min(40, (W - pad - 20) / n * 0.7);
      const fail = cssVar("--fail", "#e35d6a");
      const accent = cssVar("--accent", "#5ec8d8");
      const muted = cssVar("--muted", "#94a3b8");
      ctx.strokeStyle = muted; ctx.beginPath(); ctx.moveTo(pad, H - pad); ctx.lineTo(W - 10, H - pad); ctx.stroke();
      values.forEach((v, i) => {
        const x = pad + (i + 0.5) * ((W - pad - 20) / n) - barW / 2;
        const h = (v / maxV) * (H - pad - 16);
        ctx.fillStyle = v > 0 ? fail : accent;
        ctx.fillRect(x, H - pad - h, barW, h);
        ctx.fillStyle = muted; ctx.font = "10px sans-serif";
        ctx.fillText(String(i + 1), x + barW / 2 - 3, H - pad + 12);
      });
      ctx.fillStyle = muted; ctx.font = "10px sans-serif";
      ctx.fillText(ylabel || "", pad, 14);
    }

    function reportHasLayout(r) {
      if (!r) return false;
      const views = r._layout_views || {};
      return !!(r._layout_view || Object.keys(views).length);
    }

    function isViewerFullscreen() {
      const shell = el("layoutViewerShell");
      return !!(document.fullscreenElement && shell && document.fullscreenElement === shell);
    }

    async function toggleViewerFullscreen() {
      const shell = el("layoutViewerShell");
      if (!shell) return;
      try {
        if (isViewerFullscreen()) await document.exitFullscreen();
        else if (shell.requestFullscreen) await shell.requestFullscreen();
      } catch (_) {}
      syncFullscreenButton();
      resizeViewerCanvas();
      drawViewer();
    }

    function syncFullscreenButton() {
      const btn = el("viewerFullscreen");
      if (!btn) return;
      btn.textContent = isViewerFullscreen() ? "Exit fullscreen" : "Fullscreen";
    }

    function resizeViewerCanvas() {
      const canvas = el("layoutCanvas");
      const wrap = canvas && canvas.parentElement;
      if (!canvas || !wrap) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = wrap.getBoundingClientRect();
      const cssW = Math.max(320, Math.floor(rect.width));
      const fallbackH = isViewerFullscreen()
        ? Math.min(window.innerHeight * 0.85, 900)
        : Math.min(Math.max(window.innerHeight * 0.52, 420), 640);
      const cssH = Math.max(320, Math.floor(rect.height || fallbackH));
      viewerState.dpr = dpr;
      viewerState.cssW = cssW;
      viewerState.cssH = cssH;
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
    }

    function canvasToWorld(cssX, cssY) {
      // cssX/cssY in CSS pixels; ox/oy/scale are in device pixels
      const cx = cssX * viewerState.dpr;
      const cy = cssY * viewerState.dpr;
      const x = (cx - viewerState.ox) / viewerState.scale;
      const y = (viewerState.oy - cy) / viewerState.scale;
      return [x, y];
    }

    function eventToCanvasCss(ev, canvas) {
      const rect = canvas.getBoundingClientRect();
      return [ev.clientX - rect.left, ev.clientY - rect.top];
    }

    function pickCellAt(wx, wy) {
      const data = viewerState.data;
      if (!data || !data.cells) return null;
      // Topmost last in list; scan reverse
      for (let i = data.cells.length - 1; i >= 0; i--) {
        const c = data.cells[i];
        const isBuf = !!c.is_buffer;
        if (isBuf && !viewerState.showBuffers) continue;
        if (!isBuf && !viewerState.showCells) continue;
        if (wx >= c.x && wx <= c.x + c.w && wy >= c.y && wy <= c.y + c.h) return c;
      }
      return null;
    }

    function updateHud() {
      const hud = el("edaHud");
      if (!hud) return;
      const fit = viewerState._fitScale || (viewerState.scale / Math.max(viewerState.dpr, 1e-6));
      const zoom = Math.round(((viewerState.scale / Math.max(viewerState.dpr, 1e-6)) / Math.max(fit, 1e-9)) * 100);
      const cur = viewerState.cursorUm;
      const curTxt = cur ? `X <b>${cur[0].toFixed(3)}</b>  Y <b>${cur[1].toFixed(3)}</b> µm` : "X <b>—</b>  Y <b>—</b> µm";
      const hc = viewerState.hoverCell;
      const sel = hc
        ? `<span class="sel">SEL ${escapeHtml(hc.name)} · ${escapeHtml(hc.kind || "?")} x${hc.drive_strength ?? "?"}</span>`
        : `<span>SEL <b>—</b></span>`;
      const nCells = (viewerState.data && viewerState.data.cells) ? viewerState.data.cells.length : 0;
      const nSegs = (viewerState.data && viewerState.data.segments) ? viewerState.data.segments.length : 0;
      hud.innerHTML = `
        <span>${escapeHtml(viewerState.designName || "—")} · ${escapeHtml(viewerState.stage)}</span>
        <span>ZOOM <b>${zoom}%</b></span>
        <span>${curTxt}</span>
        ${sel}
        <span>CELLS <b>${nCells}</b> · SEGS <b>${nSegs}</b></span>`;
      const tip = el("edaTooltip");
      if (tip) {
        if (hc) {
          const strength = hc.drive_strength != null ? String(hc.drive_strength) : "—";
          const ctype = hc.cell_type || hc.family || "—";
          tip.innerHTML = `
            <div class="tt-title">${escapeHtml(hc.name)}</div>
            <div class="tt-row">Kind <b>${escapeHtml(hc.kind || "unknown")}</b></div>
            <div class="tt-row">Family <b>${escapeHtml(hc.family || "—")}</b></div>
            <div class="tt-row">Drive strength <b>${escapeHtml(strength)}</b></div>
            <div class="tt-row">Cell type <b>${escapeHtml(ctype)}</b></div>
            <div class="tt-row">Size <b>${Number(hc.w).toFixed(3)} × ${Number(hc.h).toFixed(3)}</b> µm</div>
            ${hc.is_buffer ? '<div class="tt-row"><b>CTS buffer</b></div>' : ""}
          `;
          tip.classList.add("visible");
        } else {
          tip.classList.remove("visible");
        }
      }
    }

    async function renderViewer() {
      const all = allReports();
      if (!reportHasLayout(all[activeIdx])) {
        const idx = all.findIndex(reportHasLayout);
        if (idx >= 0) {
          activeIdx = idx;
          renderList();
        }
      }
      const r = all[activeIdx];
      if (!r) return;
      viewerState.designName = r.design || r._source_name || "design";
      const views = r._layout_views || {};
      const stages = LAYOUT_STAGES.filter(([k]) => views[k] || (k === "routing" && r._layout_view));
      const stageOptions = (stages.length ? stages : LAYOUT_STAGES).map(([k, label]) =>
        `<option value="${k}" ${k === viewerState.stage ? "selected" : ""}>${label}</option>`
      ).join("");

      const layerChips = Object.entries(LAYER_COLORS).map(([L, col]) => {
        const on = viewerState.layers[L] !== false;
        return `<button type="button" class="layer-chip ${on ? "on" : "off"}" data-layer="${L}" style="--chip-c:${col}" title="Toggle ${L}">
          <span class="swatch" style="background:${col}"></span>${L}</button>`;
      }).join("");

      el("content").innerHTML = `
        <section class="layout-viewer" id="layoutViewerShell">
          <div class="eda-titlebar">
            <span class="brand">PnR Layout · Interactive</span>
            <span class="readout" id="viewerReadout">—</span>
          </div>
          <div class="eda-toolbar">
            <div class="group">
              <label class="field">Stage
                <select id="viewerStage">${stageOptions}</select>
              </label>
            </div>
            <div class="group" id="layerChips">${layerChips}</div>
            <div class="group">
              <button type="button" class="obj-tog ${viewerState.showCells ? "on" : ""}" id="togCells">Cells</button>
              <button type="button" class="obj-tog ${viewerState.showBuffers ? "on" : ""}" id="togBuffers">Buffers</button>
              <button type="button" class="obj-tog ${viewerState.showPorts ? "on" : ""}" id="togPorts">I/O</button>
              <button type="button" class="obj-tog ${viewerState.showPins ? "on" : ""}" id="togPins">Pins</button>
              <button type="button" class="obj-tog ${viewerState.showPinNames ? "on" : ""}" id="togPinNames">Pin names</button>
              <button type="button" class="obj-tog ${viewerState.showPowerPins ? "on" : ""}" id="togPowerPins">Cell PWR pins</button>
              <button type="button" class="obj-tog ${viewerState.showVdd ? "on" : ""}" id="togVdd" style="color:#e35d6a">VDD</button>
              <button type="button" class="obj-tog ${viewerState.showVss ? "on" : ""}" id="togVss" style="color:#5b8def">VSS</button>
              <button type="button" class="obj-tog ${viewerState.showGrid ? "on" : ""}" id="togGrid">Grid</button>
            </div>
            <div class="group">
              <label class="field">Metal <input type="range" id="metalOpacity" min="0.2" max="1" step="0.05" value="${viewerState.metalOpacity}"/></label>
              <label class="field">Cell <input type="range" id="cellOpacity" min="0.15" max="1" step="0.05" value="${viewerState.cellOpacity}"/></label>
            </div>
            <div class="group">
              <button class="eda-btn" type="button" id="viewerFit">Fit</button>
              <button class="eda-btn primary" type="button" id="viewerFullscreen">Fullscreen</button>
            </div>
          </div>
          <div class="eda-canvas-wrap" id="edaCanvasWrap">
            <canvas id="layoutCanvas" width="900" height="360"></canvas>
            <div class="eda-tooltip" id="edaTooltip"></div>
            <div class="eda-hud" id="edaHud"></div>
          </div>
          <p class="viewer-note" id="viewerNote">Wheel zoom · drag pan · hover cell for type/strength · Pins + names · F fullscreen · 0/R fit · G grid</p>
        </section>
        <div class="png-fallback" id="pngFallback">${renderLayoutPanel(r)}</div>
      `;

      el("viewerStage").addEventListener("change", async (ev) => {
        viewerState.stage = ev.target.value;
        await loadViewerData(r);
        fitViewer();
        drawViewer();
      });

      const bindObj = (id, key) => {
        el(id).addEventListener("click", () => {
          viewerState[key] = !viewerState[key];
          el(id).classList.toggle("on", viewerState[key]);
          drawViewer();
        });
      };
      bindObj("togCells", "showCells");
      bindObj("togBuffers", "showBuffers");
      bindObj("togPorts", "showPorts");
      bindObj("togPins", "showPins");
      bindObj("togPinNames", "showPinNames");
      bindObj("togPowerPins", "showPowerPins");
      bindObj("togVdd", "showVdd");
      bindObj("togVss", "showVss");
      bindObj("togGrid", "showGrid");

      document.querySelectorAll(".layer-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          const L = btn.getAttribute("data-layer");
          viewerState.layers[L] = !viewerState.layers[L];
          btn.classList.toggle("on", viewerState.layers[L]);
          btn.classList.toggle("off", !viewerState.layers[L]);
          drawViewer();
        });
      });

      el("metalOpacity").addEventListener("input", (ev) => {
        viewerState.metalOpacity = parseFloat(ev.target.value);
        drawViewer();
      });
      el("cellOpacity").addEventListener("input", (ev) => {
        viewerState.cellOpacity = parseFloat(ev.target.value);
        drawViewer();
      });
      el("viewerFit").addEventListener("click", () => { fitViewer(); drawViewer(); });
      el("viewerFullscreen").addEventListener("click", () => toggleViewerFullscreen());

      const canvas = el("layoutCanvas");
      canvas.addEventListener("wheel", (ev) => {
        ev.preventDefault();
        const [mx, my] = eventToCanvasCss(ev, canvas);
        const dpr = viewerState.dpr;
        const cx = mx * dpr, cy = my * dpr;
        const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
        const wx = (cx - viewerState.ox) / viewerState.scale;
        const wy = (viewerState.oy - cy) / viewerState.scale;
        viewerState.scale *= factor;
        viewerState.ox = cx - wx * viewerState.scale;
        viewerState.oy = cy + wy * viewerState.scale;
        drawViewer();
      }, { passive: false });

      canvas.addEventListener("mousedown", (ev) => {
        if (ev.button !== 0) return;
        viewerState.dragging = true;
        viewerState.lastX = ev.clientX;
        viewerState.lastY = ev.clientY;
        canvas.classList.add("panning");
      });
      window.addEventListener("mouseup", () => {
        viewerState.dragging = false;
        canvas.classList.remove("panning");
      });
      canvas.addEventListener("mousemove", (ev) => {
        const [mx, my] = eventToCanvasCss(ev, canvas);
        const [wx, wy] = canvasToWorld(mx, my);
        viewerState.cursorUm = [wx, wy];
        viewerState.hoverCell = pickCellAt(wx, wy);
        if (viewerState.dragging) {
          const dpr = viewerState.dpr;
          viewerState.ox += (ev.clientX - viewerState.lastX) * dpr;
          viewerState.oy += (ev.clientY - viewerState.lastY) * dpr;
          viewerState.lastX = ev.clientX;
          viewerState.lastY = ev.clientY;
        }
        drawViewer();
      });
      canvas.addEventListener("mouseleave", () => {
        viewerState.cursorUm = null;
        viewerState.hoverCell = null;
        updateHud();
      });
      canvas.addEventListener("dblclick", (ev) => {
        ev.preventDefault();
        fitViewer();
        drawViewer();
      });

      if (viewerKeyHandler) window.removeEventListener("keydown", viewerKeyHandler);
      viewerKeyHandler = (ev) => {
        if (activeTab !== "viewer") return;
        const tag = (ev.target && ev.target.tagName) || "";
        if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
        if (ev.key === "f" || ev.key === "F") { ev.preventDefault(); toggleViewerFullscreen(); }
        else if (ev.key === "0" || ev.key === "r" || ev.key === "R") { fitViewer(); drawViewer(); }
        else if (ev.key === "g" || ev.key === "G") {
          viewerState.showGrid = !viewerState.showGrid;
          const g = el("togGrid");
          if (g) g.classList.toggle("on", viewerState.showGrid);
          drawViewer();
        }
      };
      window.addEventListener("keydown", viewerKeyHandler);

      document.addEventListener("fullscreenchange", () => {
        syncFullscreenButton();
        resizeViewerCanvas();
        fitViewer();
        drawViewer();
      });

      if (viewerResizeObs) viewerResizeObs.disconnect();
      const wrap = el("edaCanvasWrap");
      if (wrap && window.ResizeObserver) {
        viewerResizeObs = new ResizeObserver(() => {
          resizeViewerCanvas();
          drawViewer();
        });
        viewerResizeObs.observe(wrap);
      }

      resizeViewerCanvas();
      await loadViewerData(r);
      fitViewer();
      drawViewer();
      syncFullscreenButton();
    }

    async function loadViewerData(r) {
      const note = el("viewerNote");
      const embedded = (r && r._layout_embedded) || {};
      const stage = viewerState.stage;
      if (embedded[stage]) {
        viewerState.data = embedded[stage];
        if (note) {
          note.textContent = `Embedded ${stage} layout · metals above cells · F fullscreen · 0/R fit · G grid`;
        }
        updateHud();
        return;
      }
      // Fallback: routing embed when stage missing
      if (embedded.routing && stage === "routing") {
        viewerState.data = embedded.routing;
        if (note) note.textContent = "Embedded routing layout · F fullscreen · 0/R fit · G grid";
        updateHud();
        return;
      }

      const views = r._layout_views || {};
      let url = views[stage] || (stage === "routing" ? r._layout_view : null);
      if (!url && r._layout_view) url = r._layout_view;
      if (!url) {
        viewerState.data = null;
        if (note) {
          note.textContent = "No layout_view.json for this run (re-run the design, or pick a run that has layout data).";
        }
        updateHud();
        return;
      }
      try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        viewerState.data = await resp.json();
        if (note) note.textContent = `Loaded ${url} · metals above cells · F fullscreen · 0/R fit · G grid`;
      } catch (err) {
        viewerState.data = null;
        const isFile = location.protocol === "file:";
        if (note) {
          note.textContent = isFile
            ? `Cannot load ${url} from file://. Re-generate the HTML report (layout is embedded), or serve runs/: python -m http.server -d runs`
            : `Failed to fetch ${url} (${err}). Serve the runs folder over HTTP.`;
        }
      }
      updateHud();
    }

    function fitViewer() {
      const data = viewerState.data;
      const canvas = el("layoutCanvas");
      if (!data || !canvas) return;
      resizeViewerCanvas();
      const die = data.die_area || [0, 0, 100, 100];
      const minx = die[0], miny = die[1], maxx = die[2], maxy = die[3];
      const spanX = Math.max(maxx - minx, 1e-6);
      const spanY = Math.max(maxy - miny, 1e-6);
      const pad = 28 * viewerState.dpr;
      const W = canvas.width, H = canvas.height;
      const sx = (W - 2 * pad) / spanX;
      const sy = (H - 2 * pad) / spanY;
      viewerState.scale = Math.min(sx, sy);
      viewerState._fitScale = viewerState.scale / viewerState.dpr;
      // Center die in the canvas (wide panels left a tiny left-aligned die before).
      const drawW = spanX * viewerState.scale;
      const drawH = spanY * viewerState.scale;
      viewerState.ox = (W - drawW) / 2 - minx * viewerState.scale;
      viewerState.oy = (H + drawH) / 2 + miny * viewerState.scale;
    }

    function worldToCanvas(x, y) {
      return [
        viewerState.ox + x * viewerState.scale,
        viewerState.oy - y * viewerState.scale,
      ];
    }

    function drawViewer() {
      const canvas = el("layoutCanvas");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const W = canvas.width, H = canvas.height;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#05080c";
      ctx.fillRect(0, 0, W, H);
      const data = viewerState.data;
      const readout = el("viewerReadout");
      if (!data) {
        if (readout) readout.textContent = "No layout data";
        updateHud();
        return;
      }
      const die = data.die_area || [0, 0, 100, 100];

      // Grid (under everything)
      if (viewerState.showGrid) {
        const span = Math.max(die[2] - die[0], die[3] - die[1]);
        let step = 10;
        if (span < 30) step = 2;
        else if (span < 80) step = 5;
        else if (span > 200) step = 20;
        ctx.lineWidth = Math.max(1, viewerState.dpr * 0.6);
        for (let x = Math.ceil(die[0] / step) * step; x <= die[2]; x += step) {
          const major = Math.abs(x / step) % 5 < 1e-9;
          ctx.strokeStyle = major ? "rgba(70,90,110,0.45)" : "rgba(40,55,70,0.35)";
          const [a, b] = [worldToCanvas(x, die[1]), worldToCanvas(x, die[3])];
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
        for (let y = Math.ceil(die[1] / step) * step; y <= die[3]; y += step) {
          const major = Math.abs(y / step) % 5 < 1e-9;
          ctx.strokeStyle = major ? "rgba(70,90,110,0.45)" : "rgba(40,55,70,0.35)";
          const [a, b] = [worldToCanvas(die[0], y), worldToCanvas(die[2], y)];
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
      }

      // Die outline
      const [x0, y0] = worldToCanvas(die[0], die[1]);
      const [x1, y1] = worldToCanvas(die[2], die[3]);
      ctx.strokeStyle = "#9eb4c8";
      ctx.lineWidth = 1.5 * viewerState.dpr;
      ctx.strokeRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));

      // Cells / buffers (under metals)
      let cellCount = 0;
      const hoverName = viewerState.hoverCell ? viewerState.hoverCell.name : null;
      ctx.globalAlpha = viewerState.cellOpacity;
      (data.cells || []).forEach(c => {
        const isBuf = !!c.is_buffer;
        if (isBuf && !viewerState.showBuffers) return;
        if (!isBuf && !viewerState.showCells) return;
        cellCount += 1;
        const p0 = worldToCanvas(c.x, c.y);
        const p1 = worldToCanvas(c.x + c.w, c.y + c.h);
        const x = Math.min(p0[0], p1[0]), y = Math.min(p0[1], p1[1]);
        const w = Math.abs(p1[0] - p0[0]), h = Math.abs(p1[1] - p0[1]);
        const hovered = hoverName && c.name === hoverName;
        ctx.fillStyle = hovered ? (isBuf ? "#ffc45c" : "#5a7fa0") : (isBuf ? "#e8a33d" : "#3d5168");
        ctx.strokeStyle = hovered ? "#ffe08a" : (isBuf ? "#8a5c10" : "#1c2836");
        ctx.lineWidth = (hovered ? 1.4 : 0.5) * viewerState.dpr;
        ctx.fillRect(x, y, w, h);
        ctx.strokeRect(x, y, w, h);
      });
      ctx.globalAlpha = 1;

      // Chip I/O ports on die boundary
      if (viewerState.showPorts) {
        ctx.fillStyle = "#f0f4f8";
        (data.ports || []).forEach(p => {
          const [px, py] = worldToCanvas(p.x, p.y);
          const s = 3 * viewerState.dpr;
          ctx.fillRect(px - s, py - s, s * 2, s * 2);
        });
      }

      // Power straps (under signal metals)
      (data.power_segments || []).forEach(seg => {
        const net = seg.net || "VPWR";
        if (net === "VPWR" && !viewerState.showVdd) return;
        if (net === "VGND" && !viewerState.showVss) return;
        const a = worldToCanvas(seg.x1, seg.y1);
        const b = worldToCanvas(seg.x2, seg.y2);
        ctx.strokeStyle = POWER_COLORS[net] || "#aaaaaa";
        ctx.lineWidth = 2.4 * viewerState.dpr;
        ctx.globalAlpha = 0.9;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        ctx.globalAlpha = 1;
      });

      // Signal metals ABOVE cells / power
      (data.segments || []).forEach(seg => {
        const layer = seg.layer || "met1";
        if (viewerState.layers[layer] === false) return;
        const a = worldToCanvas(seg.x1, seg.y1);
        const b = worldToCanvas(seg.x2, seg.y2);
        ctx.strokeStyle = LAYER_COLORS[layer] || "#9aa5b1";
        ctx.lineWidth = 1.35 * viewerState.dpr;
        ctx.globalAlpha = viewerState.metalOpacity;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        ctx.globalAlpha = 1;
      });

      // Cell pins + names (above metals so they stay readable)
      if (viewerState.showPins) {
        const umPerPx = viewerState.dpr / Math.max(viewerState.scale, 1e-9);
        const showNames = viewerState.showPinNames && umPerPx < 0.35;
        const fontPx = Math.max(8, Math.min(12, 11 * viewerState.dpr));
        ctx.font = `${fontPx}px IBM Plex Mono, Consolas, monospace`;
        ctx.textBaseline = "bottom";
        (data.cells || []).forEach(c => {
          const isBuf = !!c.is_buffer;
          if (isBuf && !viewerState.showBuffers) return;
          if (!isBuf && !viewerState.showCells) return;
          const pins = c.pins || [];
          const forcePins = hoverName && c.name === hoverName;
          pins.forEach(pin => {
            if (pin.is_power && !viewerState.showPowerPins && !forcePins) return;
            const [px, py] = worldToCanvas(pin.x, pin.y);
            const dir = (pin.direction || "input").toLowerCase();
            const col = pin.is_clock ? "#e8a33d" : (dir === "output" ? "#7fc97f" : (pin.is_power ? "#e0674f" : "#5ec8d8"));
            const r = (forcePins ? 3.2 : 2.2) * viewerState.dpr;
            ctx.fillStyle = col;
            ctx.beginPath();
            ctx.arc(px, py, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = "#0a1018";
            ctx.lineWidth = 0.8 * viewerState.dpr;
            ctx.stroke();
            if (showNames || forcePins) {
              ctx.fillStyle = "#f0f4f8";
              ctx.fillText(pin.name, px + 3 * viewerState.dpr, py - 2 * viewerState.dpr);
            }
          });
        });
      }

      // Hover halo on top
      if (viewerState.hoverCell) {
        const c = viewerState.hoverCell;
        const p0 = worldToCanvas(c.x, c.y);
        const p1 = worldToCanvas(c.x + c.w, c.y + c.h);
        const x = Math.min(p0[0], p1[0]), y = Math.min(p0[1], p1[1]);
        const w = Math.abs(p1[0] - p0[0]), h = Math.abs(p1[1] - p0[1]);
        ctx.strokeStyle = "#ffe08a";
        ctx.lineWidth = 2.2 * viewerState.dpr;
        ctx.strokeRect(x - 2, y - 2, w + 4, h + 4);
        ctx.fillStyle = "rgba(255, 224, 138, 0.12)";
        ctx.fillRect(x, y, w, h);
      }

      const layersOn = Object.entries(viewerState.layers).filter(([, on]) => on).map(([k]) => k);
      if (readout) {
        readout.textContent = `${cellCount} cells · ${(data.segments || []).length} segs · ${layersOn.join(",") || "no layers"} · pins ${viewerState.showPins ? "on" : "off"}`;
      }
      updateHud();
    }

    el("fileInput").addEventListener("change", async (ev) => {
      const files = Array.from(ev.target.files || []);
      for (const file of files) {
        try {
          const text = await file.text();
          const data = JSON.parse(text);
          data._source_name = file.name;
          data._source = file.name;
          extras.push(data);
        } catch (err) {
          alert(`Failed to load ${file.name}: ${err}`);
        }
      }
      activeIdx = Math.max(0, allReports().length - 1);
      renderList();
      ev.target.value = "";
    });

    el("clearExtra").addEventListener("click", () => {
      extras = [];
      activeIdx = 0;
      renderList();
    });

    el("themeToggle").addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      applyTheme(current === "dark" ? "light" : "dark");
    });

    document.querySelectorAll(".tab").forEach(btn => {
      btn.addEventListener("click", () => setTab(btn.dataset.tab));
    });

    initTheme();
    initScoreboardLink();
    renderList();
  </script>
</body>
</html>
"""
