"""Export interactive layout geometry as ``layout_view.json``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pnr_tool.design.object import DesignObject

LAYOUT_VIEW_FILE = "layout_view.json"
STAGE_VIEW_FILES = {
    "power": "layout_view_power.json",
    "placement": "layout_view_placement.json",
    "cts": "layout_view_cts.json",
    "routing": "layout_view.json",
}

_POWER_PIN_USES = frozenset({"POWER", "GROUND"})
_POWER_PIN_NAMES = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS", "VPWRA", "VGNDA"})
_STRENGTH_RE = re.compile(r"_(\d+)$")


def _parse_cell_meta(cell_type: str) -> Dict[str, Any]:
    """Derive family / drive strength from a sky130 cell type name."""
    short = cell_type
    for prefix in ("sky130_fd_sc_hd__", "sky130_ef_sc_hd__"):
        if short.startswith(prefix):
            short = short[len(prefix) :]
            break
    strength = None
    m = _STRENGTH_RE.search(short)
    if m:
        strength = int(m.group(1))
        family = short[: m.start()]
    else:
        family = short
    kind = family.split("_")[0] if family else "unknown"
    return {
        "cell_type": cell_type,
        "family": family or "unknown",
        "kind": kind or "unknown",
        "drive_strength": strength,
    }


def _pin_center_from_rects(rects: Sequence[Mapping[str, Any]]) -> Optional[Tuple[float, float]]:
    if not rects:
        return None
    # Prefer non-power-rail-looking signal layers; else first rect.
    chosen = None
    for r in rects:
        layer = str(r.get("layer", "")).lower()
        if layer in ("li1", "met1"):
            # Skip very wide rails (power) — aspect wide relative to height
            try:
                x1, y1, x2, y2 = float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"])
            except (KeyError, TypeError, ValueError):
                continue
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w > 0 and h > 0 and w / max(h, 1e-9) < 8:
                chosen = (x1, y1, x2, y2)
                break
            if chosen is None:
                chosen = (x1, y1, x2, y2)
    if chosen is None:
        try:
            r = rects[0]
            chosen = (float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return None
    x1, y1, x2, y2 = chosen
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _fallback_pin_xy(
    pin_name: str,
    direction: str,
    index: int,
    n_side: int,
    w: float,
    h: float,
) -> Tuple[float, float]:
    """Place pins along left (inputs) / right (outputs) when LEF rects missing."""
    n_side = max(n_side, 1)
    frac = (index + 0.5) / n_side
    y = max(0.15 * h, min(h * (1.0 - 0.15), h * frac))
    d = (direction or "input").lower()
    if d in ("output", "inout") or pin_name.upper() in ("Y", "X", "Q", "QN", "Z", "CO"):
        return (w * 0.82, y)
    return (w * 0.18, y)


def _cell_pins(
    cell_type: str,
    lib_cell: Mapping[str, Any],
    ox: float,
    oy: float,
    w: float,
    h: float,
) -> List[Dict[str, Any]]:
    pins_out: List[Dict[str, Any]] = []
    lib_pins = lib_cell.get("pins") or {}
    # Stable order: inputs then outputs then other; skip pure power for default view data
    # but still export them with is_power so the UI can toggle.
    items = list(lib_pins.items())
    if not items:
        return pins_out

    inputs = [(n, p) for n, p in items if str(p.get("direction", "")).lower() == "input"]
    outputs = [(n, p) for n, p in items if str(p.get("direction", "")).lower() == "output"]
    others = [
        (n, p)
        for n, p in items
        if str(p.get("direction", "")).lower() not in ("input", "output")
    ]

    def emit(group: List[Tuple[str, Any]], side_count: int) -> None:
        for i, (pname, pinfo) in enumerate(group):
            use = str(pinfo.get("use", "SIGNAL")).upper()
            is_power = use in _POWER_PIN_USES or pname.upper() in _POWER_PIN_NAMES
            direction = str(pinfo.get("direction", "input")).lower()
            local = _pin_center_from_rects(pinfo.get("rects") or [])
            if local is None:
                local = _fallback_pin_xy(pname, direction, i, side_count, w, h)
            lx, ly = local
            pins_out.append(
                {
                    "name": pname,
                    "direction": direction,
                    "use": use,
                    "is_clock": bool(pinfo.get("is_clock")),
                    "is_power": is_power,
                    "x": ox + lx,
                    "y": oy + ly,
                }
            )

    emit(inputs, len(inputs) or 1)
    emit(outputs, len(outputs) or 1)
    emit(others, len(others) or 1)
    return pins_out


def build_layout_view(
    design: DesignObject,
    stage: str = "routing",
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    report_cfg = dict((config or {}).get("report", {}))
    max_segments = int(report_cfg.get("layout_max_segments", 200000))

    buffers = set(design.clock_tree.get("new_buffers", {})) if stage != "placement" else set()
    lib_cells = design.library.get("cells", {})

    cells: List[Dict[str, Any]] = []
    for name, inst in design.instances.items():
        ctype = design.cells.get(name, {}).get("cell_type", "")
        lib = lib_cells.get(ctype, {})
        w = float(inst.get("width", 0.0))
        h = float(inst.get("height", 0.0))
        if w <= 0 or h <= 0:
            w = float(lib.get("width", 1.38))
            h = float(lib.get("height", 2.72))
        ox, oy = float(inst["x"]), float(inst["y"])
        meta = _parse_cell_meta(str(ctype or "unknown"))
        phy = inst.get("physical") or design.cells.get(name, {}).get("physical")
        cells.append(
            {
                "name": name,
                "x": ox,
                "y": oy,
                "w": w,
                "h": h,
                "is_buffer": name in buffers,
                "physical": phy,
                "cell_type": meta["cell_type"],
                "family": meta["family"],
                "kind": meta["kind"],
                "drive_strength": meta["drive_strength"],
                "pins": _cell_pins(str(ctype), lib, ox, oy, w, h),
            }
        )

    ports: List[Dict[str, Any]] = []
    for pname, xy in (design.meta.get("port_positions") or {}).items():
        try:
            ports.append({"name": str(pname), "x": float(xy[0]), "y": float(xy[1])})
        except (TypeError, ValueError, IndexError):
            continue

    segments: List[Dict[str, Any]] = []
    if stage == "routing":
        total = 0
        for segs in design.routing.values():
            if not isinstance(segs, (list, tuple)):
                continue
            for seg in segs:
                if total >= max_segments:
                    break
                try:
                    segments.append(
                        {
                            "layer": str(seg["layer"]),
                            "x1": float(seg["x1"]),
                            "y1": float(seg["y1"]),
                            "x2": float(seg["x2"]),
                            "y2": float(seg["y2"]),
                            "role": str(seg.get("role", "global")),
                            "net": None,
                        }
                    )
                    total += 1
                except (KeyError, TypeError, ValueError):
                    continue
            if total >= max_segments:
                break

    power_segments: List[Dict[str, Any]] = []
    if stage in ("power", "cts", "routing"):
        for seg in (design.power_grid or {}).get("segments", []) or []:
            if seg.get("role") == "follow_pin":
                continue
            try:
                power_segments.append(
                    {
                        "net": str(seg.get("net", "VPWR")),
                        "layer": str(seg["layer"]),
                        "x1": float(seg["x1"]),
                        "y1": float(seg["y1"]),
                        "x2": float(seg["x2"]),
                        "y2": float(seg["y2"]),
                        "role": str(seg.get("role", "strap")),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue

    die = [float(v) for v in design.die_area]
    return {
        "schema": 2,
        "design": design.name,
        "stage": stage,
        "die_area": die,
        "cells": cells,
        "ports": ports,
        "segments": segments,
        "power_segments": power_segments,
        "truncated_segments": stage == "routing"
        and sum(len(s) if isinstance(s, (list, tuple)) else 0 for s in design.routing.values())
        > len(segments),
    }


def write_layout_view(
    design: DesignObject,
    stage: str,
    out_dir: Path,
    config: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """Write stage layout JSON. Final routing also writes ``layout_view.json``."""
    report_cfg = dict((config or {}).get("report", {}))
    if not report_cfg.get("layout_view", True):
        return None
    if stage not in STAGE_VIEW_FILES:
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_layout_view(design, stage=stage, config=config)

    path = out_dir / STAGE_VIEW_FILES[stage]
    path.write_text(json.dumps(payload), encoding="utf-8")

    # Always keep canonical name pointing at the richest available stage.
    if stage == "routing":
        (out_dir / LAYOUT_VIEW_FILE).write_text(json.dumps(payload), encoding="utf-8")
    return path


def layout_view_paths(out_dir: Path) -> Dict[str, Path]:
    out_dir = Path(out_dir)
    return {stage: out_dir / name for stage, name in STAGE_VIEW_FILES.items()}
