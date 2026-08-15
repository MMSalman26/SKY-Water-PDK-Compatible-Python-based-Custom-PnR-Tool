"""Download SkyWater HD cells + OpenLane configuration into a local cache."""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from pnr_tool.pdk.alu_cells import alu_cell_pairs, cell_folder

# Base curated set + ALU logic cells (deduped, order-preserving)
_BASE_CELLS: Sequence[tuple[str, str]] = (
    ("inv", "sky130_fd_sc_hd__inv_1"),
    ("inv", "sky130_fd_sc_hd__inv_2"),
    ("nand2", "sky130_fd_sc_hd__nand2_1"),
    ("nand2", "sky130_fd_sc_hd__nand2_2"),
    ("nor2", "sky130_fd_sc_hd__nor2_1"),
    ("and2", "sky130_fd_sc_hd__and2_0"),
    ("and2", "sky130_fd_sc_hd__and2_1"),
    ("or2", "sky130_fd_sc_hd__or2_0"),
    ("or2", "sky130_fd_sc_hd__or2_1"),
    ("buf", "sky130_fd_sc_hd__buf_1"),
    ("buf", "sky130_fd_sc_hd__buf_2"),
    ("clkbuf", "sky130_fd_sc_hd__clkbuf_1"),
    ("clkbuf", "sky130_fd_sc_hd__clkbuf_2"),
    ("clkbuf", "sky130_fd_sc_hd__clkbuf_4"),
    ("clkbuf", "sky130_fd_sc_hd__clkbuf_16"),
    ("dfxtp", "sky130_fd_sc_hd__dfxtp_1"),
    ("dfxtp", "sky130_fd_sc_hd__dfxtp_2"),
    ("dfrtp", "sky130_fd_sc_hd__dfrtp_1"),
    ("conb", "sky130_fd_sc_hd__conb_1"),
    ("mux2", "sky130_fd_sc_hd__mux2_1"),
    ("a21o", "sky130_fd_sc_hd__a21o_1"),
    ("o21a", "sky130_fd_sc_hd__o21a_1"),
    ("tapvpwrvgnd", "sky130_fd_sc_hd__tapvpwrvgnd_1"),
    ("decap", "sky130_fd_sc_hd__decap_3"),
    ("decap", "sky130_fd_sc_hd__decap_4"),
    ("decap", "sky130_fd_sc_hd__decap_6"),
    ("decap", "sky130_fd_sc_hd__decap_8"),
    ("decap", "sky130_fd_sc_hd__decap_12"),
)

_SKY130_CELL_RE = re.compile(r"\b(sky130_fd_sc_hd__\w+)\b")


def _dedupe_cells(pairs: Sequence[tuple[str, str]]) -> List[tuple[str, str]]:
    seen = set()
    out: List[tuple[str, str]] = []
    for folder, stem in pairs:
        if stem in seen:
            continue
        seen.add(stem)
        out.append((folder, stem))
    return out


CURATED_CELLS: Sequence[tuple[str, str]] = tuple(
    _dedupe_cells(list(_BASE_CELLS) + alu_cell_pairs())
)


