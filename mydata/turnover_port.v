module sub_module(
    input wire data_in,
    input wire ctrl,
    output wire data_out
);

assign data_out = data_in & ctrl;

endmodule

module top(
    input wire a,
    input wire c,
    output wire b
);

sub_module u_inst(
    .data_in (b),   // IO direction error: top output connected to sub input
    .ctrl    (c),
    .data_out(a)    // IO direction error: top input connected to sub output
);

endmodule