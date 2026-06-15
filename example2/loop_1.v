module loop_examples(
    input clk, reset, en, a, b, c, d,
    output reg out_direct,
    output reg out_indirect_a, out_indirect_b, out_indirect_c, out_indirect_d, out_indirect_e,
    output reg out_fixed_direct, out_fixed_chain1, out_fixed_chain2, out_fixed_chain3
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
    out_indirect_c = out_indirect_d ^ en;
    out_indirect_d = out_indirect_e & b;
    out_indirect_e = out_indirect_a | c;
end

always @(*) begin
    if (en & b) begin
        out_direct = d;
    end else if (c) begin
        out_direct = out_direct;
    end
end

always @(posedge clk or posedge reset) begin
    if (reset) begin
        out_fixed_direct <= 1'b0;
        out_fixed_chain1 <= 1'b0;
        out_fixed_chain2 <= 1'b0;
        out_fixed_chain3 <= 1'b0;
    end else if (en & b) begin
        out_fixed_direct <= a;
        out_fixed_chain1 <= out_fixed_chain2;
    end else if (c) begin
        out_fixed_chain2 <= out_fixed_chain3;
        out_fixed_direct <= out_fixed_direct;
    end else begin
        out_fixed_chain3 <= out_fixed_chain1;
        out_fixed_direct <= out_fixed_direct;
    end
end

endmodule