module mismatch(
    input [3:0] a,
    input [1:0] b,
    input [5:0] c,
    input [2:0] d,
    output [3:0] x,
    output [5:0] y,
    output z
);
assign x = a + b;
assign y = c & d;
assign z = (a == b);
endmodule