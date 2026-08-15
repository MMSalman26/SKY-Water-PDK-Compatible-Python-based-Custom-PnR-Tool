"""Load tech parameters from cached sky130A_tech.json + OpenLane TCL hints."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


def load_tech(cache_dir: Path) -> Dict[str, Any]:
    cache_dir = Path(cache_dir)
    tech_path = cache_dir / "sky130A_tech.json"
    if not tech_path.exists():
        raise FileNotFoundError(
            f"Missing {tech_path}. Run: python -m pnr_tool fetch-pdk"
        )
    tech = json.loads(tech_path.read_text(encoding="utf-8"))
    _fill_builtin_rc(tech)
    # Overlay any useful scalars found in OpenLane / open-pdks TCL
    for tcl in (
        cache_dir / "openlane" / "configuration" / "routing.tcl",
        cache_dir / "open-pdks" / "sky130" / "openlane" / "sky130_fd_sc_hd" / "config.tcl",
    ):
        if tcl.exists():
            _overlay_tcl(tech, tcl.read_text(encoding="utf-8", errors="ignore"))
    return tech


def _fill_builtin_rc(tech: dict) -> None:
    """Ensure width/via R/C fields exist on older cached tech JSON."""
    from .fetch import _builtin_sky130_tech

    builtin = _builtin_sky130_tech()
    tech.setdefault("width_ref_um", builtin.get("width_ref_um", 0.14))
    layers = tech.setdefault("layers", {})
    for name, info in (builtin.get("layers") or {}).items():
        dest = layers.setdefault(name, {})
        for key in (
            "width_um",
            "via_r_ohm",
            "r_per_um",
            "c_per_um",
            "pitch_um",
            "min_spacing_um",
            "via_size_um",
            "enclosure_um",
        ):
            if key in info:
                dest.setdefault(key, info[key])
    tech.setdefault("via_size_um", builtin.get("via_size_um", 0.15))
    tech.setdefault("enclosure_um", builtin.get("enclosure_um", 0.055))
    tech.setdefault("mfg_grid_um", builtin.get("mfg_grid_um", 0.005))


def _overlay_tcl(tech: dict, text: str) -> None:
    # Best-effort: set_voltage / FP_CORE_UTIL style vars are not critical;
    # pick up metal pitch-like set statements if present.
    for m in re.finditer(r"set\s+::env\((\w+)\)\s+\"?([^\n\"]+)\"?", text):
        key, val = m.group(1), m.group(2).strip()
        if key in ("VDD_PIN",):
            tech.setdefault("vdd_pin", val)
        if key in ("FP_CORE_UTIL",) and val.replace(".", "", 1).isdigit():
            tech["core_util"] = float(val)
