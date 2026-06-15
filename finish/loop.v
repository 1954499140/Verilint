module loop_examples(
    input clk, reset, en, a,
    output reg out_direct,
    output reg out_indirect_a, out_indirect_b, out_indirect_c
);
reg out_fixed_direct;
always @(*) begin
    if (en) begin
        out_direct = a;
    end else begin
        out_direct = out_direct;
    end
end
assign out_indirect_a = out_indirect_b & en;
assign out_indirect_b = out_indirect_c | en;
assign out_indirect_c = out_indirect_a ^ en;

always @(posedge clk or posedge reset) begin
    if (reset) begin
        out_fixed_direct <= 1'b0;
    end else if (en) begin
        out_fixed_direct <= a;
    end else begin
        out_fixed_direct <= out_fixed_direct;
    end
end
endmodule