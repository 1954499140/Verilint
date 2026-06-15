module invert(
    input in,
    output out
);
    assign out = ~in;
endmodule

module top;
    wire a,b;
    invert u1(a,b);
endmodule