"""Algorithm-agnostic QoR scorecard helpers (HPWL, routed WL, vias)."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from pnr_tool.design.object import DesignObject


def die_area_um2(die_area: Sequence[float]) -> float:
    if len(die_area) < 4:
        return 0.0
    minx, miny, maxx, maxy = (float(v) for v in die_area[:4])
    return max(0.0, maxx - minx) * max(0.0, maxy - miny)


def _instance_center(inst: Mapping[str, Any]) -> Tuple[float, float]:
    x = float(inst.get("x", 0.0))
    y = float(inst.get("y", 0.0))
    w = float(inst.get("width", 0.0))
    h = float(inst.get("height", 0.0))
    return x + 0.5 * w, y + 0.5 * h


def compute_hpwl_um(design: DesignObject) -> float:
    """Half-perimeter wirelength over nets with ≥2 terminals (um)."""
    ports = design.meta.get("port_positions") or {}
    total = 0.0
    for ninfo in design.nets.values():
        xs: list[float] = []
        ys: list[float] = []
        for inst, _pin in ninfo.get("pins", []):
            if inst.startswith("PORT:"):
                port_name = inst.split(":", 1)[1]
                if port_name in ports:
                    px, py = ports[port_name]
                    xs.append(float(px))
                    ys.append(float(py))
                continue
            inst_data = design.instances.get(inst)
            if not inst_data:
                continue
            cx, cy = _instance_center(inst_data)
            xs.append(cx)
            ys.append(cy)
        if len(xs) < 2:
            continue
        total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return float(total)


def compute_routed_wl_um(routing: Mapping[str, Any]) -> float:
    """Sum of Manhattan segment lengths across all routed nets (um)."""
    total = 0.0
    for segs in routing.values():
        if not isinstance(segs, (list, tuple)):
            continue
        for seg in segs:
            try:
                x1 = float(seg["x1"])
                y1 = float(seg["y1"])
                x2 = float(seg["x2"])
                y2 = float(seg["y2"])
            except (KeyError, TypeError, ValueError):
                continue
            total += abs(x2 - x1) + abs(y2 - y1)
    return float(total)


def count_vias(routing: Mapping[str, Any]) -> int:
    """Approximate via count from consecutive layer changes within each net."""
    vias = 0
    for segs in routing.values():
        if not isinstance(segs, (list, tuple)) or len(segs) < 2:
            continue
        prev_layer: Optional[str] = None
        for seg in segs:
            layer = str(seg.get("layer", "")) if isinstance(seg, Mapping) else ""
            if prev_layer is not None and layer and layer != prev_layer:
                vias += 1
            if layer:
                prev_layer = layer
    return vias


def build_scorecard_metrics(design: DesignObject) -> Dict[str, Any]:
    """Collect algorithm-agnostic metrics for QoR schema 2."""
    die = design.die_area
    fallbacks = design.meta.get("routing_fallbacks", []) or []
    return {
        "num_cells": len(design.cells),
        "num_nets": len(design.nets),
        "die_area_um2": die_area_um2(die),
        "hpwl_um": compute_hpwl_um(design),
        "routed_wl_um": compute_routed_wl_um(design.routing),
        "via_count": count_vias(design.routing),
        "routing_fallback_count": len(fallbacks) if isinstance(fallbacks, (list, tuple)) else int(fallbacks or 0),
        "cts_buffer_count": len(design.clock_tree.get("new_buffers", {}) or {}),
    }
