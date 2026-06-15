module fsm_module(clk, reset, in, out);
    parameter zero=2, one=3, two=0, three=1;
    output out;
    input clk, reset, in;
    reg out;
    reg [1:0] current_state, next_state;
    reg t0, t1, t2, t3;
    reg s0, s1;
    reg m0, m1;

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            current_state <= zero;
            s0 <= 1'b0;
            s1 <= 1'b0;
        end else begin
            current_state <= next_state;
            s0 <= in;
            s1 <= s0;
        end
    end

    always @* begin
        t0 = s0;
        t1 = s1;
        t2 = t0 & t1;
        t3 = t0 ^ t1;
    end

    always @(current_state or t0 or t2 or t3) begin
        case (current_state)
            zero: begin
                m0 = 1'b0;
                m1 = 1'b1;
                if (t0) begin
                    next_state = one;
                end else begin
                    next_state = two;
                end
            end
            one: begin
                m0 = t2;
                m1 = t3;
                if (t0) begin
                    next_state = two;
                end else begin
                    next_state = zero;
                end
            end
            two: begin
                m0 = t3;
                m1 = t2;
                if (t0) begin
                    next_state = two;
                end else begin
                    next_state = zero;
                end
            end
            three: begin
                m0 = 1'b1;
                m1 = 1'b0;
                next_state = two;
            end
            default: begin
                m0 = 1'b0;
                m1 = 1'b0;
                next_state = zero;
            end
        endcase
    end

    always @(current_state or m0 or m1) begin
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