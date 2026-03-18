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
    output reg         flush_o,
    output reg         halt_csr_o
);
    reg [DW-1:0] mem [0:2**AW-1];
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            flush_o <= 1'b0;
            halt_csr_o <= 1'b0;
        end
    end
endmodule

module top (
    input  wire        clk,
    input  wire        hard_rst_n,
    input  wire        debug_rst_n,
    input  wire        irq,
    input  wire        time_irq,
    output wire        flush_csr_ctrl,
    output wire        halt_csr_ctrl
);
    wire rst_n = hard_rst_n & debug_rst_n;
    wire flush_csr_ctrl;
    wire halt_csr_ctrl;
    wire irq_i;
    wire time_irq_i;
    assign irq_i = irq;
    assign time_irq_i = time_irq;
    csr_regfile #(
        .DW(32),
        .AW(5)
    ) csr_regfile_i (
        .flush_o      ( flush_csr_ctrl ),
        .halt_csr_o   ( halt_csr_ctrl ),
        .irq_i        (1'b0),
        .time_irq_i   (),
        .rst_n        (1'b0)
    );
endmodule