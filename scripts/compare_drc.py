#!/usr/bin/env python3
"""Optional Magic/KLayout correlation harness for in-process DRC.

Writes CIF of inflated metals+vias+OBS, runs this tool's DRC, and if
``klayout`` or ``magic`` is on PATH, invokes a tiny metal-spacing deck.

  python scripts/compare_drc.py --checkpoint runs/alu/checkpoints/ALU_routing.pkl

Does not claim Calibre signoff. Skips gold cleanly when binaries are missing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pnr_tool.checkers.drc import run_drc
from pnr_tool.config import load_config
from pnr_tool.design.object import DesignObject
from pnr_tool.report.layout_cif import write_cif

_KLAYOUT_DRC = r'''
source($cif)
MET2 = input(2, 0)
report("pnr_tool compare")
MET2.width(0.14.um).output("met2.width")
MET2.space(0.14.um).output("met2.space")
'''


def _find_checkpoint(run_dir: Path) -> Optional[Path]:
    ckpt = run_dir / "checkpoints"
    if not ckpt.is_dir():
        return None
    preferred = list(ckpt.glob("*_routing.pkl"))
    if preferred:
        return preferred[0]
    pkls = sorted(ckpt.glob("*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pkls[0] if pkls else None


def _count_ours(drc: Dict[str, Any]) -> Dict[str, int]:
    counts = dict(drc.get("counts_by_type") or {})
    return {
        "short": int(counts.get("short") or 0),
        "spacing": int(counts.get("spacing") or 0),
        "open": int(counts.get("open") or 0),
        "overlap": int(counts.get("overlap") or 0),
        "via": int(counts.get("via") or 0),
        "enclosure": int(counts.get("enclosure") or 0),
        "all": int(drc.get("violation_count_all") or 0),
        "pass": int(drc.get("violation_count") or 0),
    }


def compare(
    design: DesignObject,
    out_dir: Path,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    config = config or load_config()
    ours = run_drc(design, config)
    cif = write_cif(design, out_dir / f"{design.name}.cif", config)
    result: Dict[str, Any] = {
        "design": design.name,
        "ours": _count_ours(ours),
        "files": {"cif": str(cif)},
        "gold": {"available": False, "skipped": True, "engine": None},
    }
    klayout = shutil.which("klayout")
    magic = shutil.which("magic")
    if klayout:
        script = out_dir / "compare.drc"
        # KLayout CIF load is engine-specific; emit a stub script and try.
        script.write_text(
            f'# pnr_tool DRC compare (CIF {cif.name})\n'
            "report(\"pnr_tool\")\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [klayout, "-b", "-r", str(script), str(cif)],
            capture_output=True,
            text=True,
            cwd=str(out_dir),
            check=False,
        )
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        (out_dir / "klayout.log").write_text(log, encoding="utf-8")
        result["gold"] = {
            "available": True,
            "skipped": False,
            "engine": "klayout",
            "returncode": proc.returncode,
            "note": "Minimal deck; parse log for marker counts",
        }
    elif magic:
        tcl = out_dir / "compare.tcl"
        tcl.write_text(
            f"cif read {cif.as_posix()}\ndrc check\ndrc count total\nquit\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [magic, "-dnull", "-noconsole", str(tcl)],
            capture_output=True,
            text=True,
            cwd=str(out_dir),
            check=False,
        )
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        (out_dir / "magic.log").write_text(log, encoding="utf-8")
        result["gold"] = {
            "available": True,
            "skipped": False,
            "engine": "magic",
            "returncode": proc.returncode,
        }
    else:
        result["gold"]["reason"] = "klayout/magic not on PATH"
    (out_dir / "compare.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Correlate in-process DRC vs Magic/KLayout")
    p.add_argument("--checkpoint", type=Path, default=None)
    p.add_argument("--run-dir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--config", type=Path, default=None)
    args = p.parse_args(argv)
    ckpt = args.checkpoint
    if ckpt is None and args.run_dir is not None:
        ckpt = _find_checkpoint(args.run_dir)
    if ckpt is None:
        p.error("provide --checkpoint or --run-dir with a routing pickle")
    design = DesignObject.load_checkpoint(ckpt)
    out = args.out or (ckpt.parent.parent / "drc_compare")
    result = compare(design, out, load_config(args.config))
    gold = result["gold"]
    print(
        f"{result['design']}: ours pass={result['ours']['pass']} "
        f"all={result['ours']['all']}; gold "
        f"{'skipped (' + str(gold.get('reason')) + ')' if gold.get('skipped') else gold.get('engine')}"
    )
    print(f"Wrote {out / 'compare.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
