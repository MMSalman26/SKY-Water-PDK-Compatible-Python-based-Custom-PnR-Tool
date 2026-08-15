"""Shared pin geometry helpers for routing and layout."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pnr_tool.design.object import DesignObject

_POWER_PIN_USES = frozenset({"POWER", "GROUND"})
_POWER_PIN_NAMES = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS", "VPWRA", "VGNDA"})


def pin_center_local(pinfo: Mapping[str, Any], w: float, h: float, pin_name: str) -> Tuple[float, float]:
    rects = pinfo.get("rects") or []
    chosen = None
    for r in rects:
        try:
            x1, y1, x2, y2 = float(r["x1"]), float(r["y1"]), float(r["x2"]), float(r["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        ww, hh = abs(x2 - x1), abs(y2 - y1)
        if ww > 0 and hh > 0 and ww / max(hh, 1e-9) < 8:
            chosen = (x1, y1, x2, y2)
            break
        if chosen is None:
            chosen = (x1, y1, x2, y2)
    if chosen is not None:
        x1, y1, x2, y2 = chosen
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    d = str(pinfo.get("direction", "input")).lower()
    if d in ("output", "inout") or pin_name.upper() in ("Y", "X", "Q", "QN", "Z", "CO"):
        return (w * 0.82, h * 0.5)
    return (w * 0.18, h * 0.5)


def instance_pin_xy(design: DesignObject, inst: str, pin: str) -> Optional[Tuple[float, float]]:
    """Absolute pin location in µm, or None if unplaced."""
    if inst.startswith("PORT:"):
        pos = (design.meta.get("port_positions") or {}).get(inst.split(":", 1)[1])
        if pos is None:
            return None
        return float(pos[0]), float(pos[1])
    placed = design.instances.get(inst)
    if placed is None:
        return None
    ox, oy = float(placed["x"]), float(placed["y"])
    ctype = design.cells.get(inst, {}).get("cell_type", "")
    lib = design.library.get("cells", {}).get(ctype, {})
    w = float(placed.get("width") or lib.get("width") or 1.38)
    h = float(placed.get("height") or lib.get("height") or 2.72)
    pinfo = (lib.get("pins") or {}).get(pin, {})
    lx, ly = pin_center_local(pinfo, w, h, pin)
    return ox + lx, oy + ly


def is_power_pin_name(pin: str, pinfo: Optional[Mapping[str, Any]] = None) -> bool:
    if pin.upper() in _POWER_PIN_NAMES:
        return True
    if pinfo and str(pinfo.get("use", "")).upper() in _POWER_PIN_USES:
        return True
    return False


def power_blocked_grid_edges(
    design: DesignObject,
    pitch: float,
    gw: int,
    gh: int,
    gh_edges: int,
) -> Tuple[set, set]:
    """Approximate gcell edges that overlap power straps (for avoid_power)."""
    minx, miny, _, _ = design.die_area
    blocked_h: set = set()
    blocked_v: set = set()
    segs = (design.power_grid or {}).get("segments") or []
    for seg in segs:
        if seg.get("role") == "follow_pin":
            continue  # follow-pins are dense; blocking all would kill routing
        try:
            x1, y1 = float(seg["x1"]), float(seg["y1"])
            x2, y2 = float(seg["x2"]), float(seg["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(y2 - y1) < 1e-9:  # horizontal strap
            gy = int(min(max((y1 - miny) / pitch, 0), gh - 1))
            lo = int(min(max((min(x1, x2) - minx) / pitch, 0), gw - 2))
            hi = int(min(max((max(x1, x2) - minx) / pitch, 0), gw - 1))
            for gx in range(lo, hi):
                blocked_h.add(gx * gh + gy)
        elif abs(x2 - x1) < 1e-9:  # vertical strap
            gx = int(min(max((x1 - minx) / pitch, 0), gw - 1))
            lo = int(min(max((min(y1, y2) - miny) / pitch, 0), gh_edges - 1))
            hi = int(min(max((max(y1, y2) - miny) / pitch, 0), gh_edges))
            for gy in range(lo, max(hi, lo + 1)):
                if 0 <= gy < gh_edges:
                    blocked_v.add(gx * gh_edges + gy)
    return blocked_h, blocked_v
