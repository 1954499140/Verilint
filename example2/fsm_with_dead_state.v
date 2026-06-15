module fsm_module(clk, reset, in, out);
    parameter zero=2, one=3, two=0, three=1;
    output out;
    input clk, reset, in;
    reg out;
    reg [1:0] current_state, next_state;
    reg temp1;
    reg temp2;
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            current_state <= zero;
            temp1 <= 1'b0;
        end else begin
            current_state <= next_state;
            temp1 <= in;
        end
    end
    always @(current_state or in or temp1) begin
        case (current_state)
            zero: begin
                if (in) begin
                    next_state = one;
                end else begin
                    next_state = zero;
                end
                temp2 = 1'b0;
            end
            one: begin
                if (in) begin
                    next_state = two;
                end else begin
                    next_state = zero;
                end
                temp2 = temp1;
            end
            two: begin
                if (in) begin
                    next_state = two;
                end else begin
                    next_state = zero;
                end
                temp2 = 1'b1;
            end
            three: begin
                next_state = three;
                temp2 = 1'b0;
            end
            default: begin
                next_state = zero;
                temp2 = 1'b0;
            end
        endcase
    end
    always @(current_state or temp2) begin
        case (current_state)
            zero: begin
                out <= 1'b0;
            end
            one: begin
                out <= 1'b0;
            end
            two: begin
                out <= 1'b1;
            end
            three: begin
                out <= 1'b0;
            end
            default: begin
                out <= 1'b0;
            end
        endcase
    end
endmodule