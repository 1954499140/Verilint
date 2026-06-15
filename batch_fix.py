#!/usr/bin/env python3
"""
批量修复脚本 - 修复 finish 目录中的所有 Verilog 文件

使用方法:
    python batch_fix.py [options]

示例:
    # 基础用法（修复所有.v文件到fixed目录）
    python batch_fix.py

    # 指定API密钥
    python batch_fix.py --ai-key sk-xxx

    # 指定迭代次数
    python batch_fix.py --iterations 5

    # 仅分析不修复
    python batch_fix.py --analyze-only
"""

import os
import sys
import argparse
import subprocess
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple


def find_verilog_files(directory: str) -> List[str]:
    """
    递归查找目录中的所有 .v 文件

    Args:
        directory: 要搜索的目录

    Returns:
        .v 文件路径列表
    """
    verilog_files = []
    for root, dirs, files in os.walk(directory):
        # 跳过 .git 等隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for file in files:
            if file.endswith('.v'):
                full_path = os.path.join(root, file)
                verilog_files.append(full_path)

    return sorted(verilog_files)


def run_ai_fix(file_path: str, output_dir: str, ai_key: str = None,
               iterations: int = 3, analyze_only: bool = False,
               project_root: str = None) -> Dict:
    """
    对单个文件运行 AI 修复

    Args:
        file_path: Verilog 文件路径
        output_dir: 输出目录
        ai_key: API 密钥
        iterations: 最大迭代次数
        analyze_only: 是否仅分析
        project_root: 项目根目录

    Returns:
        修复结果信息
    """
    # 构建命令
    cmd = [
        sys.executable,
        'final_lint/ai_fix_main.py',
        file_path,
        '-o', output_dir,
        '--iterations', str(iterations)
    ]

    if ai_key:
        cmd.extend(['--ai-key', ai_key])

    if analyze_only:
        cmd.append('--analyze-only')

    if project_root:
        cmd.extend(['--project', project_root])

    # 创建文件特定的子目录
    file_name = os.path.basename(file_path)
    file_stem = os.path.splitext(file_name)[0]
    file_output_dir = os.path.join(output_dir, file_stem)
    os.makedirs(file_output_dir, exist_ok=True)

    # 更新输出目录
    cmd[cmd.index('-o') + 1] = file_output_dir

    print(f"\n{'='*70}")
    print(f"处理文件: {file_path}")
    print(f"输出目录: {file_output_dir}")
    print(f"{'='*70}")

    # 运行命令
    start_time = datetime.now()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',  # 处理编码错误
            timeout=300  # 5分钟超时
        )
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        # 解析结果
        success = result.returncode == 0
        partial_success = result.returncode == 1

        # 保存完整日志
        log_file = os.path.join(file_output_dir, f"{file_stem}_log.txt")
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"Return code: {result.returncode}\n")
            f.write(f"Duration: {duration:.2f}s\n")
            f.write(f"\n{'='*50}\nSTDOUT:\n{'='*50}\n")
            f.write(result.stdout if result.stdout else "")
            f.write(f"\n{'='*50}\nSTDERR:\n{'='*50}\n")
            f.write(result.stderr if result.stderr else "")

        return {
            'file': file_path,
            'success': success,
            'partial_success': partial_success,
            'return_code': result.returncode,
            'duration': duration,
            'output_dir': file_output_dir,
            'stdout': result.stdout[-1000:] if len(result.stdout) > 1000 else result.stdout,  # 只保存最后1000字符
            'stderr': result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
        }

    except subprocess.TimeoutExpired:
        print(f"❌ 超时: {file_path}")
        return {
            'file': file_path,
            'success': False,
            'partial_success': False,
            'return_code': -1,
            'duration': 300,
            'output_dir': file_output_dir,
            'stdout': '',
            'stderr': 'Timeout after 300 seconds'
        }

    except Exception as e:
        print(f"❌ 错误: {file_path} - {e}")
        return {
            'file': file_path,
            'success': False,
            'partial_success': False,
            'return_code': -2,
            'duration': 0,
            'output_dir': file_output_dir,
            'stdout': '',
            'stderr': str(e)
        }


