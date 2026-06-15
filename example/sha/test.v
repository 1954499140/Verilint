module example_shift (
    input  logic        clk,
    input  logic [63:0] right,
    output logic [41:0] left
);

logic [63:0] right_reg;

always_ff @(posedge clk) begin
    right_reg <= right;
end

assign left = right_reg >> 6;

endmodule