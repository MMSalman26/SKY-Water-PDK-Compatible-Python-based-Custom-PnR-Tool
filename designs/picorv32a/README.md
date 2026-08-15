# PicoRV32a (SkyWater / OpenLane gate-level netlist)

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
python scripts\fetch_picorv32.py
```

## Run

Fetch PDK cells used by this netlist, then run the OpenLane-order flow
(Power → Place → CTS → Route) with default OpenROAD-inspired engines:

```powershell
python -m pnr_tool fetch-pdk --netlist designs\picorv32a\picorv32a.synthesis.v
python -m pnr_tool run --netlist designs\picorv32a\picorv32a.synthesis.v --top picorv32a `
  --config designs\picorv32a\config.yaml --clock-period-ns 24 --out runs\picorv32a
python -m pnr_tool html-report
```

Use [`config.yaml`](config.yaml) for a capped A* `step_budget` suitable for ~15k nets.

**Runtime note:** ~23× more cells than the ALU. CTS (~1.6k sinks) and global routing
(~14.6k nets) dominate; DRC shorts at global-route fidelity are expected.

## Why this stresses STA / IR

- Deep multi-cycle CPU datapath → setup pressure across many endpoints.
- 1,613 flops → large clock tree (hold / CTS latency) and dense sequential current draw for IR.
