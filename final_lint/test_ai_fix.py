"""
测试AI Fix Agent功能
"""

import sys
sys.path.insert(0, '.')

from ai_fix_agent import AIFixAgent, fix_with_ai

# 测试代码（包含一些典型问题）
test_code = '''
module test_module (
    input wire clk,
    input wire rst,
    input wire [7:0] data_in,
    output reg [7:0] data_out
);

    // 问题1: 未初始化的寄存器
    reg [7:0] temp_data;

    // 问题2: 使用但未驱动的信号
    wire [7:0] unused_signal;

    // 问题3: 宽度不匹配
    reg [3:0] narrow_reg;

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            data_out <= 0;
        end else begin
            // 使用未初始化的temp_data
            temp_data <= data_in;
            data_out <= temp_data;

            // 宽度不匹配
            narrow_reg <= data_in;
        end
    end

endmodule
'''

# 保存测试文件
with open('/tmp/test_ai_fix.v', 'w') as f:
    f.write(test_code)

print("=" * 70)
print("AI Fix Agent 测试")
print("=" * 70)

# 检查是否有API密钥
import os
if not os.getenv("OPENAI_API_KEY"):
    print("\n警告: 未设置 OPENAI_API_KEY 环境变量")
    print("请设置环境变量或使用 --ai-key 参数提供API密钥")
    print("\n示例代码已保存到 /tmp/test_ai_fix.v")
    print("可以手动运行: python verilint_checker.py /tmp/test_ai_fix.v")
else:
    print("\n测试AI Fix功能...")
    try:
        result = fix_with_ai('/tmp/test_ai_fix.v', max_iterations=2)

        print(f"\n修复结果:")
        print(f"  成功: {result.success}")
        print(f"  迭代次数: {result.iterations}")
        print(f"  原始问题数: {len(result.original_issues)}")
        print(f"  剩余问题数: {len(result.remaining_issues)}")
        print(f"  消息: {result.message}")

        if result.backup_path:
            print(f"\n备份文件: {result.backup_path}")

        print("\n修复后的代码:")
        print("-" * 70)
        print(result.fixed_code[:500] + "..." if len(result.fixed_code) > 500 else result.fixed_code)

    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
