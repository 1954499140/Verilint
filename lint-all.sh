#!/bin/bash

# 配置
CHECK_DIR="./example"
REPORT_FILE="verilator_lint_result.txt"

# 清空报告
echo "===== Verilator 静态检查报告 - $(date) =====" > "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# 检查目录
if [ ! -d "$CHECK_DIR" ]; then
    echo "错误：$CHECK_DIR 不存在"
    exit 1
fi

echo "===== 按文件夹顺序检查 example 目录 ====="
echo "结果保存到：$REPORT_FILE"
echo ""

# 【关键】按文件夹+文件自然顺序遍历，不乱序
find "$CHECK_DIR" -type f \( -name "*.v" -o -name "*.sv" \) | sort | while read -r file; do
    filename=$(basename "$file")
    filepath=$(dirname "$file")

    echo "========== 检查：$filepath/$filename =========="
    echo "========== 检查文件：$filepath/$filename ==========" >> "$REPORT_FILE"

    # 执行 Verilator 检查
    verilator --lint-only -Wall "$file" > temp.log 2>&1
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