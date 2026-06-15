import os
import sys

def is_verilog_file(filename):
    return filename.endswith(".v") 


def count_file(filepath):
    total = 0
    blank = 0
    comment = 0
    code = 0

    in_block_comment = False

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            total += 1
            stripped = line.strip()

            # 空行
            if not stripped:
                blank += 1
                continue

            # 处理块注释 /* ... */
            if in_block_comment:
                comment += 1
                if "*/" in stripped:
                    in_block_comment = False
                continue

            if stripped.startswith("/*"):
                comment += 1
                if "*/" not in stripped:
                    in_block_comment = True
                continue

            # 单行注释 //
            if stripped.startswith("//"):
                comment += 1
                continue

            # 行内注释（代码 + 注释）
            if "//" in stripped:
                code += 1
                continue

            # 普通代码
            code += 1

    return total, blank, comment, code


def analyze_project(root_dir):
    total_all = blank_all = comment_all = code_all = 0

    print(f"\nScanning: {root_dir}\n")

    for root, dirs, files in os.walk(root_dir):
        for file in files:
            if is_verilog_file(file):
                path = os.path.join(root, file)

                t, b, c, cd = count_file(path)

                total_all += t
                blank_all += b
                comment_all += c
                code_all += cd

                print(f"{path}")
                print(f"  Total: {t}, Code: {cd}, Comment: {c}, Blank: {b}\n")

    print("========== SUMMARY ==========")
    print(f"Total lines   : {total_all}")
    print(f"Code lines    : {code_all}")
    print(f"Comment lines : {comment_all}")
    print(f"Blank lines   : {blank_all}")
    print("=============================\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python count_verilog_lines.py <project_dir>")
        sys.exit(1)

    analyze_project(sys.argv[1])