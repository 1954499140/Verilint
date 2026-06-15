#!/usr/bin/env python3
"""
Yosys-based Clock and Reset Signal Counter
使用 Yosys 遍历 Verilog 项目并统计时钟和复位信号

特性:
- 使用 Yosys 解析 Verilog，支持更复杂的语法
- 自动识别时钟信号（通过分析触发器连接）
- 自动识别异步/同步复位信号
- 支持 include 路径
- 生成详细报告
"""

import os
import sys
import argparse
import subprocess
import json
import tempfile
import platform
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict


def is_wsl_available(distro: str = None) -> bool:
    """检查 WSL 是否可用"""
    try:
        cmd = ['wsl', '--version']
        if distro:
            cmd = ['wsl', '-d', distro, '--version']
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except:
        return False


def get_wsl_distros() -> List[str]:
    """获取可用的 WSL 发行版列表"""
    try:
        result = subprocess.run(['wsl', '-l', '-q'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # 解析输出，去除空行和特殊字符
            distros = [line.strip().strip('\x00') for line in result.stdout.strip().split('\n') if line.strip()]
            return distros
    except:
        pass
    return []


def to_wsl_path(win_path: str) -> str:
    """将 Windows 路径转换为 WSL 路径"""
    # 处理路径，例如 E:\path -> /mnt/e/path
    abs_path = os.path.abspath(win_path)
    if len(abs_path) >= 2 and abs_path[1] == ':':
        # Windows 绝对路径
        drive = abs_path[0].lower()
        rest = abs_path[2:].replace('\\', '/')
        return f"/mnt/{drive}{rest}"
    return abs_path.replace('\\', '/')


def to_windows_path(wsl_path: str) -> str:
    """将 WSL 路径转换回 Windows 路径"""
    if wsl_path.startswith('/mnt/'):
        parts = wsl_path.split('/')
        if len(parts) >= 3:
            drive = parts[2].upper()
            rest = '\\'.join(parts[3:])
            return f"{drive}:\\{rest}"
    return wsl_path.replace('/', '\\')


class YosysClockResetAnalyzer:
    """使用 Yosys 分析时钟和复位信号（支持 WSL）"""

    def __init__(self, include_paths: List[str] = None, verbose: bool = False, rtl_only: bool = False, use_wsl: bool = False, wsl_distro: str = None):
        self.include_paths = include_paths or []
        self.verbose = verbose
        self.rtl_only = rtl_only
        self.use_wsl = use_wsl
        self.wsl_distro = wsl_distro
        self.results = []

        if self.use_wsl and self.verbose:
            if self.wsl_distro:
                print(f"[INFO] Using WSL mode with distro: {self.wsl_distro}")
            else:
                print("[INFO] Using WSL mode for Yosys (default distro)")

    def _create_yosys_script(self, file_path: str, output_json: str) -> str:
        """创建 Yosys 脚本文件"""
        # 转换路径
        if self.use_wsl:
            file_path_wsl = to_wsl_path(file_path)
            output_json_wsl = to_wsl_path(output_json)
            include_paths_wsl = [to_wsl_path(p) for p in self.include_paths]
        else:
            file_path_wsl = file_path
            output_json_wsl = output_json
            include_paths_wsl = self.include_paths

        include_cmds = "\n".join([f"add_incdir {p}" for p in include_paths_wsl])

        if self.rtl_only:
            # RTL 模式：不进行综合，只提取原始 RTL 信息
            script = f"""
# Yosys script for RTL-level clock/reset analysis
{include_cmds}

# 读取 Verilog 文件
read_verilog -sv "{file_path_wsl}"

# 只进行层次结构分析，不进行综合
hierarchy -auto-top

# 导出设计信息到 JSON
write_json "{output_json_wsl}"
"""
        else:
            # 完整模式：进行综合后分析
            script = f"""
# Yosys script for clock/reset analysis
{include_cmds}

# 读取 Verilog 文件
read_verilog -sv "{file_path_wsl}"

# 进行基本综合
hierarchy -auto-top
proc
opt_clean

# 导出设计信息到 JSON
write_json "{output_json_wsl}"
"""
        return script

    def _get_wsl_cmd(self, cmd_args: List[str]) -> List[str]:
        """构建 WSL 命令，支持指定发行版"""
        if self.wsl_distro:
            return ['wsl', '-d', self.wsl_distro] + cmd_args
        else:
            return ['wsl'] + cmd_args

    def _run_yosys(self, script: str, output_json: str) -> Tuple[bool, str]:
        """运行 Yosys 脚本"""
        # 创建脚本文件（在 Windows 文件系统中）
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ys', delete=False) as f:
            f.write(script)
            script_file = f.name

        try:
            if self.use_wsl:
                # WSL 模式：在 WSL 中运行 Yosys
                script_file_wsl = to_wsl_path(script_file)
                output_json_wsl = to_wsl_path(output_json)

                # 确保输出目录在 WSL 中存在
                output_dir = os.path.dirname(output_json)
                if output_dir:
                    output_dir_wsl = to_wsl_path(output_dir)
                    mkdir_cmd = self._get_wsl_cmd(['mkdir', '-p', output_dir_wsl])
                    subprocess.run(mkdir_cmd, capture_output=True)

                cmd = self._get_wsl_cmd(['yosys', '-q', script_file_wsl])
            else:
                # 本地模式
                cmd = ['yosys', '-q', script_file]

            if self.verbose:
                print(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                return False, result.stderr

            return True, result.stdout
        except subprocess.TimeoutExpired:
            return False, "Yosys execution timeout"
        except FileNotFoundError:
            if self.use_wsl:
                return False, "WSL or yosys in WSL not found. Please install WSL and Yosys."
            else:
                return False, "Yosys not found. Please install Yosys first."
        except Exception as e:
            return False, str(e)
        finally:
            os.unlink(script_file)

    def _analyze_json(self, json_path: str, file_path: str) -> Dict:
        """分析 Yosys 生成的 JSON 文件"""
        clocks = []
        async_resets = []
        sync_resets = []

        try:
            with open(json_path, 'r') as f:
                design = json.load(f)

            # 分析模块
            for module_name, module_info in design.get('modules', {}).items():

                if self.rtl_only:
                    # RTL 模式：只分析端口声明和单元格连接
                    self._analyze_rtl_only(module_name, module_info, clocks, async_resets, sync_resets)
                else:
                    # 完整模式：分析综合后的单元格
                    self._analyze_synthesized(module_name, module_info, clocks, async_resets, sync_resets)

        except Exception as e:
            if self.verbose:
                print(f"Error analyzing JSON: {e}")

        return {
            'file': file_path,
            'clocks': clocks,
            'async_resets': async_resets,
            'sync_resets': sync_resets,
            'clock_count': len(clocks),
            'async_reset_count': len(async_resets),
            'sync_reset_count': len(sync_resets)
        }

    def _analyze_rtl_only(self, module_name: str, module_info: dict,
                          clocks: list, async_resets: list, sync_resets: list):
        """RTL 模式：分析原始 RTL 信号"""
        # 分析端口声明
        for port_name, port_info in module_info.get('ports', {}).items():
            port_name_lower = port_name.lower()

            # 检查是否是时钟端口
            if any(keyword in port_name_lower for keyword in ['clk', 'clock', 'ck']):
                # 避免重复
                if not any(c['name'] == port_name for c in clocks):
                    clocks.append({
                        'name': port_name,
                        'module': module_name,
                        'type': 'clock_port'
                    })

            # 检查是否是复位端口
            if any(keyword in port_name_lower for keyword in ['rst', 'reset', 'nrst', 'nreset']):
                # 根据端口名特征判断同步/异步
                if 'async' in port_name_lower or port_name_lower.startswith('arst'):
                    reset_type = 'async_reset'
                    target_list = async_resets
                elif 'sync' in port_name_lower or port_name_lower.startswith('srst'):
                    reset_type = 'sync_reset'
                    target_list = sync_resets
                else:
                    # 默认根据常见命名规则判断
                    # nreset/nrst 通常表示低电平复位，可能是异步
                    reset_type = 'async_reset'
                    target_list = async_resets

                if not any(r['name'] == port_name for r in target_list):
                    target_list.append({
                        'name': port_name,
                        'module': module_name,
                        'type': reset_type
                    })

        # 分析单元格连接中的时钟/复位
        for cell_name, cell_info in module_info.get('cells', {}).items():
            connections = cell_info.get('connections', {})

            for port_name, port_conn in connections.items():
                port_name_lower = port_name.lower()

                # 时钟信号
                if 'clk' in port_name_lower:
                    signal_names = self._extract_signal_names(port_conn)
                    for sig in signal_names:
                        if not any(c['name'] == sig for c in clocks):
                            clocks.append({
                                'name': sig,
                                'module': module_name,
                                'cell': cell_name,
                                'type': 'clock_connection'
                            })

                # 复位信号
                if 'rst' in port_name_lower or 'reset' in port_name_lower:
                    signal_names = self._extract_signal_names(port_conn)
                    for sig in signal_names:
                        if not any(r['name'] == sig for r in async_resets + sync_resets):
                            async_resets.append({
                                'name': sig,
                                'module': module_name,
                                'cell': cell_name,
                                'type': 'reset_connection'
                            })

    def _analyze_synthesized(self, module_name: str, module_info: dict,
                             clocks: list, async_resets: list, sync_resets: list):
        """完整模式：分析综合后的触发器"""
        for cell_name, cell_info in module_info.get('cells', {}).items():
            cell_type = cell_info.get('type', '')

            # 识别触发器
            if '$_DFF_' in cell_type or '$dff' in cell_type.lower():
                connections = cell_info.get('connections', {})

                # 查找时钟端口
                for port_name, port_conn in connections.items():
                    port_name_lower = port_name.lower()
                    if 'clk' in port_name_lower or 'clock' in port_name_lower or 'c' == port_name_lower:
                        signal_names = self._extract_signal_names(port_conn)
                        for sig in signal_names:
                            if not any(c['name'] == sig for c in clocks):
                                clocks.append({
                                    'name': sig,
                                    'module': module_name,
                                    'cell': cell_name,
                                    'type': 'clock'
                                })

            # 识别带复位的触发器
            if '$_DFFSR_' in cell_type or '$adff' in cell_type.lower() or '$sdff' in cell_type.lower():
                connections = cell_info.get('connections', {})
                is_async = '$adff' in cell_type.lower() or 'sr' in cell_type.lower()

                for port_name, port_conn in connections.items():
                    port_name_lower = port_name.lower()
                    if 'rst' in port_name_lower or 'reset' in port_name_lower or 'r' == port_name_lower:
                        signal_names = self._extract_signal_names(port_conn)
                        for sig in signal_names:
                            target_list = async_resets if is_async else sync_resets
                            if not any(r['name'] == sig for r in target_list):
                                target_list.append({
                                    'name': sig,
                                    'module': module_name,
                                    'cell': cell_name,
                                    'type': 'async_reset' if is_async else 'sync_reset'
                                })

        # 也分析端口声明
        for port_name, port_info in module_info.get('ports', {}).items():
            port_name_lower = port_name.lower()

            if any(keyword in port_name_lower for keyword in ['clk', 'clock']):
                if not any(c['name'] == port_name for c in clocks):
                    clocks.append({
                        'name': port_name,
                        'module': module_name,
                        'type': 'clock_port'
                    })

            if any(keyword in port_name_lower for keyword in ['rst', 'reset', 'nrst']):
                reset_type = 'async_reset' if 'async' in port_name_lower else 'sync_reset'
                target_list = async_resets if reset_type == 'async_reset' else sync_resets
                if not any(r['name'] == port_name for r in target_list):
                    target_list.append({
                        'name': port_name,
                        'module': module_name,
                        'type': reset_type
                    })

    def _extract_signal_names(self, connection) -> List[str]:
        """从连接信息中提取信号名"""
        names = []
        if isinstance(connection, list):
            for item in connection:
                if isinstance(item, str):
                    names.append(item)
        elif isinstance(connection, str):
            names.append(connection)
        return names

    def analyze_file(self, file_path: str) -> Dict:
        """分析单个 Verilog 文件"""
        if self.verbose:
            print(f"\nAnalyzing: {file_path}")

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            output_json = f.name

        try:
            # 创建并运行 Yosys 脚本
            script = self._create_yosys_script(file_path, output_json)
            success, error_msg = self._run_yosys(script, output_json)

            if not success:
                if self.verbose:
                    print(f"  Yosys error: {error_msg}")
                return {
                    'file': file_path,
                    'clocks': [],
                    'async_resets': [],
                    'sync_resets': [],
                    'clock_count': 0,
                    'async_reset_count': 0,
                    'sync_reset_count': 0,
                    'error': error_msg
                }

            # 分析生成的 JSON
            result = self._analyze_json(output_json, file_path)
            self.results.append(result)
            return result

        finally:
            if os.path.exists(output_json):
                os.unlink(output_json)

    def analyze_project(self, project_path: str, recursive: bool = True) -> Dict:
        """分析整个项目"""
        verilog_files = []

        if recursive:
            for root, dirs, files in os.walk(project_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for file in files:
                    if file.endswith(('.v', '.sv')):
                        verilog_files.append(os.path.join(root, file))
        else:
            for file in os.listdir(project_path):
                if file.endswith(('.v', '.sv')):
                    verilog_files.append(os.path.join(project_path, file))

        if not verilog_files:
            print(f"No Verilog files found in {project_path}")
            return {}

        print(f"Found {len(verilog_files)} Verilog files")

        # 分析每个文件
        results = []
        total_clocks = 0
        total_async_resets = 0
        total_sync_resets = 0
        errors = []

        for i, file_path in enumerate(verilog_files, 1):
            print(f"Progress: {i}/{len(verilog_files)} files processed...", end='\r')

            result = self.analyze_file(file_path)
            results.append(result)

            if 'error' in result:
                errors.append(f"{file_path}: {result['error']}")
            else:
                total_clocks += result['clock_count']
                total_async_resets += result['async_reset_count']
                total_sync_resets += result['sync_reset_count']

        print()  # 换行

        return {
            'project_path': project_path,
            'total_files': len(verilog_files),
            'files_with_signals': len([r for r in results if r.get('clock_count', 0) > 0 or r.get('async_reset_count', 0) > 0 or r.get('sync_reset_count', 0) > 0]),
            'total_clocks': total_clocks,
            'total_async_resets': total_async_resets,
            'total_sync_resets': total_sync_resets,
            'total_resets': total_async_resets + total_sync_resets,
            'file_results': results,
            'errors': errors
        }


def print_summary(summary: Dict, verbose: bool = False):
    """打印统计结果"""
    if not summary:
        print("No data to display")
        return

    print("\n" + "=" * 70)
    print("Yosys-based Clock and Reset Signal Statistics")
    print("=" * 70)

    print(f"\nProject Path: {summary['project_path']}")
    print(f"Total Files: {summary['total_files']}")
    print(f"Files with Clock/Reset Signals: {summary['files_with_signals']}")

    if summary.get('errors'):
        print(f"Files with Errors: {len(summary['errors'])}")

    print("\n" + "-" * 70)
    print("Summary Counts")
    print("-" * 70)
    print(f"  Clock Signals:        {summary['total_clocks']}")
    print(f"  Async Reset Signals:  {summary['total_async_resets']}")
    print(f"  Sync Reset Signals:   {summary['total_sync_resets']}")
    print(f"  Total Reset Signals:  {summary['total_resets']}")

    if verbose and summary.get('file_results'):
        print("\n" + "-" * 70)
        print("Detailed Breakdown by File")
        print("-" * 70)

        for result in summary['file_results']:
            has_signals = (result.get('clock_count', 0) > 0 or
                          result.get('async_reset_count', 0) > 0 or
                          result.get('sync_reset_count', 0) > 0)

            if has_signals or 'error' in result:
                print(f"\nFile: {result['file']}")

                if 'error' in result:
                    print(f"  Error: {result['error']}")
                    continue

                if result.get('clocks'):
                    print(f"  Clocks ({result['clock_count']}):")
                    for clock in result['clocks']:
                        print(f"    - {clock['name']} (module: {clock.get('module', 'N/A')})")

                if result.get('async_resets'):
                    print(f"  Async Resets ({result['async_reset_count']}):")
                    for reset in result['async_resets']:
                        print(f"    - {reset['name']} (module: {reset.get('module', 'N/A')})")

                if result.get('sync_resets'):
                    print(f"  Sync Resets ({result['sync_reset_count']}):")
                    for reset in result['sync_resets']:
                        print(f"    - {reset['name']} (module: {reset.get('module', 'N/A')})")

    print("\n" + "=" * 70)


def export_to_csv(summary: Dict, output_file: str):
    """导出统计结果到 CSV"""
    import csv

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['File', 'Signal Type', 'Signal Name', 'Module'])

        for result in summary.get('file_results', []):
            file_path = result['file']

            for clock in result.get('clocks', []):
                writer.writerow([file_path, 'Clock', clock['name'], clock.get('module', '')])

            for reset in result.get('async_resets', []):
                writer.writerow([file_path, 'Async Reset', reset['name'], reset.get('module', '')])

            for reset in result.get('sync_resets', []):
                writer.writerow([file_path, 'Sync Reset', reset['name'], reset.get('module', '')])

    print(f"\nResults exported to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Yosys-based Clock and Reset Signal Counter for Verilog Projects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s ./rtl
  %(prog)s ./rtl --verbose
  %(prog)s ./rtl --output stats.csv
  %(prog)s ./rtl --no-recursive
  %(prog)s ./rtl -I ./rtl -I ./common
  %(prog)s ./design.v --include ./common
  %(prog)s ./rtl --rtl-only          # Only analyze RTL level signals
  %(prog)s ./rtl --wsl               # Use Yosys in WSL (default distro)
  %(prog)s ./rtl --wsl --wsl-distro Ubuntu-22.04  # Use specific WSL distro

Note: This script requires Yosys to be installed.
      Install Yosys: https://github.com/YosysHQ/yosys
      For Windows without native Yosys, use --wsl to run Yosys in WSL.
        """
    )

    parser.add_argument('path', help='Project path or Verilog file')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed output including Yosys logs')
    parser.add_argument('-o', '--output', help='Export results to CSV file')
    parser.add_argument('-I', '--include', action='append', dest='include_paths',
                        help='Add include path (can be specified multiple times)')
    parser.add_argument('--no-recursive', action='store_true',
                        help='Do not search subdirectories')
    parser.add_argument('--rtl-only', action='store_true',
                        help='Only analyze RTL level signals (exclude synthesized cells)')
    parser.add_argument('--wsl', action='store_true',
                        help='Use WSL (Windows Subsystem for Linux) to run Yosys')
    parser.add_argument('--wsl-distro', type=str, default='Ubuntu-22.04',
                        help='WSL distribution to use (default: Ubuntu-22.04)')

    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"Error: Path does not exist: {args.path}")
        sys.exit(1)

    # 确定是否使用 WSL
    use_wsl = args.wsl
    wsl_distro = args.wsl_distro if use_wsl else None

    if use_wsl:
        # 检查指定的 WSL 发行版是否可用
        available_distros = get_wsl_distros()
        if wsl_distro and wsl_distro not in available_distros:
            print(f"Error: WSL distribution '{wsl_distro}' not found!")
            print(f"Available distros: {', '.join(available_distros) if available_distros else 'None'}")
            sys.exit(1)

        if not is_wsl_available(wsl_distro):
            print("Error: WSL is not available")
            print("Please install WSL: https://docs.microsoft.com/en-us/windows/wsl/install")
            sys.exit(1)

    # 检查 Yosys 是否安装
    if use_wsl:
        # 在指定的 WSL 发行版中检查 Yosys
        try:
            check_cmd = ['wsl', '-d', wsl_distro, 'yosys', '-V'] if wsl_distro else ['wsl', 'yosys', '-V']
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
            if args.verbose:
                print(f"WSL Yosys version: {result.stdout.strip()}")
        except Exception as e:
            print("Error: Yosys not found in WSL!")
            print(f"Please install Yosys in WSL distro '{wsl_distro}':")
            print(f"  wsl -d {wsl_distro}")
            print("  sudo apt-get update")
            print("  sudo apt-get install yosys")
            sys.exit(1)
    else:
        # 在本地检查 Yosys
        try:
            result = subprocess.run(['yosys', '-V'], capture_output=True, text=True, timeout=5)
            if args.verbose:
                print(f"Yosys version: {result.stdout.strip()}")
        except FileNotFoundError:
            print("Error: Yosys not found!")
            print("Please install Yosys first:")
            print("  - Ubuntu/Debian: sudo apt-get install yosys")
            print("  - macOS: brew install yosys")
            print("  - From source: https://github.com/YosysHQ/yosys")
            print("")
            print("Or use --wsl to run Yosys in Windows Subsystem for Linux")
            sys.exit(1)
        except Exception as e:
            print(f"Error checking Yosys: {e}")
            sys.exit(1)

    # 创建分析器
    analyzer = YosysClockResetAnalyzer(
        include_paths=args.include_paths,
        verbose=args.verbose,
        rtl_only=args.rtl_only,
        use_wsl=use_wsl,
        wsl_distro=wsl_distro
    )

    # 分析文件或项目
    if os.path.isfile(args.path):
        result = analyzer.analyze_file(args.path)
        summary = {
            'project_path': args.path,
            'total_files': 1,
            'files_with_signals': 1 if result.get('clock_count', 0) > 0 or result.get('async_reset_count', 0) > 0 or result.get('sync_reset_count', 0) > 0 else 0,
            'total_clocks': result.get('clock_count', 0),
            'total_async_resets': result.get('async_reset_count', 0),
            'total_sync_resets': result.get('sync_reset_count', 0),
            'total_resets': result.get('async_reset_count', 0) + result.get('sync_reset_count', 0),
            'file_results': [result],
            'errors': [f"{args.path}: {result['error']}"] if 'error' in result else []
        }
    else:
        summary = analyzer.analyze_project(
            args.path,
            recursive=not args.no_recursive
        )

    print_summary(summary, verbose=args.verbose)

    if args.output:
        export_to_csv(summary, args.output)


if __name__ == "__main__":
    main()
