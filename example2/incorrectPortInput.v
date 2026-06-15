module csr_regfile
#(
    parameter DW = 32,
    parameter AW = 5
)
(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        irq_i,
    input  wire        time_irq_i,
    input  wire        wr_en_i,
    input  wire [AW-1:0] addr_i,
    input  wire [DW-1:0] wdata_i,
    output reg [DW-1:0] rdata_o,
    output reg         flush_o,
    output reg         halt_csr_o,
    output reg         irq_pend_o,
    output reg         time_irq_pend_o
);

reg [DW-1:0] csr_array [0:31];
reg irq_sync_0;
reg irq_sync_1;
reg time_irq_sync_0;
reg time_irq_sync_1;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        irq_sync_0 <= 1'b0;
        irq_sync_1 <= 1'b0;
        time_irq_sync_0 <= 1'b0;
        time_irq_sync_1 <= 1'b0;
    end else begin
        irq_sync_0 <= irq_i;
        irq_sync_1 <= irq_sync_0;
        time_irq_sync_0 <= time_irq_i;
        time_irq_sync_1 <= time_irq_sync_0;
    end
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        rdata_o <= 32'h0;
    end else begin
        rdata_o <= csr_array[addr_i];
    end
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        flush_o <= 1'b0;
        halt_csr_o <= 1'b0;
        irq_pend_o <= 1'b0;
        time_irq_pend_o <= 1'b0;
    end else if (wr_en_i) begin
        csr_array[addr_i] <= wdata_i;
        flush_o <= wdata_i[0];
        halt_csr_o <= wdata_i[1];
        irq_pend_o <= wdata_i[2];
        time_irq_pend_o <= wdata_i[3];
    end else begin
        irq_pend_o <= irq_sync_1;
        time_irq_pend_o <= time_irq_sync_1;
        flush_o <= irq_sync_1 & time_irq_sync_1;
        halt_csr_o <= csr_array[addr_i][31];
    end
end

endmodule

module top (
    input  wire        clk,
    input  wire        hard_rst_n,
    input  wire        debug_rst_n,
    input  wire        irq,
    input  wire        time_irq,
    input  wire        wr_en,
    input  wire [4:0]  addr,
    input  wire [31:0] wdata,
    output wire [31:0] rdata,
    output wire        flush_csr_ctrl,
    output wire        halt_csr_ctrl,
    output wire        irq_pend,
    output wire        time_irq_pend
);

wire rst_n;
reg hard_rst_s0;
reg hard_rst_s1;
reg debug_rst_s0;
reg debug_rst_s1;

assign rst_n = hard_rst_s1 & debug_rst_s1;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        hard_rst_s0 <= 1'b0;
        hard_rst_s1 <= 1'b0;
        debug_rst_s0 <= 1'b0;
        debug_rst_s1 <= 1'b0;
    end else begin
        hard_rst_s0 <= hard_rst_n;
        hard_rst_s1 <= hard_rst_s0;
        debug_rst_s0 <= debug_rst_n;
        debug_rst_s1 <= debug_rst_s0;
    end
end

csr_regfile #(
    .DW(32)
) csr_regfile_i (
    .clk(clk),
    .rst_n(rst_n),
    .irq_i(irq),
    .wr_en_i(wr_en),
    .addr_i(addr),
    .wdata_i(wdata),
    .rdata_o(rdata),
    .flush_o(flush_csr_ctrl),
    .halt_csr_o(halt_csr_ctrl),
    .irq_pend_o(irq_pend),
    .time_irq_pend_o(time_irq_pend)
);

endmodule