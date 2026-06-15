module case_incomplete_coverage #(
    parameter OP_WIDTH = 3
)(
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire [OP_WIDTH-1:0]  op_code,
    input  wire [7:0]           data_a,
    input  wire [7:0]           data_b,
    output reg  [7:0]           result
);

reg [7:0] data_a_sync;
reg [7:0] data_b_sync;
reg [OP_WIDTH-1:0] op_sync;
reg [7:0] temp_result;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        data_a_sync <= 8'd0;
        data_b_sync <= 8'd0;
        op_sync <= {OP_WIDTH{1'b0}};
    end else begin
        data_a_sync <= data_a;
        data_b_sync <= data_b;
        op_sync <= op_code;
    end
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        temp_result <= 8'd0;
    end else begin
        case (op_sync)
            3'd0: temp_result <= data_a_sync + data_b_sync;
            3'd1: temp_result <= data_a_sync - data_b_sync;
            3'd2: temp_result <= data_a_sync & data_b_sync;

        endcase
    end
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        result <= 8'd0;
    end else begin
        result <= temp_result;
    end
end

endmodule
