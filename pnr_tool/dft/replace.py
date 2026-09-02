"""Replace non-scan flops with SkyWater HD scan equivalents (OpenROAD ``scan_replace``)."""

from __future__ import annotations

from typing import Any, Dict, List

from pnr_tool.design.object import DesignObject
from pnr_tool.dft.cells import is_scan_cell, scan_equivalent


def scan_replace(design: DesignObject) -> Dict[str, Any]:
    """Swap ``dfxtp``-family cells for ``sdfxtp`` etc. Scan pins stay disconnected.

    Must run **before placement** — scan cells are larger than the plain flops.
    """
    replaced: List[Dict[str, str]] = []
    already: List[str] = []
    unmapped: List[Dict[str, str]] = []

    for inst, info in design.cells.items():
        ctype = str(info.get("cell_type") or "")
        if is_scan_cell(ctype):
            already.append(inst)
            continue
        scan = scan_equivalent(ctype)
        if scan is None:
            lib = (design.library or {}).get("cells", {}).get(ctype, {})
            if lib.get("is_sequential"):
                unmapped.append({"instance": inst, "cell": ctype})
            continue
        info["cell_type"] = scan
        replaced.append({"instance": inst, "from": ctype, "to": scan})

    summary = {
        "replaced": replaced,
        "already_scan": already,
        "unmapped": unmapped,
        "replaced_count": len(replaced),
        "already_scan_count": len(already),
        "unmapped_count": len(unmapped),
    }
    design.meta["dft_replace"] = summary
    return summary
