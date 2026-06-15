module top(
    input wire sel_sync,
    input wire in0,
    input wire in1,
    output wire z
);
assign not_sel   = ~sel_sync;
assign and_out1  = not_sel & in0;
assign and_out2  = sel_sync & in1;
assign z         = (not_sel & in0) | and_out2;
endmodule