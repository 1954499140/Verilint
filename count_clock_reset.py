#!/usr/bin/env python3
"""
Clock, Reset and Register Signal Counter - 时钟、复位和寄存器信号统计工具

统计项目中的时钟、复位和寄存器(reg)信号数量
"""

import sys
import os
import argparse
from typing import Dict, List, Set, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'final_lint'))

from pyverilog.vparser.parser import parse
from final_lint.symbol_table_builder import SymbolTableBuilder
from symbol import Symbol


def count_clock_reset_in_file(file_path: str, debug: bool = False,
                               include_paths: List[str] = None) -> Dict[str, any]:
    """
    统计单个文件中的时钟和复位信号

    Args:
        file_path: Verilog 文件路径
        debug: 是否启用调试模式
        include_paths: 可选的 include 路径列表

    Returns:
        {
            'file': str,
            'clocks': List[Tuple[str, int]],      # [(name, line), ...]
            'async_resets': List[Tuple[str, int]], # [(name, line), ...]
            'sync_resets': List[Tuple[str, int]],  # [(name, line), ...]
            'regs': List[Tuple[str, int]],         # [(name, line), ...]
            'clock_count': int,
            'async_reset_count': int,
            'sync_reset_count': int,
            'reg_count': int
        }
    """
    try:
        ast, _ = parse([file_path], preprocess_include=include_paths)
        stb = SymbolTableBuilder()
        stb.build(ast)

        clocks = []
        async_resets = []
        sync_resets = []
        regs = []

        for scope in stb.all_scopes:
            for name, symbol in scope.symbols.items():
                if symbol.is_clock:
                    clocks.append((name, symbol.lineno))
                if symbol.is_async_reset:
                    async_resets.append((name, symbol.lineno))
                if symbol.is_sync_reset:
                    sync_resets.append((name, symbol.lineno))
                if symbol.is_reg_type():
                    regs.append((name, symbol.lineno))

        return {
            'file': file_path,
            'clocks': clocks,
            'async_resets': async_resets,
            'sync_resets': sync_resets,
            'regs': regs,
            'clock_count': len(clocks),
            'async_reset_count': len(async_resets),
            'sync_reset_count': len(sync_resets),
            'reg_count': len(regs)
        }

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return {
            'file': file_path,
            'clocks': [],
            'async_resets': [],
            'sync_resets': [],
            'regs': [],
            'clock_count': 0,
            'async_reset_count': 0,
            'sync_reset_count': 0,
            'reg_count': 0,
            'error': str(e)
        }


def count_clock_reset_in_project(project_path: str, recursive: bool = True,
                                  debug: bool = False,
                                  include_paths: List[str] = None) -> Dict[str, any]:
    """
    统计整个项目中的时钟和复位信号

    Args:
        project_path: 项目路径
        recursive: 是否递归搜索子目录
        debug: 是否启用调试模式

    Returns:
        统计结果字典
    """
    # 收集所有 Verilog 文件
    verilog_files = []

    if recursive:
        for root, dirs, files in os.walk(project_path):
            # 跳过 .git 等隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file in files:
                if file.endswith(('.v', '.sv')):
                    verilog_files.append(os.path.join(root, file))
                # 跳过 .vh/.svh 头文件，它们不是独立可解析的 Verilog 模块
    else:
        for file in os.listdir(project_path):
            if file.endswith(('.v', '.sv')):
                verilog_files.append(os.path.join(project_path, file))

    if not verilog_files:
        print(f"No Verilog files found in {project_path}")
        return {}

    print(f"Found {len(verilog_files)} Verilog files")
    if debug:
        for f in verilog_files[:10]:
            print(f"  - {f}")
        if len(verilog_files) > 10:
            print(f"  ... and {len(verilog_files) - 10} more")

    # 统计每个文件
    results = []
    total_clocks = 0
    total_async_resets = 0
    total_sync_resets = 0
    total_regs = 0

    for i, file_path in enumerate(verilog_files, 1):
        if debug:
            print(f"\n[{i}/{len(verilog_files)}] Processing: {file_path}")
        else:
            # 显示进度
            if i % 10 == 0 or i == len(verilog_files):
                print(f"Progress: {i}/{len(verilog_files)} files processed...", end='\r')

        result = count_clock_reset_in_file(file_path, debug=debug,
                                            include_paths=include_paths)
        results.append(result)

        total_clocks += result['clock_count']
        total_async_resets += result['async_reset_count']
        total_sync_resets += result['sync_reset_count']
        total_regs += result['reg_count']

    print()  # 换行

    # 汇总统计
    summary = {
        'project_path': project_path,
        'total_files': len(verilog_files),
        'files_with_signals': len([r for r in results if r['clock_count'] > 0 or r['async_reset_count'] > 0 or r['sync_reset_count'] > 0 or r['reg_count'] > 0]),
        'total_clocks': total_clocks,
        'total_async_resets': total_async_resets,
        'total_sync_resets': total_sync_resets,
        'total_resets': total_async_resets + total_sync_resets,
        'total_regs': total_regs,
        'file_results': results
    }

    return summary


