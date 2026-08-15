#!/usr/bin/env python3
"""Optional OpenSTA correlation harness for ALU / PicoRV32a (or any checkpoint).

Writes Verilog + SDC + SPEF-lite from a routed DesignObject, runs this tool's STA,
and if the OpenSTA ``sta`` binary is on PATH, reports WNS/path delay for comparison.

  python scripts/compare_opensta.py --checkpoint runs/alu/checkpoints/alu_routing.pkl
  python scripts/compare_opensta.py --run-dir runs/picorv32a --clock-period-ns 24

Does not claim signoff. Skip OpenSTA cleanly when it is not installed.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pnr_tool.checkers.sdc import write_default_sdc
from pnr_tool.checkers.sta import run_sta
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.io.verilog_writer import write_verilog
from pnr_tool.report.spef import write_spef


def _find_checkpoint(run_dir: Path) -> Optional[Path]:
    ckpt = run_dir / "checkpoints"
    if not ckpt.is_dir():
        return None
    preferred = list(ckpt.glob("*_routing.pkl"))
    if preferred:
        return preferred[0]
    pkls = sorted(ckpt.glob("*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pkls[0] if pkls else None


def _clock_port(design: DesignObject) -> str:
    for name, info in design.ports.items():
        n = name.lower()
        if info.get("direction") == "input" and ("clk" in n or n == "clock"):
            return name
    for name, info in design.ports.items():
        if info.get("direction") == "input":
            return name
    return "clk"


def _write_opensta_tcl(
    path: Path,
    verilog: Path,
    top: str,
    sdc: Path,
    spef: Path,
    liberty: Optional[Path],
) -> Path:
    lines = ["set_cmd_units -time ns -capacitance pF -resistance ohm", ""]
    if liberty and liberty.exists():
        lines.append(f'read_liberty "{liberty.as_posix()}"')
    lines += [
        f'read_verilog "{verilog.as_posix()}"',
        f"link_design {top}",
        f'read_sdc "{sdc.as_posix()}"',
        f'read_spef "{spef.as_posix()}"',
        "report_checks -path_delay max -digits 4",
        "report_wns",
        "report_tns",
        "exit",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _parse_opensta_wns(text: str) -> Optional[float]:
    # OpenSTA: "wns -0.12" or "WNS 0.00"
    import re

    m = re.search(r"\bwns\s+([-+]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _find_liberty(cache: Path) -> Optional[Path]:
    cells = cache / "sky130_fd_sc_hd" / "timing"
    if not cells.exists():
        cells = cache
    hits = list(cells.rglob("*ff_100C_1v95.lib"))
    if hits:
        return hits[0]
    jsons = list(cells.rglob("*ff_100C_1v95.lib.json"))
    return jsons[0] if jsons else None


def compare(
    design: DesignObject,
    out_dir: Path,
    clock_period_ns: float,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    config = config or load_config()
    sta_ours = run_sta(design, config, clock_period_ns=clock_period_ns)
    verilog = write_verilog(design, out_dir / f"{design.name}.v")
    clk = _clock_port(design)
    ins = [n for n, p in design.ports.items() if p.get("direction") == "input"]
    outs = [n for n, p in design.ports.items() if p.get("direction") == "output"]
    sdc = write_default_sdc(
        out_dir / f"{design.name}.sdc",
        clock_period_ns,
        clock_port=clk,
        uncertainty_ns=float((config.get("sta") or {}).get("uncertainty_ns", 0.05)),
        input_ports=ins,
        output_ports=outs,
    )
    spef = write_spef(design, out_dir / f"{design.name}.spef")
    cache = Path((config.get("pdk") or {}).get("cache_dir") or (ROOT / "pdk_cache"))
    liberty = _find_liberty(cache)
    tcl = _write_opensta_tcl(
        out_dir / "compare.tcl", verilog, design.name, sdc, spef, liberty
    )

    ours_wns_ns = float(sta_ours.get("setup_wns_ps", 0.0)) / 1000.0
    result: Dict[str, Any] = {
        "design": design.name,
        "clock_period_ns": clock_period_ns,
        "ours": {
            "setup_wns_ns": ours_wns_ns,
            "hold_wns_ns": float(sta_ours.get("hold_wns_ps", 0.0)) / 1000.0,
            "setup_failing": (sta_ours.get("summary") or {}).get("setup_failing"),
            "hold_failing": (sta_ours.get("summary") or {}).get("hold_failing"),
            "critical_path": sta_ours.get("critical_path") or {},
        },
        "files": {
            "verilog": str(verilog),
            "sdc": str(sdc),
            "spef": str(spef),
            "tcl": str(tcl),
        },
        "opensta": {"available": False, "wns_ns": None, "skipped": True},
    }

    sta_bin = shutil.which("sta")
    if not sta_bin:
        result["opensta"]["reason"] = "sta binary not on PATH"
        _write_outputs(out_dir, result)
        return result

    if liberty is None or liberty.suffix == ".json":
        result["opensta"]["reason"] = "OpenSTA needs a .lib (not .lib.json); skip run"
        _write_outputs(out_dir, result)
        return result

    proc = subprocess.run(
        [sta_bin, tcl.as_posix()],
        capture_output=True,
        text=True,
        cwd=str(out_dir),
        check=False,
    )
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    (out_dir / "opensta.log").write_text(log, encoding="utf-8")
    wns = _parse_opensta_wns(log)
    result["opensta"] = {
        "available": True,
        "skipped": False,
        "returncode": proc.returncode,
        "wns_ns": wns,
        "delta_wns_ns": None if wns is None else ours_wns_ns - wns,
    }
    _write_outputs(out_dir, result)
    return result


def _write_outputs(out_dir: Path, result: Dict[str, Any]) -> None:
    (out_dir / "compare.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    csv_path = out_dir / "compare.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["design", "ours_setup_wns_ns", "opensta_wns_ns", "delta_ns"])
        ost = result.get("opensta") or {}
        w.writerow(
            [
                result.get("design"),
                (result.get("ours") or {}).get("setup_wns_ns"),
                ost.get("wns_ns"),
                ost.get("delta_wns_ns"),
            ]
        )
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ours = (result.get("ours") or {}).get("setup_wns_ns")
        gold = (result.get("opensta") or {}).get("wns_ns")
        if ours is not None and gold is not None:
            fig, ax = plt.subplots(figsize=(4, 4))
            ax.scatter([gold], [ours], c="#2563eb")
            lo = min(float(gold), float(ours))
            hi = max(float(gold), float(ours))
            pad = max(0.05, 0.1 * (hi - lo or 0.1))
            ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], "--", color="#94a3b8")
            ax.set_xlabel("OpenSTA WNS (ns)")
            ax.set_ylabel("This checker WNS (ns)")
            ax.set_title(str(result.get("design")))
            fig.tight_layout()
            fig.savefig(out_dir / "wns_scatter.png", dpi=120)
            plt.close(fig)
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Correlate in-process STA vs OpenSTA")
    p.add_argument("--checkpoint", type=Path, default=None, help="DesignObject .pkl")
    p.add_argument("--run-dir", type=Path, default=None, help="Pipeline run directory")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--clock-period-ns", type=float, default=10.0)
    p.add_argument("--config", type=Path, default=None)
    args = p.parse_args(argv)

    ckpt = args.checkpoint
    if ckpt is None and args.run_dir is not None:
        ckpt = _find_checkpoint(args.run_dir)
    if ckpt is None:
        p.error("provide --checkpoint or --run-dir with a routing pickle")
    design = DesignObject.load_checkpoint(ckpt)
    out = args.out or (ckpt.parent.parent / "opensta_compare")
    config = load_config(args.config)
    result = compare(design, out, args.clock_period_ns, config)
    ost = result["opensta"]
    ours_s = result["ours"]["setup_wns_ns"]
    if ost.get("skipped"):
        sta_msg = f"skipped ({ost.get('reason', 'n/a')})"
    else:
        sta_msg = f"WNS {ost.get('wns_ns')}"
    print(f"{result['design']}: ours setup WNS {ours_s:.4f} ns; OpenSTA {sta_msg}")
    print(f"Wrote {out / 'compare.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
