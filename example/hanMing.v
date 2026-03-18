`define STATE_IDLE  2'b00
`define STATE_READ  2'b01

module hamming_violation_all (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [2:0]  addr,
    input  wire [1:0]  state,
    output reg         cs0, cs1, cs2,
    output reg         idle, read, write
);

parameter ADDR0 = 3'b000;
localparam ADDR1 = 3'b001;
localparam ADDR2 = 3'b010;

always @(*) begin
    cs0 = 1'b0;
    cs1 = 1'b0;
    cs2 = 1'b0;
    case (addr)
        ADDR0:  cs0 = 1'b1;
        ADDR1:  cs1 = 1'b1;
        3'b010: cs2 = 1'b1;
        default: ;
    endcase
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        idle  = 1'b0;
        read  = 1'b0;
        write = 1'b0;
    end else begin
        idle  = 1'b0;
        read  = 1'b0;
        write = 1'b0;
        if (state == `STATE_IDLE) begin
            idle = 1'b1;
        end else if (state == `STATE_READ) begin
            read = 1'b1;
        end else if (state == 2'b10) begin
            write = 1'b1;
        end
    end
end

endmodule