def print_summary(summary: Dict[str, any], verbose: bool = False):
    """打印统计结果"""
    if not summary:
        print("No data to display")
        return

    print("\n" + "=" * 70)
    print("Clock, Reset and Register Signal Statistics")
    print("=" * 70)

    print(f"\nProject Path: {summary['project_path']}")
    print(f"Total Files: {summary['total_files']}")
    print(f"Files with Signals: {summary['files_with_signals']}")

    print("\n" + "-" * 70)
    print("Summary Counts")
    print("-" * 70)
    print(f"  Clock Signals:        {summary['total_clocks']}")
    print(f"  Async Reset Signals:  {summary['total_async_resets']}")
    print(f"  Sync Reset Signals:   {summary['total_sync_resets']}")
    print(f"  Total Reset Signals:  {summary['total_resets']}")
    print(f"  Register Signals:     {summary['total_regs']}")

    if verbose:
        print("\n" + "-" * 70)
        print("Detailed Breakdown by File")
        print("-" * 70)

        for result in summary['file_results']:
            has_signals = (result['clock_count'] > 0 or
                          result['async_reset_count'] > 0 or
                          result['sync_reset_count'] > 0)

            has_signals = (result['clock_count'] > 0 or
                          result['async_reset_count'] > 0 or
                          result['sync_reset_count'] > 0 or
                          result['reg_count'] > 0)

            if has_signals:
                print(f"\nFile: {result['file']}")

                if result['clocks']:
                    print(f"  Clocks ({result['clock_count']}):")
                    for name, line in result['clocks']:
                        print(f"    - {name} (line {line})")

                if result['async_resets']:
                    print(f"  Async Resets ({result['async_reset_count']}):")
                    for name, line in result['async_resets']:
                        print(f"    - {name} (line {line})")

                if result['sync_resets']:
                    print(f"  Sync Resets ({result['sync_reset_count']}):")
                    for name, line in result['sync_resets']:
                        print(f"    - {name} (line {line})")

                if result['regs']:
                    print(f"  Registers ({result['reg_count']}):")
                    for name, line in result['regs']:
                        print(f"    - {name} (line {line})")

    print("\n" + "=" * 70)


def export_to_csv(summary: Dict[str, any], output_file: str):
    """导出统计结果到 CSV"""
    import csv

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['File', 'Signal Type', 'Signal Name', 'Line Number'])

        for result in summary['file_results']:
            file_path = result['file']

            for name, line in result['clocks']:
                writer.writerow([file_path, 'Clock', name, line])

            for name, line in result['async_resets']:
                writer.writerow([file_path, 'Async Reset', name, line])

            for name, line in result['sync_resets']:
                writer.writerow([file_path, 'Sync Reset', name, line])

            for name, line in result['regs']:
                writer.writerow([file_path, 'Register', name, line])

    print(f"\nResults exported to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Clock and Reset Signal Counter for Verilog Projects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s ./rtl
  %(prog)s ./rtl --verbose
  %(prog)s ./rtl --output stats.csv
  %(prog)s ./rtl --no-recursive --debug
  %(prog)s ./rtl -I ./rtl -I ./common
  %(prog)s ./rtl --include ./common
        """
    )

    parser.add_argument('path', help='Project path or Verilog file')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed breakdown for each file')
    parser.add_argument('-o', '--output', help='Export results to CSV file')
    parser.add_argument('-I', '--include', action='append', dest='include_paths',
                        help='Add include path (can be specified multiple times)')
    parser.add_argument('--no-recursive', action='store_true',
                        help='Do not search subdirectories')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')

    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"Error: Path does not exist: {args.path}")
        sys.exit(1)

    if os.path.isfile(args.path):
        # 单个文件
        result = count_clock_reset_in_file(args.path, debug=args.debug,
                                            include_paths=args.include_paths)
        summary = {
            'project_path': args.path,
            'total_files': 1,
            'files_with_signals': 1 if result['clock_count'] > 0 or result['async_reset_count'] > 0 or result['sync_reset_count'] > 0 or result['reg_count'] > 0 else 0,
            'total_clocks': result['clock_count'],
            'total_async_resets': result['async_reset_count'],
            'total_sync_resets': result['sync_reset_count'],
            'total_resets': result['async_reset_count'] + result['sync_reset_count'],
            'total_regs': result['reg_count'],
            'file_results': [result]
        }
    else:
        # 项目目录
        summary = count_clock_reset_in_project(
            args.path,
            recursive=not args.no_recursive,
            debug=args.debug,
            include_paths=args.include_paths
        )

    print_summary(summary, verbose=args.verbose)

    if args.output:
        export_to_csv(summary, args.output)


if __name__ == "__main__":
    main()
