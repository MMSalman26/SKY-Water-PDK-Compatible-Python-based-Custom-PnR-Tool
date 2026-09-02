# 4:1 MUX (RTL)

Tiny combinational example for ``python -m pnr_tool synth``. DFT on this netlist is a no-op (no flops).

```text
python -m pnr_tool synth --rtl designs/mux41/mux41.v --top mux41 --out designs/mux41/mux41.gl.v
python -m pnr_tool dft --netlist designs/mux41/mux41.gl.v --top mux41 --out runs/mux41/mux41.dft.v
```

Needs Yosys on PATH, or ``pip install yowasp-yosys``. PDK cache from ``fetch-pdk``.
