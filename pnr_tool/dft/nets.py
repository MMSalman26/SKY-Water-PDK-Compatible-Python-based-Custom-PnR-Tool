"""Keep DesignObject cell pins and net pin lists in sync."""

from __future__ import annotations

from typing import Optional

from pnr_tool.design.object import DesignObject


def disconnect_pin(design: DesignObject, inst: str, pin: str) -> Optional[str]:
    info = design.cells.get(inst) or {}
    pins = info.setdefault("pins", {})
    old = pins.pop(pin, None)
    if old and old in design.nets:
        lst = list(design.nets[old].get("pins") or [])
        design.nets[old]["pins"] = [(i, p) for i, p in lst if not (i == inst and p == pin)]
    return old


def connect_pin(design: DesignObject, inst: str, pin: str, net: str) -> None:
    disconnect_pin(design, inst, pin)
    design.cells[inst].setdefault("pins", {})[pin] = net
    ninfo = design.nets.setdefault(net, {"pins": [], "drivers": [], "driver": None})
    pair = (inst, pin)
    if pair not in ninfo["pins"]:
        ninfo["pins"].append(pair)


def add_port(design: DesignObject, name: str, direction: str, net: Optional[str] = None) -> str:
    """Add a top-level port. ``net`` defaults to the port name."""
    net = name if net is None else net
    design.ports[name] = {"direction": direction}
    ninfo = design.nets.setdefault(net, {"pins": [], "drivers": [], "driver": None})
    pad = (f"PORT:{name}", "PAD")
    if pad not in ninfo["pins"]:
        ninfo["pins"].append(pad)
    return net
