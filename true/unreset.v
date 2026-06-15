module top_module (
    input clk,
    input rst,
    input full,
    input full_cur,
    input drop_frame,
    input input_axis_tlast,
    input input_axis_tuser,
    output reg [7:0] wr_ptr,
    output reg [7:0] wr_ptr_cur,
    output reg drop_frame_reg
);

always @(posedge clk or posedge rst) begin
    if(rst) begin
        wr_ptr <= 0;
    end else begin
        if(full || full_cur || drop_frame) begin
            drop_frame_reg <= 1;
            if(input_axis_tlast) begin
                wr_ptr_cur <= wr_ptr;
                drop_frame_reg <= 0;
            end
        end else begin
            wr_ptr_cur <= wr_ptr_cur + 1;
            wr_ptr <= input_axis_tuser ? wr_ptr_cur : wr_ptr + 1;
        end
    end
end

endmodule