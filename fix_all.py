#!/usr/bin/env python3
"""批量修复 finish 目录中的所有 .v 文件"""

import os
import subprocess
import sys

# 配置
INPUT_DIR = "finish/ai_test"
OUTPUT_DIR = "fixed_files"
API_KEY = "sk-f3c469e44a4f4defadceda3cdac3e21d"  # 或从环境变量读取

def find_v_files(directory):
    """递归查找所有 .v 文件"""
    v_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.v'):
                v_files.append(os.path.join(root, file))
    return sorted(v_files)

def main():
    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 查找所有 .v 文件
    files = find_v_files(INPUT_DIR)
    print(f"找到 {len(files)} 个 .v 文件")

    # 逐个处理
    for i, file_path in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] 处理: {file_path}")

        cmd = [
            sys.executable,
            "final_lint/ai_fix_main.py",
            file_path,
            "-o", OUTPUT_DIR,
            "--ai-key", API_KEY,
            "--iterations", "1"
        ]

        try:
            subprocess.run(cmd, check=False, encoding='utf-8', errors='replace')
        except Exception as e:
            print(f"错误: {e}")

    print(f"\n完成！输出目录: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
