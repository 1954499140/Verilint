module foo_bar(data_out, usr_id, data_in, clk, rst_n);
output reg [7:0] data_out;
input wire [2:0] usr_id;
input wire [7:0] data_in;
input wire clk, rst_n;

wire grant_access;
reg [2:0] usr_id_sync;
reg [7:0] data_in_sync;
reg [7:0] data_out_next;
reg grant_internal;

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
usr_id_sync <= 3'h0;
else
usr_id_sync <= usr_id;
end

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
data_in_sync <= 8'h00;
else
data_in_sync <= data_in;
end

assign grant_access = grant_internal;

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
data_out = 8'h00;
else
data_out = (grant_access) ? data_in_sync : data_out;
grant_internal = (usr_id_sync == 3'h4) ? 1'b1 : 1'b0;
end

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
data_out_next <= 8'h00;
else
data_out_next <= data_out;
end

endmodule