// Yosys techmap: generic gates → sky130_fd_sc_hd 2-input AND/OR/NOT/NAND/NOR/XOR/XNOR.

module \$_NOT_ (A, Y);
    input A;
    output Y;
    sky130_fd_sc_hd__inv_1 _t (.A(A), .Y(Y));
endmodule

module \$_AND_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__and2_1 _t (.A(A), .B(B), .X(Y));
endmodule

module \$_OR_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__or2_1 _t (.A(A), .B(B), .X(Y));
endmodule

module \$_NAND_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__nand2_1 _t (.A(A), .B(B), .Y(Y));
endmodule

module \$_NOR_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__nor2_1 _t (.A(A), .B(B), .Y(Y));
endmodule

module \$_XOR_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__xor2_1 _t (.A(A), .B(B), .X(Y));
endmodule

module \$_XNOR_ (A, B, Y);
    input A, B;
    output Y;
    sky130_fd_sc_hd__xnor2_1 _t (.A(A), .B(B), .X(Y));
endmodule

module \$_ANDNOT_ (A, B, Y);
    input A, B;
    output Y;
    wire bn;
    sky130_fd_sc_hd__inv_1 _i (.A(B), .Y(bn));
    sky130_fd_sc_hd__and2_1 _a (.A(A), .B(bn), .X(Y));
endmodule

module \$_ORNOT_ (A, B, Y);
    input A, B;
    output Y;
    wire bn;
    sky130_fd_sc_hd__inv_1 _i (.A(B), .Y(bn));
    sky130_fd_sc_hd__or2_1 _o (.A(A), .B(bn), .X(Y));
endmodule

module \$_MUX_ (A, B, S, Y);
    input A, B, S;
    output Y;
    wire ns, t0, t1;
    sky130_fd_sc_hd__inv_1 _i (.A(S), .Y(ns));
    sky130_fd_sc_hd__and2_1 _a0 (.A(A), .B(ns), .X(t0));
    sky130_fd_sc_hd__and2_1 _a1 (.A(B), .B(S), .X(t1));
    sky130_fd_sc_hd__or2_1 _o (.A(t0), .B(t1), .X(Y));
endmodule
