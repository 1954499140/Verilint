#!/bin/bash

# 配置
CHECK_DIR="./example2"
REPORT_FILE="yosys_lint_result.txt"

# 清空报告
echo "===== Yosys 静态检查报告 - $(date) =====" > "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# 检查目录
if [ ! -d "$CHECK_DIR" ]; then
    echo "错误：$CHECK_DIR 不存在"
    exit 1
fi

echo "===== 按文件夹顺序检查 example 目录 ====="
echo "结果保存到：$REPORT_FILE"
echo ""

# 按文件夹顺序遍历所有 .v .sv 文件
find "$CHECK_DIR" -type f \( -name "*.v" -o -name "*.sv" \) | sort | while read -r file; do
    filename=$(basename "$file")
    filepath=$(dirname "$file")

    echo "========== 检查：$filepath/$filename =========="
    echo "========== 检查文件：$filepath/$filename ==========" >> "$REPORT_FILE"

    # Yosys 静态检查（语法 + 解析 + 高级检查）
    yosys -p "read_verilog -sv $file; check; dump -o /dev/null" > temp.log 2>&1
    result=$?

    # 写入报告
    cat temp.log >> "$REPORT_FILE"

    # 输出结果标记
    if [ $result -ne 0 ]; then
        echo -e "\033[31m$filename 【有报错/警告】\033[0m"
        echo "结果：$filename 【存在错误/警告】" >> "$REPORT_FILE"
    else
        echo -e "\033[32m$filename 【检查通过】\033[0m"
        echo "结果：$filename 【无警告】" >> "$REPORT_FILE"
    fi

    echo "" >> "$REPORT_FILE"
    echo ""
done

# 清理临时文件
rm -f temp.log

echo -e "\033[32m===== 全部检查完成！报告：$REPORT_FILE =====\033[0m"