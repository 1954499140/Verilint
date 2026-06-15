
module test(
    input clk,
    input rst_n,
    output reg adc_valid_in_d = 'h0  // ”–≥ı º÷µ
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n)
            adc_valid_in_d <= 0;
        else
            adc_valid_in_d <= 1;
endmodule
