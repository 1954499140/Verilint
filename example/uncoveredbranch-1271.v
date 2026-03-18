module case_incomplete_coverage #(
    parameter OP_WIDTH = 3
)(
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire [2:0]  op_code,
    input  wire [7:0]           data_a,
    input  wire [7:0]           data_b,
    output reg  [7:0]           result
);
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        result <= 8'd0;
    end else begin
        case (op_code)
            3'd0: result <= data_a + data_b;
            3'd1: result <= data_a - data_b;
            3'd2: result <= data_a & data_b;

        endcase
    end
end

endmodule