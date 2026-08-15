"""Minimal LEF MACRO parser for cell size and pin geometry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


_MACRO_RE = re.compile(r"\bMACRO\s+(\S+)", re.IGNORECASE)
_SIZE_RE = re.compile(r"\bSIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)", re.IGNORECASE)
_PIN_RE = re.compile(r"\bPIN\s+(\S+)", re.IGNORECASE)
_DIR_RE = re.compile(r"\bDIRECTION\s+(\S+)\s*;", re.IGNORECASE)
_USE_RE = re.compile(r"\bUSE\s+(\S+)\s*;", re.IGNORECASE)
_LAYER_RE = re.compile(r"\bLAYER\s+(\S+)\s*;", re.IGNORECASE)
_RECT_RE = re.compile(
    r"\bRECT\s+([0-9.+-eE]+)\s+([0-9.+-eE]+)\s+([0-9.+-eE]+)\s+([0-9.+-eE]+)\s*;",
    re.IGNORECASE,
)


def parse_lef_file(path: Path) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return parse_lef_text(text)


def parse_lef_text(text: str) -> Dict[str, Any]:
    cells: Dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _MACRO_RE.search(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        width = height = 0.0
        pins: Dict[str, Any] = {}
        obs: List[Dict[str, Any]] = []
        i += 1
        current_pin = None
        current_layer = None
        in_obs = False
        while i < len(lines):
            line = lines[i]
            if re.match(r"\s*END\s+" + re.escape(name) + r"\b", line, re.IGNORECASE):
                break
            sm = _SIZE_RE.search(line)
            if sm:
                width, height = float(sm.group(1)), float(sm.group(2))
            if re.match(r"\s*OBS\b", line, re.IGNORECASE):
                in_obs = True
                current_pin = None
                current_layer = None
            pm = _PIN_RE.search(line)
            if pm:
                in_obs = False
                current_pin = pm.group(1)
                pins[current_pin] = {
                    "direction": "input",
                    "use": "SIGNAL",
                    "rects": [],
                }
            if in_obs:
                lm = _LAYER_RE.search(line)
                if lm:
                    current_layer = lm.group(1)
                for rm in _RECT_RE.finditer(line):
                    obs.append(
                        {
                            "layer": current_layer or "li1",
                            "x1": float(rm.group(1)),
                            "y1": float(rm.group(2)),
                            "x2": float(rm.group(3)),
                            "y2": float(rm.group(4)),
                        }
                    )
                if re.match(r"\s*END\b", line, re.IGNORECASE) and not re.search(
                    r"END\s+\S+", line, re.IGNORECASE
                ):
                    in_obs = False
                    current_layer = None
            elif current_pin:
                dm = _DIR_RE.search(line)
                if dm:
                    pins[current_pin]["direction"] = dm.group(1).lower()
                um = _USE_RE.search(line)
                if um:
                    pins[current_pin]["use"] = um.group(1).upper()
                lm = _LAYER_RE.search(line)
                if lm:
                    current_layer = lm.group(1)
                for rm in _RECT_RE.finditer(line):
                    pins[current_pin]["rects"].append(
                        {
                            "layer": current_layer or "li1",
                            "x1": float(rm.group(1)),
                            "y1": float(rm.group(2)),
                            "x2": float(rm.group(3)),
                            "y2": float(rm.group(4)),
                        }
                    )
                if re.match(r"\s*END\s+" + re.escape(current_pin) + r"\b", line, re.IGNORECASE):
                    current_pin = None
                    current_layer = None
            i += 1
        cells[name] = {"width": width, "height": height, "pins": pins, "obs": obs}
        i += 1
    return cells


def parse_lef_dir(root: Path) -> Dict[str, Any]:
    cells: Dict[str, Any] = {}
    for path in Path(root).rglob("*.lef"):
        cells.update(parse_lef_file(path))
    # Also tlef if present
    for path in Path(root).rglob("*.tlef"):
        cells.update(parse_lef_file(path))
    return cells
