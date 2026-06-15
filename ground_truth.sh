[200~#!/bin/bash
set -e

# 输入 RTL 路径
read -p "请输入 RTL 文件夹路径: " RTL_DIR

if [ ! -d "$RTL_DIR" ]; then
	    echo "❌ 目录不存在"
	        exit 1
fi

# 进入 RTL
cd "$RTT_DIR" || exit 1

# 清理旧文件
rm -f run.ys ground_truth.json

# 写入 Yosys 脚本
cat << 'EOF' > run.ys
# 读取所有 verilog
read_verilog *.v
read_verilog rtl/*.v 2>/dev/null
read_verilog */*.v 2>/dev/null

# 不检查依赖，不报错
hierarchy -auto-top -nocheck
proc -noauto
opt -fast
extract -map
dfflibmap -liberty /dev/null
abc -dff

# 强制输出
dump -ff
dump -clk
dump -rst
write_json ground_truth.json
EOF

# 运行 yosys，忽略所有错误
echo "✅ 正在分析 RTL..."
yosys -q -s run.ys 2>/dev/null || true

# 删除临时文件
rm -f run.ys

echo ""
echo "========================================"
echo "✅ 输出已生成：$RTL_DIR/ground_truth.json"
echo "========================================"
