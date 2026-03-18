from llvmlite import ir, binding
import subprocess
import os

# --------------------------
# 1. 初始化LLVM环境
# --------------------------
# 初始化llvmlite绑定
binding.initialize()
binding.initialize_native_target()
binding.initialize_native_asmprinter()

# 创建LLVM上下文和模块（对应Verilog的counter_4bit模块）
ctx = ir.Context()
llvm_module = ir.Module(name="counter_4bit_module", context=ctx)
llvm_module.triple = binding.get_default_triple()  # 自动适配系统架构

# --------------------------
# 2. 定义全局变量（模拟Verilog的reg寄存器）
# --------------------------
# cnt: 4位计数器 → i32（低4位有效），初始值0
cnt_type = ir.IntType(32)  # 显式指定上下文
cnt_global = ir.GlobalVariable(llvm_module, cnt_type, name="cnt")
cnt_global.linkage = "private"  # 私有链接，仅模块内可见
cnt_global.initializer = ir.Constant(cnt_type, 0)

# cout: 进位输出 → i1（布尔型），初始值0
cout_type = ir.IntType(1)  # 显式指定上下文
cout_global = ir.GlobalVariable(llvm_module, cout_type, name="cout")
cout_global.linkage = "private"
cout_global.initializer = ir.Constant(cout_type, 0)

# --------------------------
# 3. 定义计数器函数（对应always @(posedge clk)）
# --------------------------
# 函数参数：clk(i1), rst_n(i1), en(i1)，返回值void
func_args = [cout_type, cout_type, cout_type]  # i1, i1, i1
# 修复：VoidType() 无参数
func_type = ir.FunctionType(ir.VoidType(), func_args)
counter_func = ir.Function(llvm_module, func_type, name="counter_4bit")

# 命名参数（对应Verilog端口）
arg_names = ["clk", "rst_n", "en"]
for i, arg in enumerate(counter_func.args):
    arg.name = arg_names[i]
clk_arg, rst_n_arg, en_arg = counter_func.args

# --------------------------
# 4. 构建基本块（时序逻辑的核心流程）
# --------------------------
# 基本块1：入口块（判断时钟是否为上升沿）
entry_bb = ir.Block(counter_func, name="entry")
# 基本块2：时钟上升沿逻辑（执行复位/计数）
posedge_bb = ir.Block(counter_func, name="posedge_logic")
# 基本块3：无时钟沿（直接返回）
exit_bb = ir.Block(counter_func, name="exit")

# 修复：IRBuilder无参初始化
builder = ir.IRBuilder()
# 指定初始构建位置为entry_bb
builder.position_at_end(entry_bb)

# 步骤1：判断时钟是否为上升沿（clk == 1）
clk_is_high = builder.icmp_unsigned(
    "==",
    clk_arg,
    ir.Constant(cout_type, 1),
    name="clk_is_high"
)
# 分支：时钟高电平→posedge_bb，否则→exit_bb
builder.cbranch(clk_is_high, posedge_bb, exit_bb)

# --------------------------
# 5. 实现上升沿逻辑（复位+计数）
# --------------------------
builder.position_at_end(posedge_bb)

# 步骤2：加载当前寄存器值（模拟硬件读寄存器）
cnt_load = builder.load(cnt_global, name="cnt_load")
cout_load = builder.load(cout_global, name="cout_load")

# 步骤3：判断同步复位（rst_n == 0）
rst_active = builder.icmp_unsigned(
    "==",
    rst_n_arg,
    ir.Constant(cout_type, 0),
    name="rst_active"
)
# 分支：复位有效→清零，否则→判断使能
rst_bb = ir.Block(counter_func, name="rst_logic")
en_bb = ir.Block(counter_func, name="en_logic")
builder.cbranch(rst_active, rst_bb, en_bb)

# 子块3.1：复位逻辑（cnt=0, cout=0）
builder.position_at_end(rst_bb)
cnt_rst_val = ir.Constant(cnt_type, 0)
cout_rst_val = ir.Constant(cout_type, 0)
builder.store(cnt_rst_val, cnt_global)  # 写回cnt寄存器
builder.store(cout_rst_val, cout_global)  # 写回cout寄存器
# 修复：br → branch（无条件跳转）
builder.branch(exit_bb)  # 跳转到退出块

