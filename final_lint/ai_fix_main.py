#!/usr/bin/env python3
"""
AI Fix Main - Verilog代码AI修复工具

这是一个独立的包装器脚本，集成了verilint_checker和ai_fix_agent。

工作流程：
1. 调用verilint_checker检查代码问题
2. 使用AI分析问题并生成修复
3. 验证修复结果
4. 输出修复报告

使用方法：
    python ai_fix_main.py <verilog_file> [options]

示例：
    # 基础用法
    python ai_fix_main.py my_design.v

    # 带项目路径和include路径
    python ai_fix_main.py my_design.v --project ./rtl -I ./include

    # 指定API密钥和迭代次数
    python ai_fix_main.py my_design.v --ai-key sk-xxx --iterations 5

    # 仅分析不修复（dry-run）
    python ai_fix_main.py my_design.v --analyze-only
"""

import sys
import os
import argparse
import json
from typing import List, Optional, Dict, Any
from datetime import datetime

# 确保可以导入final_lint模块
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from verilint_checker import check_file, check_project, get_cached_project
from ai_fix_agent import AIFixAgent, FixResult


def print_banner():
    """打印程序横幅"""
    print("=" * 70)
    print("Verilint AI Fix Tool")
    print("智能Verilog代码修复助手")
    print("=" * 70)
    print()


def print_section(title: str):
    """打印章节标题"""
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")


def run_initial_check(file_path: str, project_root: Optional[str] = None,
                      include_paths: Optional[List[str]] = None,
                      debug: bool = False) -> List[Dict]:
    """
    运行初始代码检查

    Args:
        file_path: Verilog文件路径
        project_root: 项目根目录
        include_paths: include路径列表
        debug: 是否启用调试模式

    Returns:
        问题列表
    """
    print_section("Step 1: 初始代码检查")
    print(f"检查文件: {file_path}")

    # 获取项目（如果有）
    project = None
    if project_root and os.path.isdir(project_root):
        project = get_cached_project(
            project_root,
            include_paths=include_paths or [],
            recursive=True,
            debug=debug,
            use_cache=True
        )
        print(f"项目模块数: {len(project.modules)}")

    # 运行检查
    raw_issues = check_file(
        file_path,
        project=project,
        include_paths=include_paths or [],
        output_format="json",
        debug=debug
    )

    # 转换为字典列表
    def issue_to_dict(issue):
        if isinstance(issue, dict):
            return issue
        # Handle VerilintIssue object
        severity = getattr(issue, 'severity', '')
        if hasattr(severity, 'value'):
            severity = severity.value
        category = getattr(issue, 'category', '')
        if hasattr(category, 'value'):
            category = category.value
        return {
            'file': getattr(issue, 'file_path', ''),
            'line': getattr(issue, 'line', 0),
            'column': getattr(issue, 'column', 0),
            'code': getattr(issue, 'code', 'UNKNOWN'),
            'message': getattr(issue, 'message', ''),
            'severity': str(severity),
            'category': str(category),
        }

    issues = [issue_to_dict(i) for i in raw_issues]

    print(f"发现问题: {len(issues)} 个")

    if issues:
        # 按错误代码分组
        by_code = {}
        for issue in issues:
            code = issue.get('code', 'UNKNOWN')
            by_code[code] = by_code.get(code, 0) + 1

        print("\n问题统计:")
        for code, count in sorted(by_code.items()):
            print(f"  - {code}: {count} 个")

    return issues


