"""Reduced SPEF dump of routed nets (debug / OpenSTA read_spef)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from pnr_tool.design.object import DesignObject


def _seg_rc(seg: dict, tech: dict) -> Tuple[float, float]:
    layers = tech.get("layers", {})
    width_ref = float(tech.get("width_ref_um", 0.14))
    default_layer = "met2"
    x1, y1 = float(seg.get("x1", 0)), float(seg.get("y1", 0))
    x2, y2 = float(seg.get("x2", 0)), float(seg.get("y2", 0))
    length = abs(x2 - x1) + abs(y2 - y1)
    layer = str(seg.get("layer", default_layer))
    info = layers.get(layer, layers.get(default_layer, {}))
    width = float(seg.get("width_um") or info.get("width_um") or info.get("pitch_um") or width_ref)
    r_per = float(info.get("r_per_um", 0.125)) * (width_ref / max(width, 1e-9))
    c_per = float(info.get("c_per_um", 0.14e-15)) * (max(width, 1e-9) / max(width_ref, 1e-9))
    return r_per * length, c_per * length


def write_spef(design: DesignObject, path: Path) -> Path:
    """Write a SPEF-lite file with one *D_NET per routed net (lumped R/C + pi)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tech = design.tech or {}
    via_default = 5.0
    lines: List[str] = [
        '*SPEF "IEEE 1481-1998"',
        '*DESIGN "{}"'.format(design.name),
        '*T_UNIT 1 NS',
        '*C_UNIT 1 PF',
        '*R_UNIT 1 OHM',
        '*L_UNIT 1 HENRY',
        "",
    ]
    net_id = 1
    for net, ninfo in design.nets.items():
        segs = list(design.routing.get(net, ()) or [])
        if not segs:
            continue
        r_tot = 0.0
        c_tot = 0.0
        prev_layer = None
        for seg in segs:
            r, c = _seg_rc(seg, tech)
            r_tot += r
            c_tot += c
            layer = str(seg.get("layer", "met2"))
            if prev_layer is not None and layer != prev_layer:
                info = tech.get("layers", {}).get(layer, {})
                r_tot += float(info.get("via_r_ohm", via_default))
            prev_layer = layer
        c_pf = c_tot * 1e12
        driver = ninfo.get("driver")
        dname = f"{driver[0]}:{driver[1]}" if driver else "Z"
        lines.append(f"*D_NET *{net_id} {net} {c_pf:.6g}")
        lines.append("*CONN")
        lines.append(f"*I {dname} O")
        for inst, pin in ninfo.get("pins", []) or []:
            if driver and (inst, pin) == tuple(driver):
                continue
            dir_ = "I"
            lines.append(f"*I {inst}:{pin} {dir_}")
        lines.append("*CAP")
        lines.append(f"1 {dname} {c_pf:.6g}")
        lines.append("*RES")
        lines.append(f"1 {dname} {dname} {max(r_tot, 1e-6):.6g}")
        lines.append("*END")
        lines.append("")
        net_id += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
