"""Generate a medium-scale structural Verilog fixture (~2k instances)."""

from __future__ import annotations

from pathlib import Path


def generate(n_stages: int = 40, width: int = 50) -> str:
    """width * n_stages combinational + width inverters + width flops."""
    ports_in = [f"din_{i}" for i in range(width)]
    ports_out = [f"dout_{i}" for i in range(width)]

    lines = [
        "// Auto-generated medium netlist for PnR framework regression",
        f"// stages={n_stages} width={width}",
        "module medium_design (",
        "    input  clk,",
    ]
    for p in ports_in:
        lines.append(f"    input  {p},")
    for i, p in enumerate(ports_out):
        comma = "," if i < len(ports_out) - 1 else ""
        lines.append(f"    output {p}{comma}")
    lines.append(");")
    lines.append("")

    for s in range(n_stages + 1):
        for i in range(width):
            lines.append(f"    wire s{s}_{i};")
    lines.append("")

    for i in range(width):
        lines.append(
            f"    sky130_fd_sc_hd__inv_2 u_in_{i} (.A(din_{i}), .Y(s0_{i}));"
        )
    lines.append("")

    for s in range(n_stages):
        nxt = s + 1
        for i in range(width):
            j = (i + 1) % width
            cell_sel = (s + i) % 3
            name = f"u_s{s}_{i}"
            if cell_sel == 0:
                lines.append(
                    f"    sky130_fd_sc_hd__nand2_1 {name} ("
                    f".A(s{s}_{i}), .B(s{s}_{j}), .Y(s{nxt}_{i}));"
                )
            elif cell_sel == 1:
                lines.append(
                    f"    sky130_fd_sc_hd__nor2_1 {name} ("
                    f".A(s{s}_{i}), .B(s{s}_{j}), .Y(s{nxt}_{i}));"
                )
            else:
                lines.append(
                    f"    sky130_fd_sc_hd__inv_2 {name} ("
                    f".A(s{s}_{i}), .Y(s{nxt}_{i}));"
                )
        lines.append("")

    last = n_stages
    for i in range(width):
        lines.append(
            f"    sky130_fd_sc_hd__dfxtp_1 u_ff_{i} ("
            f".CLK(clk), .D(s{last}_{i}), .Q(dout_{i}));"
        )

    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    text = generate(n_stages=40, width=50)
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "medium_design.v"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    count = text.count("sky130_fd_sc_hd__")
    print(f"Wrote {out} ({count} instances)")


if __name__ == "__main__":
    main()
