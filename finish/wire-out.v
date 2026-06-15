
// Issue 1: Wire width mismatches | Issue 2: Array bounds violations | Issue 3: Module port connection inconsistency

module inverter_4bit(
    input wire [3:0] in,
    output wire [3:0] out
);
    assign out = ~in;
endmodule

module T_flipflop(
    input wire clk,
    input wire T,
    output reg Q
);
    always @(posedge clk) begin
        Q <= Q ^ T;
    end
endmodule

module sub_module(
    input wire [1:0] in_port,
    output wire [1:0] out_port
);
    assign out_port = in_port;
endmodule

module top_module #(parameter N =4) (
    input wire clk,
    input wire [3:0] wrong_width_in,
    output wire [3:0] inv_out,
    output wire [N-1:0] count_out,
    output wire [0:0] wrong_out,
    input wire [3:0] small_data
);
    // Issue 1: Wire width mismatches
    inverter_4bit inv(
        .in(wrong_width_in),
        .out(inv_out)
    );

    // Issue 2: Array bounds violations
    reg [3:0] t [0:3];
    genvar i;
    generate
        for (i = 0; i < N; i = i + 1) begin : flipflop_gen
            T_flipflop ff(
                .clk(clk),
                .T(t[i]),
                .Q(count_out[i])
            );
        end
    endgenerate

    // Issue 3: Module port connection inconsistency
    wire [1:0] wrong_in;
    assign wrong_in = 2'b10;
    sub_module sm(
        .in_port(wrong_in),
        .out_port(wrong_out)
    );

    // Issue 4: Concat duplicate signals
    wire [7:0] duplicate_concat;
    wire [3:0] concat_temp;
    assign concat_temp = 4'b1010;
    assign duplicate_concat = {concat_temp, concat_temp};  // Duplicate: concat_temp appears twice

    // Issue 5: Partselect with wrong bounds (msb < lsb)
    wire [3:0] wrong_bounds;
    reg [7:0] bounds_temp;
    assign wrong_bounds = bounds_temp[2:5];  // Error: 2 < 5

    // Issue 6: Partselect overflow
    wire [3:0] overflow_select;
    reg [7:0] overflow_temp;
    assign overflow_select = overflow_temp[10:7];  // Error: overflow_temp is only 8-bit (0-7)

    // Issue 7: Repeat width mismatch (repeat width > LHS width)
    wire [7:0] repeat_result;
    assign repeat_result = {4{small_data}};  // Error: 4*4=16-bit > 8-bit LHS

    // Issue 8: Array index out of bounds
    wire array_out;
    assign array_out = t[5];  // Error: t array has only 4 elements (0-3)

endmodule