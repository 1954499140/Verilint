module simple_array_overflow;
    // Array with 5 elements (valid index: 0-4)
    reg [7:0] arr [0:4];
    integer i;
    reg [7:0] val;
    reg [1:0] cube [0:1][0:2][0:3];

    initial begin
        val = arr[2];
        val = arr[5];
        val = arr[-1];
    end
endmodule