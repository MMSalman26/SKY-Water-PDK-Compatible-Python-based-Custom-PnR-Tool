// Many flops on one clock — forces a multi-level CTS tree when max_fanout is small.
module golden_many_ff (
    input  clk,
    input  d,
    output q0,
    output q1,
    output q2,
    output q3,
    output q4,
    output q5,
    output q6,
    output q7,
    output q8,
    output q9,
    output q10,
    output q11,
    output q12,
    output q13,
    output q14,
    output q15,
    output q16,
    output q17,
    output q18,
    output q19,
    output q20,
    output q21,
    output q22,
    output q23,
    output q24,
    output q25,
    output q26,
    output q27,
    output q28,
    output q29,
    output q30,
    output q31
);
    sky130_fd_sc_hd__dfxtp_1 ff0  (.CLK(clk), .D(d),   .Q(q0));
    sky130_fd_sc_hd__dfxtp_1 ff1  (.CLK(clk), .D(q0),  .Q(q1));
    sky130_fd_sc_hd__dfxtp_1 ff2  (.CLK(clk), .D(q1),  .Q(q2));
    sky130_fd_sc_hd__dfxtp_1 ff3  (.CLK(clk), .D(q2),  .Q(q3));
    sky130_fd_sc_hd__dfxtp_1 ff4  (.CLK(clk), .D(q3),  .Q(q4));
    sky130_fd_sc_hd__dfxtp_1 ff5  (.CLK(clk), .D(q4),  .Q(q5));
    sky130_fd_sc_hd__dfxtp_1 ff6  (.CLK(clk), .D(q5),  .Q(q6));
    sky130_fd_sc_hd__dfxtp_1 ff7  (.CLK(clk), .D(q6),  .Q(q7));
    sky130_fd_sc_hd__dfxtp_1 ff8  (.CLK(clk), .D(q7),  .Q(q8));
    sky130_fd_sc_hd__dfxtp_1 ff9  (.CLK(clk), .D(q8),  .Q(q9));
    sky130_fd_sc_hd__dfxtp_1 ff10 (.CLK(clk), .D(q9),  .Q(q10));
    sky130_fd_sc_hd__dfxtp_1 ff11 (.CLK(clk), .D(q10), .Q(q11));
    sky130_fd_sc_hd__dfxtp_1 ff12 (.CLK(clk), .D(q11), .Q(q12));
    sky130_fd_sc_hd__dfxtp_1 ff13 (.CLK(clk), .D(q12), .Q(q13));
    sky130_fd_sc_hd__dfxtp_1 ff14 (.CLK(clk), .D(q13), .Q(q14));
    sky130_fd_sc_hd__dfxtp_1 ff15 (.CLK(clk), .D(q14), .Q(q15));
    sky130_fd_sc_hd__dfxtp_1 ff16 (.CLK(clk), .D(q15), .Q(q16));
    sky130_fd_sc_hd__dfxtp_1 ff17 (.CLK(clk), .D(q16), .Q(q17));
    sky130_fd_sc_hd__dfxtp_1 ff18 (.CLK(clk), .D(q17), .Q(q18));
    sky130_fd_sc_hd__dfxtp_1 ff19 (.CLK(clk), .D(q18), .Q(q19));
    sky130_fd_sc_hd__dfxtp_1 ff20 (.CLK(clk), .D(q19), .Q(q20));
    sky130_fd_sc_hd__dfxtp_1 ff21 (.CLK(clk), .D(q20), .Q(q21));
    sky130_fd_sc_hd__dfxtp_1 ff22 (.CLK(clk), .D(q21), .Q(q22));
    sky130_fd_sc_hd__dfxtp_1 ff23 (.CLK(clk), .D(q22), .Q(q23));
    sky130_fd_sc_hd__dfxtp_1 ff24 (.CLK(clk), .D(q23), .Q(q24));
    sky130_fd_sc_hd__dfxtp_1 ff25 (.CLK(clk), .D(q24), .Q(q25));
    sky130_fd_sc_hd__dfxtp_1 ff26 (.CLK(clk), .D(q25), .Q(q26));
    sky130_fd_sc_hd__dfxtp_1 ff27 (.CLK(clk), .D(q26), .Q(q27));
    sky130_fd_sc_hd__dfxtp_1 ff28 (.CLK(clk), .D(q27), .Q(q28));
    sky130_fd_sc_hd__dfxtp_1 ff29 (.CLK(clk), .D(q28), .Q(q29));
    sky130_fd_sc_hd__dfxtp_1 ff30 (.CLK(clk), .D(q29), .Q(q30));
    sky130_fd_sc_hd__dfxtp_1 ff31 (.CLK(clk), .D(q30), .Q(q31));
endmodule