def run_ai_analysis(file_path: str, issues: List[Dict],
                    api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    运行AI问题分析

    Args:
        file_path: 文件路径
        issues: 问题列表
        api_key: API密钥

    Returns:
        分析结果
    """
    print_section("Step 2: AI问题分析")
    print("正在使用AI分析问题真伪...")
    try:
        agent = AIFixAgent(api_key=api_key)

        # 读取代码
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()

        # AI分析
        analysis = agent.analyze_issues(code, issues, file_path)

        # 显示分析结果
        real_errors = [a for a in analysis.get('analysis', [])
                      if a.get('is_real_error', True)]
        false_positives = [a for a in analysis.get('analysis', [])
                          if not a.get('is_real_error', True)]

        print(f"\n分析结果:")
        print(f"  真实错误: {len(real_errors)} 个")
        print(f"  误报: {len(false_positives)} 个")

        if real_errors:
            print("\n真实错误详情:")
            for err in real_errors[:5]:  # 只显示前5个
                print(f"  - [{err.get('issue_code')}] {err.get('reasoning', 'N/A')[:60]}...")

        if false_positives:
            print("\n误报详情:")
            for fp in false_positives[:3]:  # 只显示前3个
                print(f"  - [{fp.get('issue_code')}] {fp.get('reasoning', 'N/A')[:60]}...")

        return analysis

    except Exception as e:
        print(f"AI分析失败: {e}")
        return {"error": str(e), "analysis": []}


def run_ai_fix(file_path: str, project_root: Optional[str] = None,
               include_paths: Optional[List[str]] = None,
               api_key: Optional[str] = None,
               max_iterations: int = 3,
               dry_run: bool = False,
               output_file: Optional[str] = None,
               max_retries: int = 3) -> tuple[FixResult, List[Dict]]:
    """
    运行AI修复（支持多次重试）

    Args:
        file_path: 文件路径
        project_root: 项目根目录
        include_paths: include路径列表
        api_key: API密钥
        max_iterations: 最大迭代次数
        dry_run: 是否仅分析不修复
        output_file: 修复后文件输出路径（默认覆盖原文件）
        max_retries: 最大重试次数（默认3次）

    Returns:
        (修复结果, 重试错误记录列表)
    """
    print_section("Step 3: AI代码修复")

    if dry_run:
        print("模式: 仅分析 (dry-run)")
        return FixResult(
            success=True,
            fixed_code="",
            original_issues=[],
            remaining_issues=[],
            new_issues=[],
            iterations=0,
            message="Dry-run模式，未执行修复"
        ), []

    print(f"最大迭代次数: {max_iterations}")
    print(f"最大重试次数: {max_retries}")
    print("开始AI修复...")
    print()

    retry_errors = []  # 记录每次重试的错误

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            print(f"\n🔄 第 {attempt}/{max_retries} 次重试...")

        try:
            agent = AIFixAgent(api_key=api_key)
            result = agent.fix_file(
                file_path,
                project_root=project_root,
                include_paths=include_paths,
                max_iterations=max_iterations,
                output_file=output_file
            )

            # 如果修复成功或部分成功，返回结果
            if result.success:
                if retry_errors:
                    result.message += f" (经过 {len(retry_errors)} 次重试)"
                return result, retry_errors

            # 修复未成功，记录错误
            error_record = {
                "attempt": attempt,
                "message": result.message,
                "iterations": result.iterations,
                "remaining_issues": len(result.remaining_issues),
                "new_issues": len(result.new_issues)
            }
            retry_errors.append(error_record)

            # 如果不是最后一次尝试，打印错误并继续
            if attempt < max_retries:
                print(f"⚠️ 第 {attempt} 次尝试失败: {result.message}")
                print(f"   剩余问题: {len(result.remaining_issues)} 个")

        except Exception as e:
            error_msg = str(e)
            print(f"❌ 第 {attempt} 次尝试异常: {error_msg}")

            error_record = {
                "attempt": attempt,
                "exception": error_msg,
                "traceback": None
            }

            if attempt == max_retries:
                import traceback
                tb = traceback.format_exc()
                error_record["traceback"] = tb
                print(tb)

            retry_errors.append(error_record)

            if attempt < max_retries:
                print("   准备重试...")

    # 所有重试都失败了
    final_result = FixResult(
        success=False,
        fixed_code="",
        original_issues=[],
        remaining_issues=[],
        new_issues=[],
        iterations=0,
        message=f"修复失败: 经过 {max_retries} 次尝试仍未成功"
    )

    return final_result, retry_errors


def print_fix_report(result: FixResult, file_path: str, output_file: Optional[str] = None):
    """打印修复报告"""
    print_section("Step 4: 修复报告")

    print(f"修复状态: {'✅ 成功' if result.success else '⚠️ 部分成功'}")
    print(f"迭代次数: {result.iterations}")
    print(f"原始问题: {len(result.original_issues)} 个")
    print(f"剩余问题: {len(result.remaining_issues)} 个")
    print(f"新增问题: {len(result.new_issues)} 个")

    if result.new_issues:
        print("\n⚠️ 警告: 修复引入了新的问题:")
        for issue in result.new_issues[:5]:
            print(f"  - [{issue.get('code')}] {issue.get('message', 'N/A')[:50]}...")

    # 计算修复率
    if result.original_issues:
        fixed_count = len(result.original_issues) - len(result.remaining_issues)
        fix_rate = (fixed_count / len(result.original_issues)) * 100
        print(f"\n修复率: {fix_rate:.1f}% ({fixed_count}/{len(result.original_issues)})")

    # 显示每次迭代后的issue变化
    issue_history = getattr(result, 'issue_history', [])
    if issue_history:
        print(f"\n📊 Issue 变化历史:")
        for record in issue_history:
            stage = record.get('stage', 'unknown')
            iteration = record.get('iteration', 0)
            count = record.get('issue_count', 0)
            if stage == 'initial':
                print(f"   初始状态: {count} 个问题")
            elif stage == 'complete_fix':
                print(f"   第 {iteration} 轮: ✅ 全部修复完成")
            elif stage == 'partial_fix':
                print(f"   第 {iteration} 轮后: 剩余 {count} 个问题")
            elif stage == 'final':
                print(f"   最终状态: 剩余 {count} 个问题")

    print(f"\n消息: {result.message}")

    # 确定修复后文件路径
    fixed_file_path = output_file if output_file else file_path

    # 提取备份路径并显示文件位置
    print(f"\n📁 文件位置:")
    print(f"  📄 原始文件: {file_path}")

    if not output_file and "备份:" in result.message:
        backup_start = result.message.find("备份:") + 3
        backup_path = result.message[backup_start:].strip()
        if backup_path and backup_path != "None":
            print(f"  💾 备份文件: {backup_path}")

    print(f"  🔧 修复后文件: {fixed_file_path}")


def save_report(result: FixResult, file_path: str, output_dir: Optional[str] = None,
                output_file: Optional[str] = None, retry_errors: Optional[List[Dict]] = None):
    """保存修复报告到文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"{base_name}_fix_report_{timestamp}.json")
    else:
        report_path = f"{base_name}_fix_report_{timestamp}.json"

    # 从message中提取备份路径
    backup_path = None
    if "备份:" in result.message:
        backup_start = result.message.find("备份:") + 3
        backup_path = result.message[backup_start:].strip()

    # 确定修复后文件路径
    fixed_file_path = output_file if output_file else file_path

    report = {
        "timestamp": timestamp,
        "original_file": file_path,
        "fixed_file": fixed_file_path,  # 修复后的文件位置
        "backup_file": backup_path if not output_file else None,  # 原始文件备份位置
        "success": result.success,
        "iterations": result.iterations,
        "original_issue_count": len(result.original_issues),
        "remaining_issue_count": len(result.remaining_issues),
        "new_issue_count": len(result.new_issues),
        "message": result.message,
        "retry_errors": retry_errors or [],  # 保存三次重试的错误记录
        "issue_history": getattr(result, 'issue_history', []) or [],  # 每次迭代后的issue记录
        "original_issues": result.original_issues,
        "remaining_issues": result.remaining_issues,
        "new_issues": result.new_issues
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 确定修复后文件路径
    fixed_file_path = output_file if output_file else file_path

    print(f"\n📄 详细报告已保存: {report_path}")
    if retry_errors:
        print(f"⚠️  重试记录: {len(retry_errors)} 次")
    if backup_path and not output_file:
        print(f"💾 原始文件备份: {backup_path}")
    print(f"🔧 修复后的文件: {fixed_file_path}")
    return report_path


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Verilog代码AI修复工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s my_design.v
  %(prog)s my_design.v --project ./rtl -I ./include
  %(prog)s my_design.v --ai-key sk-xxx --iterations 5
  %(prog)s my_design.v --analyze-only
  %(prog)s my_design.v -o ./fixed_files/
  %(prog)s my_design.v --retries 3
        """
    )

    parser.add_argument('file', help='要修复的Verilog文件')
    parser.add_argument('--project', '-p', help='项目根目录（用于模块解析）')
    parser.add_argument('-I', '--include', dest='include_paths',
                        action='append', default=[],
                        help='Include路径（可多次使用）')
    parser.add_argument('--ai-key', help='AI API密钥（默认从环境变量读取）')
    parser.add_argument('--iterations', type=int, default=1,
                        help='最大修复迭代次数（默认: 3）')
    parser.add_argument('--retries', type=int, default=1,
                        help='修复失败时最大重试次数（默认: 3）')
    parser.add_argument('--analyze-only', action='store_true',
                        help='仅分析问题，不执行修复')
    parser.add_argument('--output-dir', '-o',
                        help='修复后文件和报告的输出目录（保持原文件名）')
    parser.add_argument('--debug', action='store_true',
                        help='启用调试模式')
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')

    args = parser.parse_args()

    print_banner()

    # 检查文件是否存在
    if not os.path.exists(args.file):
        print(f"❌ 错误: 文件不存在: {args.file}")
        sys.exit(1)

    # 获取API密钥
    api_key = args.ai_key or os.getenv("OPENAI_API_KEY")
    if not args.analyze_only and not api_key:
        print("⚠️ 警告: 未提供AI API密钥")
        print("  请使用 --ai-key 参数或设置 OPENAI_API_KEY 环境变量")
        print("  将只执行检查，不进行AI修复")
        print()

    try:
        # Step 1: 初始检查
        issues = run_initial_check(
            args.file,
            project_root=args.project,
            include_paths=args.include_paths,
            debug=args.debug
        )

        if not issues:
            print("\n✅ 没有发现任何问题，无需修复！")
            sys.exit(0)

        # Step 2: AI分析（如果有API密钥）
        # if api_key:
        #     analysis = run_ai_analysis(args.file, issues, api_key)

        #     # 检查分析是否出错
        #     if analysis.get('error'):
        #         print(f"\n⚠️ AI分析出错: {analysis.get('error')}")
        #         print("将继续执行修复流程...")
        #         analysis['analysis'] = []  # 出错时假设所有问题都需要修复

        #     # 检查是否有真实错误
        #     real_errors = [a for a in analysis.get('analysis', [])
        #                   if a.get('is_real_error', True)]

        #     if not real_errors and analysis.get('analysis'):
        #         # analysis不为空但real_errors为空，说明都是误报
        #         print("\n✅ AI分析发现所有问题都是误报，无需修复！")
        #         sys.exit(0)
        #     elif not analysis.get('analysis') and not analysis.get('error'):
        #         # analysis为空且没有错误，说明解析失败
        #         print("\n⚠️ AI分析结果为空，将尝试修复所有问题")
        # else:
        #     analysis = None

        # 计算输出文件路径
        output_file = None
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            # 保持原文件名，输出到指定目录
            base_name = os.path.basename(args.file)
            output_file = os.path.join(args.output_dir, base_name)

        # Step 3: AI修复
        result, retry_errors = run_ai_fix(
            args.file,
            project_root=args.project,
            include_paths=args.include_paths,
            api_key=api_key,
            max_iterations=args.iterations,
            dry_run=args.analyze_only,
            output_file=output_file,
            max_retries=args.retries
        )

        # Step 4: 打印报告
        print_fix_report(result, args.file, output_file)

        # 如果有重试错误，打印摘要
        if retry_errors:
            print(f"\n⚠️  修复过程中发生了 {len(retry_errors)} 次重试:")
            for err in retry_errors:
                print(f"   第 {err['attempt']} 次: {err.get('message', err.get('exception', '未知错误'))}")

        # Step 5: 保存报告
        report_path = save_report(result, args.file, args.output_dir, output_file, retry_errors)

        # 返回退出码
        if result.success and len(result.remaining_issues) == 0:
            print("\n🎉 所有问题已修复！")
            sys.exit(0)
        elif result.success:
            print("\n⚠️ 部分问题已修复，仍有剩余问题")
            sys.exit(1)
        else:
            print("\n❌ 修复失败")
            sys.exit(2)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    main()
