// 4:1 multiplexer (behavioral RTL → Yosys → sky130_fd_sc_hd GLN)
//
//   python -m pnr_tool synth --rtl designs/mux41/mux41.v --top mux41 --out designs/mux41/mux41.gl.v

module mux41 (
    input  wire       a,
    input  wire       b,
    input  wire       c,
    input  wire       d,
    input  wire [1:0] sel,
    output wire       y
);
    assign y = sel[1] ? (sel[0] ? d : c) : (sel[0] ? b : a);
endmodule
