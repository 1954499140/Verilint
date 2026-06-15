module sens_error_demo #(
    parameter DATA_WIDTH = 4
)(
    input clk,
    input rst_n,
    input a, b, c, d,
    input [DATA_WIDTH-1:0] in_data0,
    input [DATA_WIDTH-1:0] in_data1,
    input sel,
    output reg out1,
    output reg out2,
    output reg out3,
    output reg [DATA_WIDTH-1:0] out_data,
    output reg latch_out,
    output reg flag
);
reg temp_reg;
reg [DATA_WIDTH-1:0] temp_data0;
reg [DATA_WIDTH-1:0] temp_data1;
always @(a) begin
    out1 = (a & b) ^ (c & d);
end
always @(c or d) begin
    out2 = (c | c) & (d ^ ~d) | (a & b);
end
always @(a or b) begin
    temp_reg = a + b;
    out3 = temp_reg + c;
end
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        out_data <= 0;
    end else begin
        out_data <= sel ? temp_data0 : temp_data1;
    end
end
always @* begin
    temp_data0 = in_data0 + in_data1;
    temp_data1 = in_data0 ^ in_data1;
end
always @(flag) begin
    latch_out = temp_reg;
end
endmodule