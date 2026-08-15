#!/usr/bin/env python3
"""Optional ngspice/OpenROAD correlation harness for in-process static IR.

Writes SPICE (PDN R + V + I) and instance currents from a routed DesignObject,
runs this tool's IR checker, and if ``ngspice`` or ``openroad`` is on PATH,
tries a DC operating-point (or records that PDNSim is not auto-driven).

  python scripts/compare_ir.py --checkpoint runs/alu/checkpoints/ALU_routing.pkl

Does not claim Voltus signoff. Skips gold cleanly when binaries are missing.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pnr_tool.checkers.ir_drop import run_ir_drop, write_ir_spice
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject


def _find_checkpoint(run_dir: Path) -> Optional[Path]:
    ckpt = run_dir / "checkpoints"
    if not ckpt.is_dir():
        return None
    preferred = list(ckpt.glob("*_routing.pkl"))
    if preferred:
        return preferred[0]
    pkls = sorted(ckpt.glob("*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pkls[0] if pkls else None


def _parse_ngspice_voltages(text: str) -> Dict[str, float]:
    """Parse ``v(n12) = 1.79`` / ``n12 = 1.79e+00`` style .op dumps."""
    out: Dict[str, float] = {}
    for m in re.finditer(
        r"v\(\s*(n\d+)\s*\)\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
        text,
        re.IGNORECASE,
    ):
        out[m.group(1).lower()] = float(m.group(2))
    if out:
        return out
    for m in re.finditer(
        r"\b(n\d+)\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
        text,
        re.IGNORECASE,
    ):
        out[m.group(1).lower()] = float(m.group(2))
    return out


def compare(
    design: DesignObject,
    out_dir: Path,
    config: Optional[Dict[str, Any]] = None,
    clock_period_ns: float = 10.0,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    config = config or load_config()
    ours = run_ir_drop(design, config, clock_period_ns=clock_period_ns)
    spice_path = write_ir_spice(ours, out_dir / f"{design.name}_ir.sp")
    currents_path = out_dir / f"{design.name}_ir_currents.json"
    heat = list(ours.get("instance_heatmap") or [])
    currents_path.write_text(
        json.dumps(
            {
                "total_a": ours.get("total_current_a"),
                "currents": ours.get("currents"),
                "instances": heat,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result: Dict[str, Any] = {
        "design": design.name,
        "ours": {
            "min_voltage": ours.get("min_voltage"),
            "min_voltage_raw": ours.get("min_voltage_raw"),
            "max_ir_drop": ours.get("max_ir_drop"),
            "max_supply_collapse": ours.get("max_supply_collapse"),
            "source_type": ours.get("source_type"),
            "corner": ours.get("corner"),
            "instances_affected": ours.get("instances_affected"),
            "solver_residual": ours.get("solver_residual"),
            "max_j_ma_per_um": ours.get("max_j_ma_per_um"),
        },
        "files": {
            "spice": str(spice_path),
            "currents": str(currents_path),
        },
        "gold": {"available": False, "skipped": True, "engine": None},
    }

    ngspice = shutil.which("ngspice")
    openroad = shutil.which("openroad")
    if ngspice:
        proc = subprocess.run(
            [ngspice, "-b", str(spice_path)],
            capture_output=True,
            text=True,
            cwd=str(out_dir),
            check=False,
        )
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        (out_dir / "ngspice.log").write_text(log, encoding="utf-8")
        volts = _parse_ngspice_voltages(log)
        gold_min = min(volts.values()) if volts else None
        ours_min = ours.get("min_voltage")
        result["gold"] = {
            "available": True,
            "skipped": False,
            "engine": "ngspice",
            "returncode": proc.returncode,
            "n_voltages": len(volts),
            "min_voltage": gold_min,
            "delta_min_v": None
            if gold_min is None or ours_min is None
            else float(ours_min) - float(gold_min),
        }
    elif openroad:
        proc = subprocess.run(
            [openroad, "-version"],
            capture_output=True,
            text=True,
            cwd=str(out_dir),
            check=False,
        )
        (out_dir / "openroad.log").write_text(
            (proc.stdout or "") + "\n" + (proc.stderr or ""),
            encoding="utf-8",
        )
        result["gold"] = {
            "available": True,
            "skipped": True,
            "engine": "openroad",
            "reason": "openroad on PATH but no automated PDNSim deck; SPICE written for manual psm",
            "returncode": proc.returncode,
        }
    else:
        result["gold"]["reason"] = "ngspice/openroad not on PATH"

    _write_outputs(out_dir, result, heat)
    return result


def _write_outputs(out_dir: Path, result: Dict[str, Any], heat: List[dict]) -> None:
    (out_dir / "compare.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    csv_path = out_dir / "compare.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["design", "ours_max_ir_drop", "ours_min_v", "gold_engine", "gold_min_v"])
        gold = result.get("gold") or {}
        ours = result.get("ours") or {}
        w.writerow(
            [
                result.get("design"),
                ours.get("max_ir_drop"),
                ours.get("min_voltage"),
                gold.get("engine"),
                gold.get("min_voltage"),
            ]
        )
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        ours_v = (result.get("ours") or {}).get("min_voltage")
        gold_v = (result.get("gold") or {}).get("min_voltage")
        if ours_v is not None and gold_v is not None:
            fig, ax = plt.subplots(figsize=(4, 4))
            ax.scatter([gold_v], [ours_v], c="#2563eb")
            lo = min(float(gold_v), float(ours_v))
            hi = max(float(gold_v), float(ours_v))
            pad = max(0.01, 0.1 * (hi - lo or 0.05))
            ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], "--", color="#94a3b8")
            ax.set_xlabel("Gold min V (ngspice)")
            ax.set_ylabel("This checker min V")
            ax.set_title(str(result.get("design")))
            fig.tight_layout()
            fig.savefig(out_dir / "ir_scatter.png", dpi=120)
            plt.close(fig)
        elif heat:
            fig, ax = plt.subplots(figsize=(4, 3.2))
            xs = [p.get("x") for p in heat]
            ys = [p.get("y") for p in heat]
            c = [p.get("drop_pct") for p in heat]
            ax.scatter(xs, ys, c=c, cmap="RdYlGn_r", s=18)
            ax.set_title("Instance drop % (this checker)")
            ax.set_aspect("equal", adjustable="datalim")
            fig.tight_layout()
            fig.savefig(out_dir / "ir_scatter.png", dpi=120)
            plt.close(fig)
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Correlate in-process IR vs ngspice/OpenROAD")
    p.add_argument("--checkpoint", type=Path, default=None)
    p.add_argument("--run-dir", type=Path, default=None)
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
    out = args.out or (ckpt.parent.parent / "ir_compare")
    result = compare(design, out, load_config(args.config), args.clock_period_ns)
    gold = result["gold"]
    ours = result["ours"]
    if gold.get("skipped"):
        gold_msg = f"skipped ({gold.get('reason', 'n/a')})"
    else:
        gold_msg = f"{gold.get('engine')} minV={gold.get('min_voltage')}"
    print(
        f"{result['design']}: ours max_ir={ours.get('max_ir_drop')} "
        f"minV={ours.get('min_voltage')}; gold {gold_msg}"
    )
    print(f"SPICE {result['files']['spice']}")
    print(f"Wrote {out / 'compare.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
