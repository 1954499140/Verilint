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
localparam ADDR2 = 3'b011;

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

reg [1:0] state_sync0;
reg [1:0] state_sync1;
reg [2:0] addr_sync0;
reg [2:0] addr_sync1;
reg cs0_internal;
reg cs1_internal;
reg cs2_internal;
reg idle_internal;
reg read_internal;
reg write_internal;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state_sync0 <= 2'b00;
        state_sync1 <= 2'b00;
        addr_sync0 <= 3'b000;
        addr_sync1 <= 3'b000;
    end else begin
        state_sync0 <= state;
        state_sync1 <= state_sync0;
        addr_sync0 <= addr;
        addr_sync1 <= addr_sync0;
    end
end

always @(*) begin
    cs0_internal = 1'b0;
    cs1_internal = 1'b0;
    cs2_internal = 1'b0;
    case (addr_sync1)
        ADDR0:  cs0_internal = 1'b1;
        ADDR1:  cs1_internal = 1'b1;
        3'b010: cs2_internal = 1'b1;
        default: ;
    endcase
    cs0 = cs0_internal;
    cs1 = cs1_internal;
    cs2 = cs2_internal;
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        idle_internal  = 1'b0;
        read_internal  = 1'b0;
        write_internal = 1'b0;
    end else begin
        idle_internal  = 1'b0;
        read_internal  = 1'b0;
        write_internal = 1'b0;
        if (state_sync1 == `STATE_IDLE) begin
            idle_internal = 1'b1;
        end else if (state_sync1 == `STATE_READ) begin
            read_internal = 1'b1;
        end else if (state_sync1 == 2'b10) begin
            write_internal = 1'b1;
        end
    end
    idle <= idle_internal;
    read <= read_internal;
    write <= write_internal;
end

endmodule