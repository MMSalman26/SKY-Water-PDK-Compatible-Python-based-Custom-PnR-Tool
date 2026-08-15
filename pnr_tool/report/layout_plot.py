"""Headless (Agg) per-stage layout PNG rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from pnr_tool.design.object import DesignObject

STAGE_FILES: Dict[str, str] = {
    "power": "layout_power.png",
    "placement": "layout_placement.png",
    "cts": "layout_cts.png",
    "routing": "layout_routing.png",
}

_STAGE_TITLES = {
    "power": "Power Plan",
    "placement": "Placement",
    "cts": "Clock Opt (CTS)",
    "routing": "Routing",
}
_VDD_COLOR = "#e35d6a"
_VSS_COLOR = "#5b8def"

_CELL_FACE = "#4a5b73"
_CELL_EDGE = "#22303f"
_BUFFER_FACE = "#e8a33d"
_BUFFER_EDGE = "#8a5c10"
_TAP_FACE = "#3d9e8a"
_TAP_EDGE = "#1f5c50"
_DECAP_FACE = "#6b7fd7"
_DECAP_EDGE = "#343f7a"
_DIE_EDGE = "#c8d3e0"
_LAYER_COLORS = (
    ("met1", "#5ec8d8"),
    ("met2", "#e0674f"),
    ("met3", "#7fc97f"),
    ("met4", "#c286d8"),
    ("met5", "#e8d44d"),
    ("li1", "#9aa5b1"),
)


def layout_image_paths(out_dir: Path) -> Dict[str, Path]:
    out_dir = Path(out_dir)
    return {stage: out_dir / name for stage, name in STAGE_FILES.items()}


def write_stage_layout(
    design: DesignObject,
    stage: str,
    out_dir: Path,
    config: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """Render the current layout for ``stage`` into ``out_dir``.

    Returns the written path, or ``None`` when image output is disabled.
    """
    report_cfg = dict((config or {}).get("report", {}))
    if not report_cfg.get("layout_images", True):
        return None
    if stage not in STAGE_FILES:
        raise ValueError(f"Unknown layout stage: {stage}")

    out_path = Path(out_dir) / STAGE_FILES[stage]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dpi = min(float(report_cfg.get("layout_dpi", 110)), 200.0)
    size_in = float(report_cfg.get("layout_size_in", 8.0))
    max_segments = int(report_cfg.get("layout_max_segments", 200000))

    minx, miny, maxx, maxy = (float(v) for v in design.die_area)
    span_x = max(maxx - minx, 1e-6)
    span_y = max(maxy - miny, 1e-6)
    aspect = span_y / span_x

    fig = Figure(figsize=(size_in, max(2.5, min(size_in * aspect, size_in * 1.6))), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    buffers = (
        set(design.clock_tree.get("new_buffers", {}))
        if stage not in ("placement", "power")
        else set()
    )
    cell_polys, buffer_polys, tap_polys, decap_polys = _cell_polygons(design, buffers)
    # Z-order: die → cells/buffers → ports → metals on top (matches Layout viewer).
    if cell_polys:
        ax.add_collection(
            PolyCollection(
                cell_polys,
                facecolors=_CELL_FACE,
                edgecolors=_CELL_EDGE,
                linewidths=0.15 if len(cell_polys) > 500 else 0.4,
                zorder=2,
            )
        )
    if tap_polys:
        ax.add_collection(
            PolyCollection(
                tap_polys,
                facecolors=_TAP_FACE,
                edgecolors=_TAP_EDGE,
                linewidths=0.35,
                zorder=2.5,
            )
        )
    if decap_polys:
        ax.add_collection(
            PolyCollection(
                decap_polys,
                facecolors=_DECAP_FACE,
                edgecolors=_DECAP_EDGE,
                linewidths=0.3,
                zorder=2.5,
            )
        )
    if buffer_polys:
        ax.add_collection(
            PolyCollection(
                buffer_polys,
                facecolors=_BUFFER_FACE,
                edgecolors=_BUFFER_EDGE,
                linewidths=0.6,
                zorder=3,
            )
        )

    ports = design.meta.get("port_positions") or {}
    legend_handles: List[Line2D] = []
    if ports:
        ax.scatter(
            [float(p[0]) for p in ports.values()],
            [float(p[1]) for p in ports.values()],
            s=8,
            c="#f0f4f8",
            marker="s",
            linewidths=0,
            zorder=4,
        )
        legend_handles.append(
            Line2D(
                [0],
                [0],
                color="#f0f4f8",
                marker="s",
                linestyle="none",
                markersize=4,
                label=f"I/O port ({len(ports)})",
            )
        )

    ax.add_collection(
        PolyCollection(
            [[(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]],
            facecolors="none",
            edgecolors=_DIE_EDGE,
            linewidths=1.2,
            zorder=5,
        )
    )

    if stage in ("power", "cts", "routing") and design.power_grid:
        legend_handles = _draw_power(ax, design) + legend_handles

    if stage == "routing":
        legend_handles = _draw_routing(ax, design, max_segments) + legend_handles

    pad_x = span_x * 0.03
    pad_y = span_y * 0.03
    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x (um)", fontsize=8)
    ax.set_ylabel("y (um)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.set_facecolor("#101720")
    fig.patch.set_facecolor("#0f1419")
    for spine in ax.spines.values():
        spine.set_color("#33404f")
    ax.xaxis.label.set_color("#c8d3e0")
    ax.yaxis.label.set_color("#c8d3e0")
    ax.tick_params(colors="#94a3b8")

    if tap_polys:
        legend_handles.append(Line2D([0], [0], color=_TAP_FACE, lw=4, label="Tap"))
    if decap_polys:
        legend_handles.append(
            Line2D([0], [0], color=_DECAP_FACE, lw=4, label="Decap")
        )
    if buffer_polys:
        legend_handles.append(
            Line2D([0], [0], color=_BUFFER_FACE, lw=4, label="CTS buffer")
        )
    if legend_handles:
        legend = ax.legend(
            handles=legend_handles, loc="upper right", fontsize=7, framealpha=0.95
        )
        legend.set_zorder(10)
        legend.get_frame().set_facecolor("#1a222c")
        for text in legend.get_texts():
            text.set_color("#e7eef7")

    title = (
        f"{design.name} — {_STAGE_TITLES[stage]}\n"
        f"{len(design.instances)} placed cells · die {span_x:.1f} x {span_y:.1f} um"
    )
    ax.set_title(title, fontsize=10, color="#e7eef7")
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor())
    fig.clear()
    return out_path


def _cell_polygons(
    design: DesignObject, buffers: set
) -> Tuple[
    List[Sequence[Tuple[float, float]]],
    List[Sequence[Tuple[float, float]]],
    List[Sequence[Tuple[float, float]]],
    List[Sequence[Tuple[float, float]]],
]:
    lib_cells = design.library.get("cells", {})
    cells: List[Sequence[Tuple[float, float]]] = []
    bufs: List[Sequence[Tuple[float, float]]] = []
    taps: List[Sequence[Tuple[float, float]]] = []
    decaps: List[Sequence[Tuple[float, float]]] = []
    for name, inst in design.instances.items():
        w = float(inst.get("width", 0.0))
        h = float(inst.get("height", 0.0))
        if w <= 0 or h <= 0:
            lib = lib_cells.get(design.cells.get(name, {}).get("cell_type", ""), {})
            w = float(lib.get("width", 1.38))
            h = float(lib.get("height", 2.72))
        x = float(inst["x"])
        y = float(inst["y"])
        poly = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        phy = inst.get("physical") or design.cells.get(name, {}).get("physical")
        if name in buffers:
            bufs.append(poly)
        elif phy == "tap":
            taps.append(poly)
        elif phy == "decap":
            decaps.append(poly)
        else:
            cells.append(poly)
    return cells, bufs, taps, decaps


def _draw_power(ax, design: DesignObject) -> List[Line2D]:
    by_net: Dict[str, List[Tuple[Tuple[float, float], Tuple[float, float]]]] = {}
    for seg in design.power_grid.get("segments", []) or []:
        if seg.get("role") == "follow_pin":
            continue
        net = str(seg.get("net", "VPWR"))
        by_net.setdefault(net, []).append(
            ((float(seg["x1"]), float(seg["y1"])), (float(seg["x2"]), float(seg["y2"])))
        )
    handles: List[Line2D] = []
    colors = {"VPWR": _VDD_COLOR, "VGND": _VSS_COLOR}
    for net, lines in by_net.items():
        color = colors.get(net, "#aaaaaa")
        ax.add_collection(
            LineCollection(lines, colors=color, linewidths=1.2, alpha=0.85, zorder=6)
        )
        handles.append(Line2D([0], [0], color=color, lw=2, label=f"{net} ({len(lines)})"))
    return handles


def _draw_routing(ax, design: DesignObject, max_segments: int) -> List[Line2D]:
    by_layer: Dict[str, List[Tuple[Tuple[float, float], Tuple[float, float]]]] = {}
    total = 0
    for segs in design.routing.values():
        for seg in segs:
            by_layer.setdefault(str(seg["layer"]), []).append(
                ((float(seg["x1"]), float(seg["y1"])), (float(seg["x2"]), float(seg["y2"])))
            )
            total += 1
            if total >= max_segments:
                break
        if total >= max_segments:
            break

    palette = dict(_LAYER_COLORS)
    handles: List[Line2D] = []
    for i, (layer, lines) in enumerate(sorted(by_layer.items())):
        color = palette.get(layer, _LAYER_COLORS[i % len(_LAYER_COLORS)][1])
        ax.add_collection(
            LineCollection(lines, colors=color, linewidths=0.45, alpha=0.9, zorder=8)
        )
        handles.append(Line2D([0], [0], color=color, lw=2, label=f"{layer} ({len(lines)})"))
    return handles