def print_summary(results: List[Dict], total_time: float):
    """打印修复摘要"""
    print(f"\n{'='*70}")
    print("批量修复完成")
    print(f"{'='*70}")

    total = len(results)
    success = sum(1 for r in results if r['success'])
    partial = sum(1 for r in results if r['partial_success'])
    failed = total - success - partial

    print(f"\n处理统计:")
    print(f"  总文件数: {total}")
    print(f"  ✅ 完全修复: {success}")
    print(f"  ⚠️  部分修复: {partial}")
    print(f"  ❌ 失败/超时: {failed}")
    print(f"  总耗时: {total_time:.2f}秒")
    print(f"  平均耗时: {total_time/total:.2f}秒/文件" if total > 0 else "")

    if failed > 0:
        print(f"\n失败的文件:")
        for r in results:
            if not r['success'] and not r['partial_success']:
                print(f"  - {r['file']}")

    if partial > 0:
        print(f"\n部分修复的文件:")
        for r in results:
            if r['partial_success']:
                print(f"  - {r['file']}")


def save_summary_report(results: List[Dict], output_dir: str, total_time: float):
    """保存汇总报告"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"batch_fix_summary_{timestamp}.json")

    summary = {
        'timestamp': timestamp,
        'total_files': len(results),
        'success_count': sum(1 for r in results if r['success']),
        'partial_count': sum(1 for r in results if r['partial_success']),
        'failed_count': len(results) - sum(1 for r in results if r['success']) - sum(1 for r in results if r['partial_success']),
        'total_time': total_time,
        'results': results
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n📄 汇总报告已保存: {report_path}")


def main():
    parser = argparse.ArgumentParser(
        description='批量修复 Verilog 文件',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s
  %(prog)s --ai-key sk-xxx --iterations 5
  %(prog)s --analyze-only
  %(prog)s --input-dir ./my_verilog --output-dir ./my_fixed
        """
    )

    parser.add_argument('--input-dir', '-i',
                        default='finish',
                        help='输入目录（默认: finish）')
    parser.add_argument('--output-dir', '-o',
                        default='fixed',
                        help='输出目录（默认: fixed）')
    parser.add_argument('--ai-key',
                        help='AI API密钥（默认从环境变量读取）',default="sk-f3c469e44a4f4defadceda3cdac3e21d")
    parser.add_argument('--iterations', type=int, default=3,
                        help='最大修复迭代次数（默认: 3）')
    parser.add_argument('--analyze-only', action='store_true',
                        help='仅分析问题，不执行修复')
    parser.add_argument('--project', '-p',
                        help='项目根目录（用于模块解析）')

    args = parser.parse_args()

    # 检查输入目录
    if not os.path.exists(args.input_dir):
        print(f"❌ 错误: 输入目录不存在: {args.input_dir}")
        sys.exit(1)

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    # 查找所有 .v 文件
    print(f"正在查找 {args.input_dir} 中的 .v 文件...")
    verilog_files = find_verilog_files(args.input_dir)

    if not verilog_files:
        print(f"⚠️ 未在 {args.input_dir} 中找到任何 .v 文件")
        sys.exit(0)

    print(f"找到 {len(verilog_files)} 个 Verilog 文件")

    # 获取 API 密钥
    ai_key = args.ai_key or os.getenv("OPENAI_API_KEY")
    if not ai_key and not args.analyze_only:
        print("⚠️ 警告: 未提供 AI API 密钥，将只执行分析")
        print("  请使用 --ai-key 参数或设置 OPENAI_API_KEY 环境变量")
        return

    # 处理所有文件
    results = []
    start_time = datetime.now()

    try:
        for i, file_path in enumerate(verilog_files, 1):
            print(f"\n\n进度: {i}/{len(verilog_files)} ({i/len(verilog_files)*100:.1f}%)")

            result = run_ai_fix(
                file_path=file_path,
                output_dir=args.output_dir,
                ai_key=ai_key,
                iterations=args.iterations,
                analyze_only=args.analyze_only,
                project_root=args.project
            )
            results.append(result)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")

    finally:
        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        # 打印摘要
        print_summary(results, total_time)

        # 保存汇总报告
        save_summary_report(results, args.output_dir, total_time)


if __name__ == "__main__":
    main()
