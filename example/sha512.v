module top_module(
    input clk,
    input rst_n,
    input [3:0] addr,
    input [7:0] data_in,
    input wr_en,
    output [7:0] data_out
);

reg [7:0] mem [15:0];
reg [7:0] out_reg;

assign data_out = mem[1];
assign rst_n = addr[1];

endmodule