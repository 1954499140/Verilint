module multiple_drivers (
    input  wire clk,
    input  wire en1,
    input  wire en2,
    input  wire data1,
    input  wire data2,
    output reg  shared_signal
);

reg sync_en1;
reg sync_en2;
reg sync_data1;
reg sync_data2;

always @(posedge clk) begin
    sync_en1 <= en1;
    sync_data1 <= data1;
end

always @(posedge clk) begin
    sync_en2 <= en2;
    sync_data2 <= data2;
end

always @(posedge clk) begin
    if (sync_en1) begin
        shared_signal <= sync_data1;
    end
end

always @(posedge clk) begin
    if (sync_en2) begin
        shared_signal <= sync_data2;
    end
end

endmodule