def scan_netlist_cells(path: Union[str, Path]) -> List[str]:
    """Return sorted unique ``sky130_fd_sc_hd__*`` cell stems from a netlist."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return sorted(set(_SKY130_CELL_RE.findall(text)))


def cell_pairs_from_stems(stems: Iterable[str]) -> List[tuple[str, str]]:
    return [(cell_folder(stem), stem) for stem in stems]


def cells_for_fetch(
    extra_netlists: Optional[Sequence[Union[str, Path]]] = None,
) -> List[tuple[str, str]]:
    pairs = list(CURATED_CELLS)
    if extra_netlists:
        for nl in extra_netlists:
            pairs.extend(cell_pairs_from_stems(scan_netlist_cells(nl)))
    return _dedupe_cells(pairs)

SKY130_RAW = "https://raw.githubusercontent.com/google/skywater-pdk-libs-sky130_fd_sc_hd/main"
OPENLANE_RAW = "https://raw.githubusercontent.com/The-OpenROAD-Project/OpenLane/master"
OPENPDKS_RAW = "https://raw.githubusercontent.com/fossi-foundation/open-pdks/master"

FF_CORNER = "ff_100C_1v95"
SS_CORNER = "ss_100C_1v60"
TT_CORNER = "tt_025C_1v80"
TIMING_CORNERS = (FF_CORNER, SS_CORNER, TT_CORNER)

OPENLANE_FILES = (
    "configuration/routing.tcl",
    "configuration/placement.tcl",
    "configuration/cts.tcl",
    "configuration/extraction.tcl",
    "configuration/general.tcl",
    "configuration/floorplan.tcl",
)

# Tech LEF / layer info reference from open_pdks sky130A (text files)
OPENPDKS_FILES = (
    # Documented reference; may 404 on some paths — fetch tolerates missing
    "sky130/openlane/sky130_fd_sc_hd/config.tcl",
)


def _urlopen(url: str, timeout: float = 60.0):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "pnr-tool/0.1"})
    return urllib.request.urlopen(req, context=ctx, timeout=timeout)


# Physical-only cells ship no timing library, and the "nom" tech LEF is not
# published upstream — their 404s are expected, not failures.
_OPTIONAL_CELL_PREFIXES = ("fill", "decap", "tap", "diode")
_OPTIONAL_FILES = frozenset({"tech/sky130_fd_sc_hd__nom.tlef"})


def _is_optional_lib(stem: str) -> bool:
    suffix = stem.rsplit("__", 1)[-1] if "__" in stem else stem
    return suffix.startswith(_OPTIONAL_CELL_PREFIXES)


def _download(url: str, dest: Path, force: bool = False, optional: bool = False) -> bool:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _urlopen(url) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        if optional:
            print(f"  INFO: optional asset not published upstream: {dest.name}")
        else:
            print(f"  WARN: failed to download {url}: {exc}")
        return False


def fetch_pdk(
    cache_dir: Path,
    force: bool = False,
    extra_netlists: Optional[Sequence[Union[str, Path]]] = None,
) -> Path:
    """Fetch curated PDK/tech assets into cache_dir. Idempotent.

    When ``extra_netlists`` is provided, also download LEF/lib for every
    ``sky130_fd_sc_hd__*`` stem referenced in those netlists.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    downloaded: List[str] = []
    cells = cells_for_fetch(extra_netlists)

    print(f"Fetching PDK into {cache_dir} ({len(cells)} cells) ...")

    # Per-cell LEF + ff/ss/tt liberty JSON (ss/tt optional if unpublished)
    for folder, stem in cells:
        lef_rel = f"cells/{folder}/{stem}.lef"
        optional_lib = _is_optional_lib(stem)
        dest_lef = cache_dir / "sky130_fd_sc_hd" / lef_rel
        if _download(f"{SKY130_RAW}/{lef_rel}", dest_lef, force=force, optional=False):
            downloaded.append(lef_rel)
        for corner in TIMING_CORNERS:
            lib_rel = f"cells/{folder}/{stem}__{corner}.lib.json"
            dest_lib = cache_dir / "sky130_fd_sc_hd" / lib_rel
            optional = optional_lib or corner != FF_CORNER
            if _download(f"{SKY130_RAW}/{lib_rel}", dest_lib, force=force, optional=optional):
                downloaded.append(lib_rel)

    # Tech folder (site / leftover)
    for rel in (
        "tech/sky130_fd_sc_hd.tlef",
        "tech/sky130_fd_sc_hd__nom.tlef",
    ):
        url = f"{SKY130_RAW}/{rel}"
        dest = cache_dir / "sky130_fd_sc_hd" / rel
        if _download(url, dest, force=force, optional=rel in _OPTIONAL_FILES):
            downloaded.append(rel)

    # OpenLane configuration
    for rel in OPENLANE_FILES:
        url = f"{OPENLANE_RAW}/{rel}"
        dest = cache_dir / "openlane" / rel
        if _download(url, dest, force=force):
            downloaded.append(f"openlane/{rel}")

    # open-pdks reference config (best-effort)
    for rel in OPENPDKS_FILES:
        url = f"{OPENPDKS_RAW}/{rel}"
        dest = cache_dir / "open-pdks" / rel
        if _download(url, dest, force=force, optional=True):
            downloaded.append(f"open-pdks/{rel}")

    # Write built-in sky130A tech JSON used by the framework (from OpenLane/open_pdks knowledge)
    tech_json = cache_dir / "sky130A_tech.json"
    if force or not tech_json.exists():
        tech_json.write_text(json.dumps(_builtin_sky130_tech(), indent=2), encoding="utf-8")
        downloaded.append("sky130A_tech.json")

    manifest = {
        "corner": FF_CORNER,
        "corners": list(TIMING_CORNERS),
        "cells": [stem for _, stem in cells],
        "extra_netlists": [str(p) for p in (extra_netlists or [])],
        "files": downloaded,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"PDK fetch complete ({len(downloaded)} files).")
    return cache_dir


