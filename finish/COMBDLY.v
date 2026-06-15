module sens_error_demo(
    input  a, b, c, d,
    output reg out1,
    output reg out2
);

always @(a) begin
    out1 = a & b;
end

always @(c or d) begin
    out2 = c | c;
end

reg out3;
always @(a or b) begin
    out3 = a + b;
end

endmodule