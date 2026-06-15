module fsm_1(out, user_input, clk, rst_n);
input [2:0] user_input;
input clk, rst_n;
output reg [2:0] out;
reg [1:0] state;
reg [1:0] next_state;
reg t0;
reg t1;
reg [2:0] tmp;

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
begin
state <= 2'h0;
t0 <= 1'b0;
t1 <= 1'b0;
end
else
begin
state <= next_state;
t0 <= user_input[0];
t1 <= user_input[1];
end
end

always @*
begin
tmp = user_input;
end

always @(state or user_input or t0 or t1)
begin
case (user_input)
3'h3: next_state = 2'h3;
3'h4: next_state = 2'h2;
3'h5: next_state = 2'h1;
endcase
end

always @(posedge clk or negedge rst_n)
begin
if (!rst_n)
out <= 3'h0;
else
out <= {t1, t0, user_input[2]};
end

endmodule