module example(
    input clk,
    input rst,
    input in,
    output reg x,
    output reg y
);

always @(posedge clk)
    if (rst)
        x <= 1;
    else begin
        x <= y;
        y <= x ? in : 0;
    end
endmodule