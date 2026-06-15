module Ex (
    input  wire        in0,
    input  wire        in1,
    input  wire        sel,
    input  wire        en,
    input  wire        rst_n,
    input  wire        clk,
    input  wire [2:0]  in_ext0,
    input  wire [2:0]  in_ext1,
    input  wire [1:0]  mode,
    input  wire [3:0]  cfg_data,
    output wire        z,
    output reg  [3:0]  z_ext,
    output wire        z_err,
    output reg  [7:0]  status_reg,
    output wire [5:0]  comb_out,
    output reg  [3:0]  cnt
);

reg         sel_sync_reg [1:0];
reg         en_sync_reg [1:0];
reg  [2:0]  in_ext0_reg;
reg  [2:0]  in_ext1_reg;
reg  [3:0]  cfg_data_reg;
reg  [1:0]  mode_sync_reg;

wire        sel_sync;
wire        en_sync;
wire        not_sel;
wire        and_out1;
wire        and_out2;
wire  [2:0] ext_and;
wire  [2:0] ext_or;
wire  [2:0] ext_xor;
wire  [2:0] ext_nand;
wire  [2:0] ext_nor;
wire  [2:0] ext_xnor;
reg   [3:0] mux_out;
wire        valid;
reg         glitch_detect;
reg  [2:0]  state;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        sel_sync_reg[0] <= 1'b0;
        sel_sync_reg[1] <= 1'b0;
        en_sync_reg[0]  <= 1'b0;
        en_sync_reg[1]  <= 1'b0;
        in_ext0_reg     <= 3'b000;
        in_ext1_reg     <= 3'b000;
        cfg_data_reg    <= 4'b0000;
        mode_sync_reg   <= 2'b00;
    end else begin
        sel_sync_reg[0] <= sel;
        en_sync_reg[0]  <= en;
        sel_sync_reg[1] <= sel_sync_reg[0];
        en_sync_reg[1]  <= en_sync_reg[0];
        in_ext0_reg     <= in_ext0;
        in_ext1_reg     <= in_ext1;
        cfg_data_reg    <= cfg_data;
        mode_sync_reg   <= mode;
    end
end

assign sel_sync = sel_sync_reg[1];
assign en_sync  = en_sync_reg[1];

assign not_sel   = ~sel_sync;
assign and_out1  = not_sel & in0;
assign and_out2  = sel_sync & in1;
assign z         = and_out1 | and_out2;
endmodule