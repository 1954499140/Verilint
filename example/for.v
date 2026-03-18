module for_error(
    input [3:0] N, // 运行时可变的输入变量
    input [1:0] data,
    output reg out
);

always @(*) begin
    out = 1'b0;
    for(integer i=0;i<4;i=i+1) begin
        out = out | data[i];
    end
end

endmodule