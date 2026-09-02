"""Optional Yosys synthesis: RTL Verilog → SkyWater HD gate-level netlist."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from pnr_tool.config import load_config, project_root
from pnr_tool.pdk.fetch import TT_CORNER, fetch_pdk, pdk_ready
from pnr_tool.synth.liberty import write_mapping_liberty


class SynthError(RuntimeError):
    pass


def find_yosys(explicit: Optional[str] = None) -> List[str]:
    """Return argv prefix for Yosys: native binary or YoWASP."""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return [str(p)]
        found = shutil.which(explicit)
        if found:
            return [found]
        raise FileNotFoundError(f"Yosys not found: {explicit}")
    scripts = Path(sys.executable).resolve().parent
    for name in ("yosys.exe", "yosys", "yowasp-yosys.exe", "yowasp-yosys"):
        cand = scripts / name
        if cand.is_file():
            return [str(cand)]
    for name in ("yosys", "yosys.exe", "yowasp-yosys", "yowasp-yosys.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    raise FileNotFoundError(
        "Yosys not found. Install OSS CAD Suite (yosys on PATH) or:\n"
        "  pip install yowasp-yosys"
    )


def _infer_top(rtl: Path, top: Optional[str]) -> str:
    if top:
        return str(top)
    text = Path(rtl).read_text(encoding="utf-8", errors="replace")
    import re

    mods = re.findall(r"^\s*module\s+([A-Za-z_]\w*)", text, re.MULTILINE)
    if not mods:
        raise SynthError(f"No module found in {rtl}")
    return mods[-1]


def _ys_script(
    rtl_files: Sequence[Path],
    top: str,
    liberty: Path,
    out_v: Path,
    extra_cmds: Sequence[str] = (),
    skip_dfflibmap: bool = False,
    basic_gate_map: Optional[Path] = None,
) -> str:
    reads = "\n".join(f'read_verilog -sv "{p.as_posix()}"' for p in rtl_files)
    extra = "\n".join(extra_cmds)
    lib = liberty.as_posix()
    out = out_v.as_posix()
    if basic_gate_map is not None:
        mp = basic_gate_map.as_posix()
        map_pass = f"""synth -top {top} -flatten -noabc -nofsm -noshare -noalumacc
simplemap
techmap -map "{mp}"
opt_clean -purge
"""
    else:
        dff = "" if skip_dfflibmap else f'\ndfflibmap -liberty "{lib}"'
        map_pass = f"""synth -top {top} -flatten{dff}
abc -liberty "{lib}"
"""
    return f"""# pnr-tool Yosys script (SkyWater HD mapping)
{reads}
hierarchy -check -top {top}
proc
flatten
opt_expr
opt_clean
{extra}
{map_pass}setundef -zero
splitnets
opt_clean -purge
stat
write_verilog -noattr -noexpr -nohex -nodec "{out}"
"""


def run_yosys_synth(
    rtl: Union[str, Path, Sequence[Union[str, Path]]],
    *,
    top: Optional[str] = None,
    out: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    yosys: Optional[str] = None,
    fetch_if_missing: bool = True,
) -> Dict[str, Any]:
    """Synthesize RTL to a structural ``sky130_fd_sc_hd`` gate-level netlist."""
    cfg = config if config is not None else load_config(config_path)
    synth_cfg = cfg.get("synth") or {}
    cache = Path(cfg["pdk"]["cache_dir"])
    if fetch_if_missing and not pdk_ready(cache):
        fetch_pdk(cache)

    if isinstance(rtl, (str, Path)):
        rtl_files = [Path(rtl)]
    else:
        rtl_files = [Path(p) for p in rtl]
    if not rtl_files:
        raise SynthError("No RTL files given")
    for p in rtl_files:
        if not p.is_file():
            raise SynthError(f"RTL not found: {p}")

    top_name = _infer_top(rtl_files[0], top)
    out_v = Path(out) if out else project_root() / "runs" / top_name / f"{top_name}.gl.v"
    out_v.parent.mkdir(parents=True, exist_ok=True)

    corner = str(synth_cfg.get("liberty_corner") or TT_CORNER)
    liberty = out_v.parent / f"sky130_hd_{corner}.lib"
    raw_pfx = synth_cfg.get("mapping_prefixes")
    allow_prefixes: Optional[List[str]] = None
    if raw_pfx:
        allow_prefixes = [str(p) for p in raw_pfx if str(p).strip()]
    write_mapping_liberty(cache, liberty, corner=corner, allow_prefixes=allow_prefixes)

    local_rtl: List[Path] = []
    for i, p in enumerate(rtl_files):
        dest = out_v.parent / f"_rtl_{i}_{p.name}"
        dest.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        local_rtl.append(Path(dest.name))

    map_copy: Optional[Path] = None
    if allow_prefixes:
        src_map = Path(__file__).with_name("basic_gates.map.v")
        map_copy = out_v.parent / "basic_gates.map.v"
        map_copy.write_text(src_map.read_text(encoding="utf-8"), encoding="utf-8")

    script = _ys_script(
        local_rtl,
        top_name,
        Path(liberty.name),
        Path(out_v.name),
        skip_dfflibmap=bool(allow_prefixes),
        basic_gate_map=Path(map_copy.name) if map_copy else None,
    )
    script_path = out_v.parent / f"{top_name}.ys"
    script_path.write_text(script, encoding="utf-8")

    cmd = find_yosys(yosys or synth_cfg.get("yosys"))
    try:
        proc = subprocess.run(
            cmd + ["-q", "-"],
            input=script,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(out_v.parent),
        )
    except OSError as exc:
        raise SynthError(f"Failed to launch Yosys ({cmd}): {exc}") from exc

    log_path = out_v.parent / f"{top_name}.yosys.log"
    log_path.write_text(
        (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else ""),
        encoding="utf-8",
    )
    if proc.returncode != 0 or not out_v.is_file() or out_v.stat().st_size < 8:
        tail = (proc.stderr or proc.stdout or "")[-4000:]
        raise SynthError(
            f"Yosys failed (exit {proc.returncode}). Log: {log_path}\n{tail}"
        )

    if fetch_if_missing:
        fetch_pdk(cache, extra_netlists=[out_v])

    text = out_v.read_text(encoding="utf-8", errors="replace")
    cells = sorted(set(re.findall(r"sky130_fd_sc_hd__\w+", text)))
    return {
        "top": top_name,
        "rtl": [str(p) for p in rtl_files],
        "netlist": str(out_v),
        "liberty": str(liberty),
        "script": str(script_path),
        "log": str(log_path),
        "yosys": cmd,
        "cells": cells,
    }