# 子块3.2：使能判断逻辑
builder.position_at_end(en_bb)
en_active = builder.icmp_unsigned(
    "==",
    en_arg,
    ir.Constant(cout_type, 1),
    name="en_active"
)
# 分支：使能有效→计数，否则→保持状态
count_bb = ir.Block(counter_func, name="count_logic")
hold_bb = ir.Block(counter_func, name="hold_logic")
builder.cbranch(en_active, count_bb, hold_bb)

# 子块3.2.1：计数逻辑（核心）
builder.position_at_end(count_bb)
# 掩码提取cnt低4位（模拟Verilog的[3:0]位宽）
cnt_mask = ir.Constant(cnt_type, 0b1111)  # 4位掩码
cnt_masked = builder.and_(cnt_load, cnt_mask, name="cnt_masked")
# 判断是否计数到最大值（15/0b1111）
cnt_full = builder.icmp_unsigned(
    "==",
    cnt_masked,
    ir.Constant(cnt_type, 0b1111),
    name="cnt_full"
)
# 分支：计数满→清零+置进位，否则→计数+1+清进位
full_bb = ir.Block(counter_func, name="cnt_full_logic")
inc_bb = ir.Block(counter_func, name="cnt_inc_logic")
builder.cbranch(cnt_full, full_bb, inc_bb)

# 子块3.2.1.1：计数满逻辑
builder.position_at_end(full_bb)
cnt_full_val = ir.Constant(cnt_type, 0)
cout_full_val = ir.Constant(cout_type, 1)
builder.store(cnt_full_val, cnt_global)
builder.store(cout_full_val, cout_global)
builder.branch(exit_bb)  # 修复：br → branch

# 子块3.2.1.2：计数+1逻辑
builder.position_at_end(inc_bb)
cnt_inc_val = builder.add(cnt_masked, ir.Constant(cnt_type, 1), name="cnt_inc")
cout_inc_val = ir.Constant(cout_type, 0)
builder.store(cnt_inc_val, cnt_global)
builder.store(cout_inc_val, cout_global)
builder.branch(exit_bb)  # 修复：br → branch

# 子块3.2.2：保持状态逻辑（寄存器值不变）
builder.position_at_end(hold_bb)
builder.store(cnt_load, cnt_global)  # 写回原cnt值
builder.store(cout_load, cout_global)  # 写回原cout值
builder.branch(exit_bb)  # 修复：br → branch

# --------------------------
# 6. 退出块（函数返回）
# --------------------------
builder.position_at_end(exit_bb)
builder.ret_void()

# --------------------------
# 7. 生成IR文件并验证（替代Module.verify()）
# --------------------------
# 打印LLVM IR代码
print("=== 生成的LLVM IR代码 ===")
print(llvm_module)

# 保存IR到文件（指定UTF-8编码避免中文乱码）
ir_file_path = "counter_4bit.ll"
with open(ir_file_path, "w", encoding="utf-8") as f:
    f.write(str(llvm_module))
print(f"\n✅ LLVM IR文件已保存到：{os.path.abspath(ir_file_path)}")

# 可选：用llvm-as验证IR合法性（需安装LLVM工具链）
try:
    # 调用llvm-as检查IR语法
    result = subprocess.run(
        ["llvm-as", ir_file_path, "-o", "counter_4bit.bc"],
        capture_output=True,
        text=True,
        check=True
    )
    print("✅ LLVM IR语法验证通过！")

    # 可选：反汇编查看字节码
    disassemble = subprocess.run(
        ["llvm-dis", "counter_4bit.bc", "-o", "-"],
        capture_output=True,
        text=True,
        check=True
    )
    print("\n=== 反汇编验证结果（字节码→IR）===")
    print(disassemble.stdout)

except FileNotFoundError:
    print("\n⚠️  未找到llvm-as工具，跳过IR验证（请安装LLVM工具链）")
    print("   下载地址：https://llvm.org/releases/")
except subprocess.CalledProcessError as e:
    print(f"\n❌ LLVM IR语法验证失败：{e.stderr}")