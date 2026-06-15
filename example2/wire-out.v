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
    reg Q_sync;
    always @(posedge clk) begin
        Q_sync <= Q ^ T;
        Q <= Q_sync;
    end
endmodule

module sub_module(
    input wire in_port,
    output wire [1:0] out_port
);
    wire tmp;
    assign tmp = in_port;
    assign out_port = {tmp, ~tmp};
endmodule

module top_module #(parameter N = 4) (
    input wire clk,
    input wire rst_n,
    input wire [3:0] wrong_width_in,
    input wire [3:0] data_in,
    output wire [3:0] inv_out,
    output wire [N-1:0] count_out,
    output wire [0:0] wrong_out,
    output wire [3:0] data_out
);
    reg [3:0] data_sync;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)
            data_sync <= 4'b0000;
        else
            data_sync <= data_in;
    end

    inverter_4bit inv(
        .in(wrong_width_in),
        .out(inv_out)
    );

    reg [3:0] t [0:3];
    generate
        genvar i;
        for (i = 0; i <= N; i = i + 1) begin : t_ff_gen
            T_flipflop ff(
                .clk(clk),
                .T(t[i]),
                .Q(count_out[i])
            );
        end
    endgenerate

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            t[0] <= 4'b0000;
            t[1] <= 4'b0000;
            t[2] <= 4'b0000;
            t[3] <= 4'b0000;
        end
        else begin
            t[0] <= data_sync[0];
            t[1] <= data_sync[1];
            t[2] <= data_sync[2];
            t[3] <= data_sync[3];
        end
    end

    wire [1:0] wrong_in;
    assign wrong_in = 2'b10;
    sub_module sm(
        .in_port(wrong_in),
        .out_port(wrong_out)
    );

    assign data_out = inv_out ^ count_out[3:0];

endmodule