"""Download the OpenLane-synthesized PicoRV32a gate-level netlist."""

from __future__ import annotations

import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "designs" / "picorv32a" / "picorv32a.synthesis.v"
README = ROOT / "designs" / "picorv32a" / "README.md"

# VSD OpenLane workshop artifact (PicoRV32 RTL is ISC-licensed).
NETLIST_URL = (
    "https://raw.githubusercontent.com/ABHIMR1502/Digital-SoC-Design/"
    "main/DAY1/picorv32a.synthesis.v"
)
# Upstream file is ~1.89 MB; reject obviously truncated downloads.
MIN_BYTES = 1_000_000

README_TEXT = """# PicoRV32a (SkyWater / OpenLane gate-level netlist)

Challenge design: OpenLane-synthesized [PicoRV32](https://github.com/YosysHQ/picorv32)
RISC-V core mapped to `sky130_fd_sc_hd`.

| Metric | Value |
| --- | --- |
| Cells (post-synth) | 14,876 |
| Flip-flops (`dfxtp_2`) | 1,613 |
| Distinct cell types | ~58 |
| Logic area (yosys) | ~147,713 um² |

## Source

Netlist artifact from the VSD OpenLane workshop:

- Repository: [ABHIMR1502/Digital-SoC-Design](https://github.com/ABHIMR1502/Digital-SoC-Design)
- Path: `DAY1/picorv32a.synthesis.v`
- PicoRV32 RTL license: ISC (Clifford Wolf / YosysHQ)

Re-fetch:

```powershell
python scripts\\fetch_picorv32.py
```

## Run

Fetch PDK cells used by this netlist, then place/route:

```powershell
python -m pnr_tool fetch-pdk --netlist designs\\picorv32a\\picorv32a.synthesis.v
python -m pnr_tool run --netlist designs\\picorv32a\\picorv32a.synthesis.v --top picorv32a `
  --config designs\\picorv32a\\config.yaml --clock-period-ns 24 --out runs\\picorv32a
python -m pnr_tool html-report
```

**Runtime note:** ~23× more cells than the ALU. CTS (~1.6k sinks) and global routing
(~14.6k nets) dominate; DRC shorts at global-route fidelity are expected.

## Why this stresses STA / IR

- Deep multi-cycle CPU datapath → setup pressure across many endpoints.
- 1,613 flops → large clock tree (hold / CTS latency) and dense sequential current draw for IR.
"""


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "pnr-tool/0.1"})
    print(f"Downloading {url}")
    with urllib.request.urlopen(req, context=ctx, timeout=120.0) as resp:
        data = resp.read()
    if len(data) < MIN_BYTES:
        raise RuntimeError(
            f"Downloaded file too small ({len(data)} bytes); expected >= {MIN_BYTES}"
        )
    dest.write_bytes(data)
    print(f"Wrote {dest} ({len(data):,} bytes)")


def main() -> int:
    if DEST.exists() and DEST.stat().st_size >= MIN_BYTES:
        print(f"Already present: {DEST} ({DEST.stat().st_size:,} bytes)")
    else:
        try:
            _download(NETLIST_URL, DEST)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, RuntimeError) as exc:
            print(f"ERROR: failed to fetch PicoRV32 netlist: {exc}", file=sys.stderr)
            return 1

    README.write_text(README_TEXT, encoding="utf-8")
    print(f"Wrote {README}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
