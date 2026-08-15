"""CIF dump of inflated metals, vias, and LEF OBS for Magic/KLayout."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pnr_tool.checkers.drc import MetalRect, collect_drc_geometry
from pnr_tool.design.object import DesignObject

# CIF user unit: 0.001 um (1 nm)
_SCALE = 1000.0

_LAYER_MAP = {
    "li1": "LIP",
    "met1": "MET1",
    "met2": "MET2",
    "met3": "MET3",
    "met4": "MET4",
    "met5": "MET5",
}


def _cif_box(bbox: tuple) -> str:
    x1, y1, x2, y2 = bbox
    cx = int(round((x1 + x2) * 0.5 * _SCALE))
    cy = int(round((y1 + y2) * 0.5 * _SCALE))
    length = max(1, int(round(abs(x2 - x1) * _SCALE)))
    width = max(1, int(round(abs(y2 - y1) * _SCALE)))
    return f"B {length} {width} {cx} {cy};"


def write_cif(design: DesignObject, path: Path, config: Optional[Dict[str, Any]] = None) -> Path:
    """Write a CIF file Magic/KLayout can read (inflated wires + vias + OBS)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    geom = collect_drc_geometry(design, config or {})
    lines: List[str] = [
        "(CIF 2.0);",
        f"(Generator pnr_tool DRC dump {design.name});",
        "DS 1 1 1;",
        f"9 {design.name};",
    ]
    by_layer: Dict[str, List[MetalRect]] = {}
    for r in list(geom["wires"]) + list(geom["vias"]) + list(geom["obs"]):
        by_layer.setdefault(r.layer, []).append(r)
    for layer, rects in sorted(by_layer.items()):
        cif_l = _LAYER_MAP.get(layer, layer.upper().replace("-", "")[:8])
        lines.append(f"L {cif_l};")
        for r in rects:
            lines.append(_cif_box(r.bbox))
    lines.append("DF;")
    lines.append("C 1;")
    lines.append("E")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
