import os
import json
import subprocess
import tempfile
import re
from collections import defaultdict

YOSYS_BIN = "yosys"


# ========= RTL过滤 =========
def is_rtl_file(filepath):
    filename = os.path.basename(filepath).lower()

    # ===== 1️⃣ 文件名过滤 =====
    if any(k in filename for k in ["tb", "test", "sim"]):
        return False

    try:
        with open(filepath, "r", errors="ignore") as f:
            content = f.read().lower()

            # ===== 2️⃣ testbench特征 =====
            if "initial" in content:
                return False
            if "$display" in content:
                return False
            if "$finish" in content:
                return False

            # ===== 3️⃣ 必须包含RTL结构 =====
            if "always" not in content:
                return False

    except:
        return False

    return True


def find_rtl_files(project_path):
    rtl_files = []

    for root, _, files in os.walk(project_path):
        for f in files:
            # ===== 只要 .v =====
            if f.endswith(".v"):
                fullpath = os.path.join(root, f)

                if is_rtl_file(fullpath):
                    rtl_files.append(fullpath)

    return rtl_files


# ========= Yosys脚本 =========
def generate_ys_script(rtl_files, json_out):
    lines = []

    for f in rtl_files:
        # ⚠️ WSL必须加引号
        lines.append(f'read_verilog "{f}"')

    lines += [
        "hierarchy -auto-top",
        "proc",
        "opt_clean",
        f'write_json "{json_out}"'
    ]

    return "\n".join(lines)


def run_yosys(ys_path):
    try:
        subprocess.run([YOSYS_BIN, ys_path], check=True)
    except subprocess.CalledProcessError:
        print("❌ Yosys 执行失败")
        exit(1)


# ========= 识别规则 =========
def is_reset(name):
    return re.search(r"rst|reset", name, re.IGNORECASE)


# ========= 分析 =========
def analyze_json(json_file):
    with open(json_file) as f:
        data = json.load(f)

    clocks = defaultdict(int)
    resets = defaultdict(int)

    modules = data.get("modules", {})

    for mname, module in modules.items():
        processes = module.get("processes", {})

        for pname, proc in processes.items():
            syncs = proc.get("syncs", [])

            for sync in syncs:
                stype = sync.get("type")
                signal = str(sync.get("signal"))

                # ===== clock =====
                if stype in ["posedge", "negedge"]:
                    clocks[signal] += 1

                # ===== reset =====
                elif stype == "level":
                    if is_reset(signal):
                        resets[signal] += 1

    return clocks, resets


# ========= 主函数 =========
def analyze_project(project_path):
    print(f"🔍 分析路径: {project_path}")

    rtl_files = find_rtl_files(project_path)

    if not rtl_files:
        print("❌ 没有找到 RTL (.v) 文件")
        return

    print(f"📁 RTL 文件数量: {len(rtl_files)}")

    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = os.path.join(tmpdir, "design.json")
        ys_file = os.path.join(tmpdir, "run.ys")

        ys_script = generate_ys_script(rtl_files, json_file)

        with open(ys_file, "w") as f:
            f.write(ys_script)

        run_yosys(ys_file)

        clocks, resets = analyze_json(json_file)

    # ========= 输出 =========
    print("\n=== Clock Signals ===")
    for clk, cnt in sorted(clocks.items(), key=lambda x: -x[1]):
        print(f"{clk} (出现 {cnt} 次)")

    print("\n=== Reset Signals ===")
    for rst, cnt in sorted(resets.items(), key=lambda x: -x[1]):
        print(f"{rst} (出现 {cnt} 次)")


# ========= CLI =========
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("用法: python3 analyze.py <项目路径>")
        exit(1)

    analyze_project(sys.argv[1])
