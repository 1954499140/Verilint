
module top(
    input wire sel_sync,
    input wire in0,
    input wire in1,
    output wire z
);
wire not_sel;
wire and_out1;
wire and_out2;

assign not_sel   = ~sel_sync;
assign and_out1  = not_sel & in0;
assign and_out2  = sel_sync & in1;
assign z         = and_out1 | and_out2;

endmodule
