"""Emit a flat structural Verilog netlist from a DesignObject (OpenSTA input)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from pnr_tool.design.object import DesignObject

_POWER_PINS = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS", "VPWRA", "VGNDA"})


def _ident(name: str) -> str:
    if name and name.isidentifier() and not name.startswith("$"):
        return name
    return "\\" + str(name) + " "


def write_verilog(design: DesignObject, path: Path) -> Path:
    """Write a synthesizable-style structural netlist (no power pins)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ports = list(design.ports.keys())
    port_list = ", ".join(_ident(p) for p in ports)
    lines = [f"module {_ident(design.name).strip()} ({port_list});", ""]
    for pname, pinfo in design.ports.items():
        direction = str(pinfo.get("direction", "inout"))
        if direction not in ("input", "output", "inout"):
            direction = "inout"
        lines.append(f"  {direction} {_ident(pname)};")
    lines.append("")
    declared = set(design.ports)
    for net in design.nets:
        if net in declared:
            continue
        lines.append(f"  wire {_ident(net)};")
        declared.add(net)
    lines.append("")
    port_home: Dict[str, str] = {}
    for net, ninfo in design.nets.items():
        for inst, _pin in ninfo.get("pins") or []:
            if str(inst).startswith("PORT:"):
                port_home[str(inst).split(":", 1)[1]] = net
    for pname in design.ports:
        home = port_home.get(pname)
        if home and home != pname:
            lines.append(f"  assign {_ident(pname)} = {_ident(home)};")
    if port_home:
        lines.append("")
    for inst, info in design.cells.items():
        ctype = info.get("cell_type") or "UNKNOWN"
        conns: Dict[str, Any] = dict(info.get("pins") or {})
        parts = []
        for pin, net in conns.items():
            if pin in _POWER_PINS:
                continue
            parts.append(f".{_ident(pin).strip()}({_ident(str(net))})")
        joined = ", ".join(parts)
        lines.append(f"  {_ident(ctype).strip()} {_ident(inst)} ({joined});")
    lines.append("endmodule")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
