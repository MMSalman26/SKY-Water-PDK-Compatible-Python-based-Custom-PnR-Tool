# 4:1 MUX (RTL)

Tiny combinational example for ``python -m pnr_tool synth``.

```text
python -m pnr_tool synth --rtl designs/mux41/mux41.v --top mux41 --out designs/mux41/mux41.gl.v
```

Needs Yosys on PATH, or ``pip install yowasp-yosys``. PDK cache from ``fetch-pdk``.
