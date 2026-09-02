"""Wide, high-contrast SVGs for designs/matmul44/explain.html."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = Path(__file__).resolve().parent / "explain_assets"
INK = "#0b1220"
CREAM = "#f6f1e8"
MUTED = "#d9d2c6"
CYAN = "#6eebf0"
AMBER = "#ffc14a"
ROSE = "#f08a70"
BLUE = "#9ad4f5"
PANEL = "#1a2433"
LINE = "#4a5d78"
ROWS = [CYAN, AMBER, ROSE, BLUE]


def _fig(w: float, h: float):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(INK)
    ax.set_facecolor(INK)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, h / w * 12)
    ax.set_axis_off()
    return fig, ax


def _box(ax, x, y, w, h, fc, ec=None, lw=1.6, alpha=1.0):
    p = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.03,rounding_size=0.12",
        facecolor=fc,
        edgecolor=ec or fc,
        linewidth=lw,
        alpha=alpha,
    )
    ax.add_patch(p)
    return p


def _save(fig, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        OUT / name,
        format="svg",
        facecolor=INK,
        edgecolor=INK,
        bbox_inches="tight",
        pad_inches=0.18,
    )
    plt.close(fig)


def packing():
    fig, ax = _fig(12.2, 6.85)
    ymax = ax.get_ylim()[1]

    ax.text(0.25, ymax - 0.22, "Port packing — why a is 32 bits and c is 96 bits", fontsize=17, color=CREAM, fontweight="bold", va="top")
    ax.text(
        0.25,
        ymax - 0.58,
        "Three wires, not 48 pins.  Each A/B cell = 2 bits.  Each C cell = 6 bits because the largest sum is 3×3×4 = 36.",
        fontsize=12,
        color=MUTED,
        va="top",
    )

    def row_bus(y, nbits, bits_per, name, formula_hint):
        ax.text(0.25, y + 1.42, name, fontsize=13, color=CYAN, fontweight="bold")
        ax.text(0.25, y + 1.12, formula_hint, fontsize=12, color=AMBER, fontweight="medium")
        left, width = 0.25, 11.5
        gap = 0.12
        rw = (width - 3 * gap) / 4
        for r in range(4):
            x = left + r * (rw + gap)
            _box(ax, x, y + 0.18, rw, 0.86, ROWS[r], lw=0)
            lo = r * 4 * bits_per
            hi = lo + 4 * bits_per - 1
            ax.text(x + rw / 2, y + 0.72, f"row {r}", ha="center", va="center", fontsize=16, color=INK, fontweight="bold")
            ax.text(
                x + rw / 2,
                y + 0.38,
                f"[{r},0] … [{r},3]     bits {hi}:{lo}",
                ha="center",
                va="center",
                fontsize=12.5,
                color=INK,
                fontweight="medium",
            )
        ax.text(0.25, y, "bit 0  starts here  (row 0, col 0)", fontsize=10.5, color=MUTED, va="top")
        ax.text(11.75, y, f"bit {nbits - 1}  ends here  (row 3, col 3)", fontsize=10.5, color=MUTED, ha="right", va="top")

    row_bus(
        y=3.72,
        nbits=32,
        bits_per=2,
        name="a[31:0]  and  b[31:0]     ·     16 cells × 2 bits = 32 bits",
        formula_hint="A[row][col]  lives in  a[ row×8 + col×2  +: 2 ]",
    )
    row_bus(
        y=1.88,
        nbits=96,
        bits_per=6,
        name="c[95:0]     ·     16 cells × 6 bits = 96 bits",
        formula_hint="C[row][col]  lives in  c[ row×24 + col×6  +: 6 ]",
    )

    cards = [
        (0.25, CYAN, "A  input", "A[gi][k]\na[(gi×8) + k×2  +: 2]\n2 bits, row-major"),
        (4.15, AMBER, "B  input", "B[k][gj]\nb[(k×8) + gj×2  +: 2]\n2 bits, same tiling"),
        (8.05, ROSE, "C  output", "C[gi][gj]\nc[(gi×24) + gj×6 +: 6]\n6 bits, holds 0…36"),
    ]
    for x, col, title, body in cards:
        _box(ax, x, 0.12, 3.7, 1.52, col, lw=0)
        ax.text(x + 1.85, 1.38, title, ha="center", va="center", fontsize=13, color=INK, fontweight="bold")
        ax.text(x + 1.85, 0.68, body, ha="center", va="center", fontsize=11.5, color=INK, linespacing=1.45)

    _save(fig, "packing.svg")


def adder_widths():
    fig, ax = _fig(12.2, 5.35)
    ymax = ax.get_ylim()[1]

    ax.text(0.25, ymax - 0.26, "One C[i][j] datapath — the bit-width staircase", fontsize=16, color=CREAM, fontweight="bold", va="top")
    ax.text(
        0.25,
        ymax - 0.60,
        "Hardware never uses  *  or  + .  mul2 is AND/XOR.  add_rc is AND/OR/XOR ripple.  Demo C[0][0] = 1×1 + 2×1 + 3×2 + 0×3 = 9.",
        fontsize=11.5,
        color=MUTED,
        va="top",
    )

    stages = [
        (0.20, MUTED, "1. Slice", "2-bit", "a0…a3  from row i\nb0…b3  from col j"),
        (2.55, CYAN, "2. mul2 × 4", "4-bit", "each product 0…9\ndemo:  1, 2, 6, 0"),
        (4.90, AMBER, "3. add W=4", "5-bit", "s01 = p0 + p1\ndemo:  1 + 2 = 3"),
        (7.25, ROSE, "4. add W=5", "6-bit", "s012 = s01 + p2\ndemo:  3 + 6 = 9"),
        (9.60, CYAN, "5. add W=6", "6-bit C", "keep [5:0] of the sum\ndemo:  9 + 0 = 9"),
    ]
    box_w, box_h, y = 2.15, 2.55, 1.55
    for x, col, title, bits, body in stages:
        _box(ax, x, y, box_w, box_h, col, lw=0)
        ax.text(x + box_w / 2, y + box_h - 0.32, title, ha="center", va="center", fontsize=11.5, color=INK, fontweight="bold")
        ax.text(x + box_w / 2, y + 1.45, bits, ha="center", va="center", fontsize=22, color=INK, fontweight="bold")
        ax.text(x + box_w / 2, y + 0.55, body, ha="center", va="center", fontsize=11.5, color=INK, linespacing=1.4)

    for i in range(len(stages) - 1):
        x0 = stages[i][0] + box_w
        x1 = stages[i + 1][0]
        ax.add_patch(
            FancyArrowPatch(
                (x0 + 0.02, y + box_h / 2),
                (x1 - 0.02, y + box_h / 2),
                arrowstyle="-|>",
                mutation_scale=12,
                lw=1.4,
                color=CREAM,
            )
        )

    ax.text(
        6.0,
        0.55,
        "Widths grow just enough for the worst case:  9+9=18 needs 5 bits,  then +9 needs 6 bits,  then 36 still fits in 6 bits.",
        ha="center",
        va="center",
        fontsize=11.5,
        color=CREAM,
    )
    ax.text(
        6.0,
        0.22,
        "Sixteen independent copies of this chain fill the 4×4 result.  No clock.",
        ha="center",
        va="center",
        fontsize=11,
        color=MUTED,
    )
    _save(fig, "adder_widths.svg")


def instances():
    fig, ax = _fig(12.2, 4.85)
    ymax = ax.get_ylim()[1]
    total = 1408

    ax.text(0.25, ymax - 0.26, "After Yosys  —  1,408 combinational cells  (limit 1,500)", fontsize=16, color=CREAM, fontweight="bold", va="top")
    ax.text(
        0.25,
        ymax - 0.60,
        "Mapped to AND / XOR / OR only.  There are no flip-flops, so a scan-chain DFT pass would stitch nothing.",
        fontsize=11.5,
        color=MUTED,
        va="top",
    )

    cards = [
        (0.25, CYAN, "752", "and2_1", "53%   ·   every partial product\nand every generate bit"),
        (4.15, AMBER, "512", "xor2_1", "36%   ·   sum bits in mul2\nand propagate in add_rc"),
        (8.05, ROSE, "144", "or2_1", "10%   ·   carry  g ∨ (p ∧ cin)\nin the ripple adders"),
    ]
    for x, col, n, name, why in cards:
        _box(ax, x, 1.55, 3.7, 2.15, col, lw=0)
        ax.text(x + 1.85, 3.22, n, ha="center", va="center", fontsize=32, color=INK, fontweight="bold")
        ax.text(x + 1.85, 2.55, name, ha="center", va="center", fontsize=14, color=INK, fontweight="bold")
        ax.text(x + 1.85, 1.95, why, ha="center", va="center", fontsize=11, color=INK, linespacing=1.4)

    # stacked proportion bar
    ax.text(0.25, 1.22, "Share of the netlist", fontsize=11, color=MUTED)
    x0, bar_w, bar_h, by = 0.25, 11.5, 0.42, 0.62
    shares = [(752, CYAN), (512, AMBER), (144, ROSE)]
    cursor = x0
    for n, col in shares:
        w = bar_w * n / total
        _box(ax, cursor, by, w - 0.03, bar_h, col, lw=0)
        if w > 1.4:
            ax.text(cursor + w / 2, by + bar_h / 2, f"{n}", ha="center", va="center", fontsize=12, color=INK, fontweight="bold")
        cursor += w

    ax.text(
        0.25,
        0.22,
            "No clocks.  No flip-flops.  Stop after synth — this netlist is for teaching, not a PnR run.",
            fontsize=12,
            color=CREAM,
        va="center",
    )
    _save(fig, "instances.svg")


def main() -> None:
    packing()
    adder_widths()
    instances()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
