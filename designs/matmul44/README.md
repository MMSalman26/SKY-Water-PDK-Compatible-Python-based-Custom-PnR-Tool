# 4×4 matrix multiply (2-bit, combinational)

Unsigned 4×4 × 4×4 → 4×4. Each element is 2 bits; each output is 6 bits (`3*3*4 = 36`). No flip-flops.

```text
python -m pnr_tool synth --rtl designs/matmul44/matmul44.v --top matmul44 --config designs/matmul44/config.yaml --out designs/matmul44/matmul44.gl.v
```

ABC is limited to `inv` / `and2` / `or2` / `nand2` / `nor2` / `xor2` / `xnor2`. Do not run DFT or PnR on this example unless you choose to later.
