"""SkyWater HD scan-cell equivalents (OpenROAD ``scan_replace`` mapping)."""

from __future__ import annotations

import re
from typing import Optional, Tuple

_PREFIX = "sky130_fd_sc_hd__"
_CELL_RE = re.compile(r"^(sky130_fd_sc_hd__)([A-Za-z0-9]+)(_\d+)$")

# Non-scan flop stem → scan stem (drive strength is preserved).
SCAN_EQUIV = {
    "dfxtp": "sdfxtp",
    "dfxbp": "sdfxbp",
    "dfrtp": "sdfrtp",
    "dfrbp": "sdfrbp",
    "dfrtn": "sdfrtn",
    "dfstp": "sdfstp",
    "dfsbp": "sdfsbp",
    "dfbbp": "sdfbbp",
    "dfbbn": "sdfbbn",
    "edfxtp": "sedfxtp",
    "edfxbp": "sedfxbp",
}

SCAN_STEMS = frozenset(SCAN_EQUIV.values())
NON_SCAN_STEMS = frozenset(SCAN_EQUIV)

SCAN_IN_PINS = ("SCD", "SI", "SCAN_IN", "SCANIN")
SCAN_EN_PINS = ("SCE", "SE", "SCAN_EN", "SCANEN")
Q_PINS = ("Q", "Q_N", "QN", "X")
CLK_PINS = ("CLK", "CLKN", "CK")


def split_cell(cell_type: str) -> Optional[Tuple[str, str, str]]:
    """Return ``(prefix, stem, drive)`` or None if not a SkyWater HD cell."""
    m = _CELL_RE.match(str(cell_type).strip())
    if not m:
        return None
    return m.group(1), m.group(2).lower(), m.group(3)


def is_scan_cell(cell_type: str) -> bool:
    parts = split_cell(cell_type)
    if parts is None:
        return False
    return parts[1] in SCAN_STEMS


def is_nonscan_flop(cell_type: str) -> bool:
    parts = split_cell(cell_type)
    if parts is None:
        return False
    return parts[1] in NON_SCAN_STEMS


def scan_equivalent(cell_type: str) -> Optional[str]:
    """``sky130_fd_sc_hd__dfxtp_1`` → ``sky130_fd_sc_hd__sdfxtp_1``."""
    parts = split_cell(cell_type)
    if parts is None:
        return None
    prefix, stem, drive = parts
    scan_stem = SCAN_EQUIV.get(stem)
    if not scan_stem:
        return None
    return f"{prefix}{scan_stem}{drive}"


def pick_pin(pins: dict, candidates: Tuple[str, ...]) -> Optional[str]:
    upper = {str(k).upper(): k for k in pins}
    for name in candidates:
        if name in pins:
            return name
        if name.upper() in upper:
            return upper[name.upper()]
    return None
