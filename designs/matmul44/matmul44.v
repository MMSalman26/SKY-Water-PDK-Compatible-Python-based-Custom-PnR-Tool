// Combinational 4x4 matrix multiply, 2-bit unsigned elements.
// C[i][j] = sum_k A[i][k] * B[k][j]  (max 3*3*4 = 36, packed in 6 bits)
//
// Assign-only AND/OR/NOT/NAND/NOR/XOR/XNOR (no *, +, ?:, no flops).
//
//   python -m pnr_tool synth --rtl designs/matmul44/matmul44.v --top matmul44 \
//       --config designs/matmul44/config.yaml --out designs/matmul44/matmul44.gl.v

module matmul44 (
    input  wire [31:0] a,
    input  wire [31:0] b,
    output wire [95:0] c
);
    genvar gi, gj;
    generate
        for (gi = 0; gi < 4; gi = gi + 1) begin : rows
            for (gj = 0; gj < 4; gj = gj + 1) begin : cols
                wire [1:0] a0, a1, a2, a3;
                wire [1:0] b0, b1, b2, b3;
                assign a0 = a[(gi * 8) +: 2];
                assign a1 = a[(gi * 8) + 2 +: 2];
                assign a2 = a[(gi * 8) + 4 +: 2];
                assign a3 = a[(gi * 8) + 6 +: 2];
                assign b0 = b[(gj * 2) +: 2];
                assign b1 = b[8 + (gj * 2) +: 2];
                assign b2 = b[16 + (gj * 2) +: 2];
                assign b3 = b[24 + (gj * 2) +: 2];

                wire [3:0] p0, p1, p2, p3;
                mul2 u_m0 (.x(a0), .y(b0), .p(p0));
                mul2 u_m1 (.x(a1), .y(b1), .p(p1));
                mul2 u_m2 (.x(a2), .y(b2), .p(p2));
                mul2 u_m3 (.x(a3), .y(b3), .p(p3));

                wire [4:0] s01;
                wire [5:0] s012;
                wire [6:0] s0123;
                add_rc #(.W(4)) u_a0 (.x(p0), .y(p1), .s(s01));
                add_rc #(.W(5)) u_a1 (.x(s01), .y({1'b0, p2}), .s(s012));
                add_rc #(.W(6)) u_a2 (.x(s012), .y({2'b00, p3}), .s(s0123));
                assign c[(gi * 24) + (gj * 6) +: 6] = s0123[5:0];
            end
        end
    endgenerate
endmodule

// 2x2 unsigned array multiplier (product 0..9).
module mul2 (
    input  wire [1:0] x,
    input  wire [1:0] y,
    output wire [3:0] p
);
    wire pp01, pp10, pp11, s1, c1, s2, c2;
    assign pp01 = x[0] & y[1];
    assign pp10 = x[1] & y[0];
    assign pp11 = x[1] & y[1];
    assign s1 = pp01 ^ pp10;
    assign c1 = pp01 & pp10;
    assign s2 = pp11 ^ c1;
    assign c2 = pp11 & c1;
    assign p = {c2, s2, s1, x[0] & y[0]};
endmodule

// Ripple adder: s = x + y (W+1 bits). Carry uses AND/OR, sum XOR.
module add_rc #(
    parameter W = 4
) (
    input  wire [W-1:0] x,
    input  wire [W-1:0] y,
    output wire [W:0]   s
);
    wire [W:0] cin;
    wire [W-1:0] p, g;
    assign cin[0] = 1'b0;
    genvar i;
    generate
        for (i = 0; i < W; i = i + 1) begin : bits
            assign p[i] = x[i] ^ y[i];
            assign g[i] = x[i] & y[i];
            assign s[i] = p[i] ^ cin[i];
            assign cin[i+1] = g[i] | (p[i] & cin[i]);
        end
    endgenerate
    assign s[W] = cin[W];
endmodule
