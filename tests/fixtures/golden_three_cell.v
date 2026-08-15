// Hand-calculable golden: 2 inverters + 1 NAND2 (combinational)
// Used to validate elaborator, placement overlaps math, STA/IR smoke.
module golden_three_cell (
    input  A,
    input  B,
    output Y
);
    wire n1;
    wire n2;

    sky130_fd_sc_hd__inv_2  u_inv0 (
        .A(A),
        .Y(n1)
    );
    sky130_fd_sc_hd__inv_2  u_inv1 (
        .A(B),
        .Y(n2)
    );
    sky130_fd_sc_hd__nand2_1 u_nand (
        .A(n1),
        .B(n2),
        .Y(Y)
    );
endmodule
