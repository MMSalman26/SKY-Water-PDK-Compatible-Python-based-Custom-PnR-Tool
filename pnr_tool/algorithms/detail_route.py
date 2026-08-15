"""Light detailed routing: pin stubs from LEF pins to global path endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pnr_tool.algorithms.pins import instance_pin_xy
from pnr_tool.design.object import DesignObject


def add_pin_stubs(
    design: DesignObject,
    routing: Dict[str, List[dict]],
    config: Dict[str, Any],
) -> Dict[str, List[dict]]:
    """Append stub segments from each pin to the nearest global path vertex."""
    rcfg = config.get("routing", {})
    if not rcfg.get("pin_stubs", True):
        return routing
    default_layer = str(rcfg.get("default_layer", "met2"))

    out: Dict[str, List[dict]] = {}
    for net, segs in routing.items():
        ninfo = design.nets.get(net, {})
        pins = list(ninfo.get("pins", []))
        if not segs:
            out[net] = list(segs)
            continue
        # Tag existing as global
        tagged = []
        for s in segs:
            ns = dict(s)
            ns.setdefault("role", "global")
            tagged.append(ns)
        points = _path_points(tagged)
        for inst, pin in pins:
            xy = instance_pin_xy(design, inst, pin)
            if xy is None:
                continue
            px, py = xy
            nearest = _nearest_point(px, py, points)
            if nearest is None:
                continue
            nx, ny = nearest
            if abs(px - nx) + abs(py - ny) < 1e-6:
                continue
            # Prefer horizontal then vertical stub on default_layer (L-shape)
            layer = str(tagged[0].get("layer", default_layer))
            if abs(px - nx) > 1e-9:
                tagged.append(
                    {
                        "layer": layer,
                        "x1": px,
                        "y1": py,
                        "x2": nx,
                        "y2": py,
                        "role": "stub",
                    }
                )
            if abs(py - ny) > 1e-9:
                tagged.append(
                    {
                        "layer": layer,
                        "x1": nx,
                        "y1": py,
                        "x2": nx,
                        "y2": ny,
                        "role": "stub",
                    }
                )
        out[net] = tagged
    return out


def _path_points(segs: List[dict]) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    for s in segs:
        try:
            pts.append((float(s["x1"]), float(s["y1"])))
            pts.append((float(s["x2"]), float(s["y2"])))
        except (KeyError, TypeError, ValueError):
            continue
    return pts


def _nearest_point(
    x: float, y: float, points: List[Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    if not points:
        return None
    return min(points, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
