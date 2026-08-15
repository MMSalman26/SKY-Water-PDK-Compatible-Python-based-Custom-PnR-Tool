# 32-bit ALU (SkyWater / OpenLane gate-level netlist)

Source: [HafizMutahirAhmed/ASIC-ALU-OpenLane](https://github.com/HafizMutahirAhmed/ASIC-ALU-OpenLane)

Netlist path in upstream repo:

`runs/RUN_2025.09.18_17.28.22/results/final/verilog/gl/ALU.v`

## Notes

- Mapped to `sky130_fd_sc_hd` (plus physical `sky130_ef_sc_hd__decap_12` fillers in the original file).
- This framework **strips** fill / decap / tap cells and power pins (`VGND`/`VPWR`/…) so the logic cone (~640 instances) can be placed/routed with the cached HD library.
- Bus ports are expanded to bits (`a[31:0]`, `b[31:0]`, `result[31:0]`, `alu_op[2:0]`).

## Run

```powershell
python -m pnr_tool fetch-pdk
python -m pnr_tool run --netlist designs\alu\ALU.v --top ALU --clock-period-ns 10 --out runs\alu
python -m pnr_tool html-report
```

QoR: `runs/alu/ALU.qor.json`  
Dashboard: `runs/index.html`
