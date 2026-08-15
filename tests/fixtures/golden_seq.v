// Tiny sequential golden: one flop + one inverter on D path
module golden_seq (
    input  clk,
    input  d,
    output q
);
    wire n1;

    sky130_fd_sc_hd__inv_2 u_inv (
        .A(d),
        .Y(n1)
    );
    sky130_fd_sc_hd__dfxtp_1 u_ff (
        .CLK(clk),
        .D(n1),
        .Q(q)
    );
endmodule
