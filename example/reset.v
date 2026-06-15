//mismatch
module reset_dimension_mismatch (
    input         clk_50m,
    input         rst_n,
    input         rst,
    input         rst2_n,
    input         sys_en,
    input  [7:0]  data_in,
    output [7:0]  data_out1,
    output [7:0]  data_out2,
    output [15:0] cnt,
    output        flag1,
    output        flag2
);

reg  [7:0]  reg1;
reg  [7:0]  reg2;
reg  [15:0] reg3;
reg         reg4;
reg         reg5;

sub_module u_sub_module (
    .clk    (clk_50m),
    .rst_in (rst & sys_en),
    .din    (reg1),
    .dout   (data_out1)
);

always @(posedge clk_50m or negedge rst_n) begin
    if (!rst_n) reg1 <= 8'h00;
    else reg1 <= data_in;
end

always @(posedge clk_50m or negedge rst_n) begin
    if (!rst_n) reg2 <= 8'hff;
    else reg2 <= data_in + 1'b1;
end

always @(posedge clk_50m or posedge rst) begin
    if (rst) reg3 <= 16'h0000;
    else reg3 <= reg3 + 1'b1;
end

always @(posedge clk_50m) begin
    if (!rst_n && !rst2_n) reg4 <= 1'b0;
    else reg4 <= (reg3 > 16'h1000) ? 1'b1 : 1'b0;
end

always @(posedge clk_50m) begin
    if (sys_en) reg5 <= 1'b1;
    else reg5 <= reg4;
end

assign data_out2 = reg2;
assign cnt = reg3;
assign flag1 = reg4;
assign flag2 = reg5;

endmodule

module sub_module (
    input         clk,
    input         rst_in,
    input  [7:0]  din,
    output reg [7:0] dout
);

always @(posedge clk) begin
    if (rst_in) dout <= 8'h00;
    else dout <= din + 8'h02;
end

endmodule