def _builtin_sky130_tech() -> dict:
    """Sky130A routing/IR defaults aligned with OpenLane sky130A."""
    return {
        "name": "sky130A",
        "vdd": 1.8,
        "site_height_um": 2.72,
        "site_width_um": 0.46,
        "layers": {
            "li1": {"pitch_um": 0.46, "width_um": 0.17, "r_per_um": 12.2, "c_per_um": 0.15e-15, "direction": "horizontal", "via_r_ohm": 50.0, "via_size_um": 0.17, "enclosure_um": 0.06},
            "met1": {"pitch_um": 0.48, "width_um": 0.14, "r_per_um": 0.125, "c_per_um": 0.15e-15, "direction": "horizontal", "min_spacing_um": 0.14, "via_r_ohm": 15.0, "via_size_um": 0.15, "enclosure_um": 0.055},
            "met2": {"pitch_um": 0.48, "width_um": 0.14, "r_per_um": 0.125, "c_per_um": 0.14e-15, "direction": "vertical", "min_spacing_um": 0.14, "via_r_ohm": 15.0, "via_size_um": 0.15, "enclosure_um": 0.055},
            "met3": {"pitch_um": 0.64, "width_um": 0.30, "r_per_um": 0.047, "c_per_um": 0.14e-15, "direction": "horizontal", "min_spacing_um": 0.3, "via_r_ohm": 8.0, "via_size_um": 0.20, "enclosure_um": 0.065},
            "met4": {"pitch_um": 0.74, "width_um": 0.30, "r_per_um": 0.047, "c_per_um": 0.12e-15, "direction": "vertical", "min_spacing_um": 0.3, "via_r_ohm": 5.0, "via_size_um": 0.20, "enclosure_um": 0.065},
            "met5": {"pitch_um": 1.6, "width_um": 1.6, "r_per_um": 0.029, "c_per_um": 0.12e-15, "direction": "horizontal", "min_spacing_um": 1.6, "via_r_ohm": 3.0, "via_size_um": 0.80, "enclosure_um": 0.16},
        },
        "width_ref_um": 0.14,
        "via_size_um": 0.15,
        "enclosure_um": 0.055,
        "mfg_grid_um": 0.005,
        "power_layers": ["met1", "met2", "met3", "met4", "met5"],
        "signal_layers": ["met1", "met2", "met3", "met4", "met5"],
        "default_clock_buffer": "sky130_fd_sc_hd__clkbuf_4",
        "tie_hi_cell": "sky130_fd_sc_hd__conb_1",
        "tie_lo_cell": "sky130_fd_sc_hd__conb_1",
    }


def pdk_ready(cache_dir: Path) -> bool:
    cache_dir = Path(cache_dir)
    if not (cache_dir / "manifest.json").exists():
        return False
    if not (cache_dir / "sky130A_tech.json").exists():
        return False
    # At least a few LEFs
    lefs = list((cache_dir / "sky130_fd_sc_hd" / "cells").rglob("*.lef"))
    return len(lefs) >= 5


def list_cached_cells(cache_dir: Path) -> Iterable[str]:
    for lef in (Path(cache_dir) / "sky130_fd_sc_hd" / "cells").rglob("*.lef"):
        yield lef.stem
