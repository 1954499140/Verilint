module reset_dimension_mismatch (
    input         clk_50m,
    input         rst_n,
    input         rst,
    input         rst2_n,
    input         sys_en,
    input  [7:0]  data_in,
    input  [7:0]  ctrl_in,
    output [7:0]  data_out1,
    output [7:0]  data_out2,
    output [7:0]  data_out3,
    output [15:0] cnt,
    output [15:0] cnt2,
    output        flag1,
    output        flag2,
    output        flag3
);

reg  [7:0]  reg1;
reg  [7:0]  reg2;
reg  [7:0]  reg6;
reg  [15:0] reg3;
reg  [15:0] reg7;
reg         reg4;
reg         reg5;
reg         reg8;

sub_module u_sub_module (
    .clk    (clk_50m),
    .rst_in (rst & sys_en),
    .din    (reg1),
    .dout   (data_out1)
);

sub_module u_sub_module2 (
    .clk    (clk_50m),
    .rst_in (rst_n | rst2_n),
    .din    (reg2),
    .dout   (data_out3)
);

always @(posedge clk_50m or negedge rst_n) begin
    if (!rst_n) reg1 <= 8'h00;
    else reg1 <= data_in;
end

always @(posedge clk_50m or negedge rst_n) begin
    if (!rst_n) reg2 <= 8'hff;
    else reg2 <= data_in + 1'b1;
end

always @(posedge clk_50m or negedge rst2_n) begin
    if (!rst2_n) reg6 <= 8'h55;
    else reg6 <= ctrl_in + reg1;
end

always @(posedge clk_50m or posedge rst) begin
    if (rst) reg3 <= 16'h0000;
    else reg3 <= reg3 + 1'b1;
end

always @(posedge clk_50m or posedge rst) begin
    if (rst) reg7 <= 16'hFFFF;
    else reg7 <= reg7 - 1'b1;
end

always @(posedge clk_50m) begin
    if (!rst_n && !rst2_n) reg4 <= 1'b0;
    else reg4 <= (reg3 > 16'h1000) ? 1'b1 : 1'b0;
end

always @(posedge clk_50m) begin
    if (sys_en) reg5 <= 1'b1;
    else reg5 <= reg4;
end

always @(posedge clk_50m) begin
    if (rst) reg8 <= 1'b0;
    else reg8 <= (reg7 < 16'h8000) ? 1'b1 : 1'b0;
end

assign data_out2 = reg2;
assign cnt = reg3;
assign cnt2 = reg7;
assign flag1 = reg4;
assign flag2 = reg5;
assign flag3 = reg8;

endmodule

module sub_module (
    input         clk,
    input         rst_in,
    input  [7:0]  din,
    output reg [7:0] dout
);

reg [7:0] delay_reg;

always @(posedge clk) begin
    if (rst_in) delay_reg <= 8'h00;
    else delay_reg <= din;
end

always @(posedge clk) begin
    if (rst_in) dout <= 8'h00;
    else dout <= delay_reg + 8'h02;
end

endmodule