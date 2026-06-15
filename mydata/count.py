import os

# ====================== 配置项 ======================
TARGET_FOLDER = "."  # 要统计的文件夹（. 表示当前文件夹）
# 需要统计的代码文件后缀（根据你的项目修改）
CODE_SUFFIX = [".v", ".vh", ".sv", ".svh"]  # Verilog / SystemVerilog
# CODE_SUFFIX = [".py", ".java", ".c", ".cpp", ".h"]  # 通用代码
# ====================================================

total_lines = 0
total_code_lines = 0
total_comment_lines = 0
total_blank_lines = 0
file_count = 0

def count_lines(file_path):
    global total_lines, total_code_lines, total_comment_lines, total_blank_lines
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except:
        return

    file_lines = len(lines)
    code_lines = 0
    comment_lines = 0
    blank_lines = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_lines += 1
        elif stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            comment_lines += 1
        else:
            code_lines += 1

    total_lines += file_lines
    total_code_lines += code_lines
    total_comment_lines += comment_lines
    total_blank_lines += blank_lines

    print(f"📄 {os.path.basename(file_path)} | 总行 {file_lines} | 代码 {code_lines} | 注释 {comment_lines} | 空行 {blank_lines}")

# 遍历文件夹
for root, dirs, files in os.walk(TARGET_FOLDER):
    for file in files:
        if any(file.endswith(suf) for suf in CODE_SUFFIX):
            file_path = os.path.join(root, file)
            file_count += 1
            count_lines(file_path)

# 输出结果
print("\n" + "="*50)
print(f"📊 统计完成！共扫描文件：{file_count} 个")
print(f"📝 总行数：{total_lines}")
print(f"💻 有效代码行：{total_code_lines}")
print(f"✏️ 注释行：{total_comment_lines}")
print(f"⏳ 空行：{total_blank_lines}")
print("="*50)