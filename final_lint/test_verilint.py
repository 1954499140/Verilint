"""
测试 Verilint 综合检查器
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verilint_checker import check_file

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verilint Checker Test")
    parser.add_argument("--file", help="Verilog file to check")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--lsp", action="store_true", help="Output LSP format")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")

    args = parser.parse_args()
    #
    output_format = "text"
    if args.json:
        output_format = "json"
    elif args.lsp:
        output_format = "lsp"

    # 检查文件
    issues = check_file('../finish/32-verilog-project/Booth.v', debug=args.debug, output_format=output_format)

    print(f"\n\nTotal issues found: {len(issues)}")
