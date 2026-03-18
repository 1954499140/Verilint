module loop_examples(
    input clk, reset, en, a,
    output reg out_direct,
    output reg out_indirect_a, out_indirect_b, out_indirect_c,
    output reg out_fixed_direct,
    output reg out_fixed_indirect_a, out_fixed_indirect_b, out_fixed_indirect_c
);

always @(*) begin
    if (en) begin
        out_direct = a;
    end else begin
        out_direct = out_direct;
    end
end
always @(*) begin
    out_indirect_a = out_indirect_b & en;
    out_indirect_b = out_indirect_c | en;
    out_indirect_c = out_indirect_a ^ en;
end

always @(posedge clk or posedge reset) begin
    if (reset) begin
        out_fixed_direct <= 1'b0;
    end else if (en) begin
        out_fixed_direct <= a;
    end else begin
        out_fixed_direct <= out_fixed_direct;
    end
end

always @(posedge clk or posedge reset) begin
    if (reset) begin
        out_fixed_indirect_a <= 1'b0;
        out_fixed_indirect_b <= 1'b0;
        out_fixed_indirect_c <= 1'b0;
    end else begin
        out_fixed_indirect_a <= out_fixed_indirect_b & en;
        out_fixed_indirect_b <= out_fixed_indirect_c | en;
        out_fixed_indirect_c <= out_fixed_indirect_a ^ en;
    end
end

endmodule