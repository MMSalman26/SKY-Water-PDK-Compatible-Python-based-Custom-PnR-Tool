"""VDD/VSS power planning: core ring + straps (physical, not from netlist)."""

from __future__ import annotations

from typing import Any, Dict, List

from pnr_tool.design.object import DesignObject


def plan_power(design: DesignObject, config: Dict[str, Any]) -> Dict[str, Any]:
    """Build ``design.power_grid`` with VPWR/VGND ring and strap segments."""
    pcfg = config.get("power", {})
    pitch = float(pcfg.get("strap_pitch_um", 20.0))
    ring_inset = float(pcfg.get("ring_inset_um", 1.0))
    vdd_layer = str(pcfg.get("vdd_layer", "met5"))
    vss_layer = str(pcfg.get("vss_layer", "met4"))
    follow_layer = str(pcfg.get("follow_pin_layer", "met1"))
    ring_w = float(pcfg.get("ring_width_um", 1.6))
    strap_w = float(pcfg.get("strap_width_um", 0.8))
    follow_w = float(pcfg.get("follow_pin_width_um", 0.48))
    width_ref = float(pcfg.get("width_ref_um", 0.48))

    minx, miny, maxx, maxy = (float(v) for v in design.die_area)
    # Inset ring slightly inside die
    x0, y0 = minx + ring_inset, miny + ring_inset
    x1, y1 = maxx - ring_inset, maxy - ring_inset
    if x1 <= x0 or y1 <= y0:
        x0, y0, x1, y1 = minx, miny, maxx, maxy

    segs: List[Dict[str, Any]] = []

    def add(
        net: str,
        layer: str,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        role: str,
        width_um: float,
    ) -> None:
        segs.append(
            {
                "net": net,
                "layer": layer,
                "x1": ax,
                "y1": ay,
                "x2": bx,
                "y2": by,
                "role": role,
                "width_um": width_um,
            }
        )

    # Core rings: VDD outer-ish on met5, VSS slightly inset on met4
    add("VPWR", vdd_layer, x0, y0, x1, y0, "ring", ring_w)
    add("VPWR", vdd_layer, x1, y0, x1, y1, "ring", ring_w)
    add("VPWR", vdd_layer, x1, y1, x0, y1, "ring", ring_w)
    add("VPWR", vdd_layer, x0, y1, x0, y0, "ring", ring_w)

    inset = min(2.0, (x1 - x0) * 0.05, (y1 - y0) * 0.05)
    sx0, sy0, sx1, sy1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    add("VGND", vss_layer, sx0, sy0, sx1, sy0, "ring", ring_w)
    add("VGND", vss_layer, sx1, sy0, sx1, sy1, "ring", ring_w)
    add("VGND", vss_layer, sx1, sy1, sx0, sy1, "ring", ring_w)
    add("VGND", vss_layer, sx0, sy1, sx0, sy0, "ring", ring_w)

    # Vertical VDD straps + horizontal VSS straps
    x = x0 + pitch
    while x < x1 - pitch * 0.5:
        add("VPWR", vdd_layer, x, y0, x, y1, "strap", strap_w)
        x += pitch
    y = y0 + pitch * 0.5
    while y < y1 - pitch * 0.25:
        add("VGND", vss_layer, x0, y, x1, y, "strap", strap_w)
        y += pitch

    # Light met1 follow-pin rails (row-aligned abstraction for IR / DRC)
    row_h = float(config.get("placement", {}).get("row_height_um", design.tech.get("site_height_um", 2.72)))
    y = miny
    row = 0
    while y <= maxy + 1e-9:
        net = "VPWR" if row % 2 == 0 else "VGND"
        add(net, follow_layer, minx, y, maxx, y, "follow_pin", follow_w)
        y += row_h
        row += 1

    grid = {
        "vdd_net": "VPWR",
        "vss_net": "VGND",
        "vdd_layer": vdd_layer,
        "vss_layer": vss_layer,
        "strap_pitch_um": pitch,
        "width_ref_um": width_ref,
        "segments": segs,
    }
    design.power_grid = grid
    return grid
