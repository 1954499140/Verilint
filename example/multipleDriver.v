module multiple_drivers (
    input  wire clk,
    input  wire en1,
    input  wire en2,
    input  wire data1,
    input  wire data2,
    output reg  shared_signal
);

always @(posedge clk) begin
    if (en1) begin
        shared_signal <= data1;
    end
end

always @(posedge clk) begin
    if (en2) begin
        shared_signal <= data2;
    end
end

endmodule