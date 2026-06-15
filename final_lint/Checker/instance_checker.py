"""
Instance Checker - 实例化检查器

检测:
1. floating_port: 接口悬空（未连接）
2. constant_branch: 输入用于分支判断但不能是常数
3. reversed_connection: input/output接反
4. 模块层次结构分析（循环依赖、未使用模块等）
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, InstanceList, Instance, PortArg, Identifier,
    IntConst, Cond, IfStatement, CaseStatement, Decl,
    Input, Output, Inout, Always, Assign, Ioport, GenerateStatement
)

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder
from symbol import Symbol, SymbolType

# Import instance builder for hierarchy analysis
try:
    from ..instance_builder import InstanceBuilder, build_instance_hierarchy
except ImportError:
    from instance_builder import InstanceBuilder, build_instance_hierarchy


class InstanceIssueType(Enum):
    """实例化问题类型"""
    FLOATING_PORT = "floating_port"          # 接口悬空
    CONSTANT_BRANCH = "constant_branch"      # 分支判断为常数
    REVERSED_CONNECTION = "reversed_connection"  # input/output接反


@dataclass
class InstanceIssue:
    """实例化问题报告"""
    issue_type: InstanceIssueType
    instance_name: str
    module_name: str
    port_name: str
    lineno: int
    description: str
    severity: str = "warning"  # error, warning, info


class InstanceChecker:
    """
    实例化检查器

    检测三个常见问题:
    1. floating_port: 实例化时接口悬空（未连接）
    2. constant_branch: 输入到模块的信号如果用于该模块的分支判断，不能是常数
    3. reversed_connection: 将input连接到output或反之
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False, project=None, ignored_instances: List[str] = None):
        self.ast = ast
        self.stb = stb
        self.issues: List[InstanceIssue] = []
        self.debug = debug
        self.project = project  # Project 对象，用于跨文件模块查询
        self.ignored_instances: List[str] = ignored_instances or []  # 要忽略的实例名列表

        # 存储子模块信息: module_name -> {port_name: port_info}
        self.module_ports: Dict[str, Dict[str, Dict]] = {}
        self._collect_module_info()
        self._collect_project_module_info()  # 从 project 收集其他文件的模块信息

        # 存储每个模块内部端口使用情况（用于检查分支判断）
        self.module_port_usage: Dict[str, Dict[str, List[Dict]]] = {}
        self._collect_module_port_usage()

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def _collect_module_info(self):
        """收集所有模块的端口信息"""
        for module in self._get_modules():
            module_name = module.name
            self.module_ports[module_name] = {}

            self._dbg(f"Collecting port info for module: {module_name}")

            # 1. 从portlist提取端口信息（ANSI style: module sub(input a, output b)）
            if hasattr(module, 'portlist') and module.portlist:
                # 首先收集所有端口信息
                raw_ports = []
                for port in module.portlist.ports:
                    if isinstance(port, Ioport) and port.first:
                        decl = port.first
                        port_name = None
                        direction = None

                        if isinstance(decl, Input):
                            port_name = decl.name
                            direction = 'input'
                        elif isinstance(decl, Output):
                            port_name = decl.name
                            direction = 'output'
                        elif isinstance(decl, Inout):
                            port_name = decl.name
                            direction = 'inout'

                        if port_name and direction:
                            raw_ports.append((port_name, direction, decl))

                # Workaround for Pyverilog bug: 处理方向变化
                # Pyverilog bug: input a, output b, c, d 会把 c, d 错误地标记为 input
                # 修复：找到所有 direction 变化的点，修正后续被错误标记的端口
                if len(raw_ports) >= 2:
                    # 找到 direction 发生变化的位置
                    direction_changes = []
                    for i in range(1, len(raw_ports)):
                        if raw_ports[i][1] != raw_ports[i-1][1]:
                            direction_changes.append(i)  # 记录变化位置的索引

                    # 对于每个 direction 变化，检查后续端口是否被错误标记
                    for change_idx in direction_changes:
                        new_direction = raw_ports[change_idx][1]  # 变化后的新方向
                        # 从变化位置往后检查，修复被错误标记的端口
                        for i in range(change_idx + 1, len(raw_ports)):
                            # 如果当前端口的方向与新方向不同，可能是被错误标记了
                            if raw_ports[i][1] != new_direction:
                                old_direction = raw_ports[i][1]
                                raw_ports[i] = (raw_ports[i][0], new_direction, raw_ports[i][2])
                                self._dbg(f"  Fixed port {raw_ports[i][0]} from '{old_direction}' to '{new_direction}' (Pyverilog bug workaround)")

                # 存储处理后的端口信息
                for port_name, direction, decl in raw_ports:
                    self.module_ports[module_name][port_name] = {
                        'direction': direction,
                        'width': getattr(decl, 'width', None)
                    }
                    self._dbg(f"  Port(from portlist) {port_name}: {direction}")

            # 2. 从模块内部的声明提取端口方向（Non-ANSI style: module sub(a, b); input a; output b;）
            for item in module.items:
                if isinstance(item, Decl):
                    for decl in item.list:
                        # 处理链表结构
                        current_decl = decl
                        while current_decl:
                            port_name = None
                            direction = None

                            if isinstance(current_decl, Input):
                                port_name = current_decl.name
                                direction = 'input'
                            elif isinstance(current_decl, Output):
                                port_name = current_decl.name
                                direction = 'output'
                            elif isinstance(current_decl, Inout):
                                port_name = current_decl.name
                                direction = 'inout'

                            if port_name and direction:
                                if port_name not in self.module_ports[module_name]:
                                    self.module_ports[module_name][port_name] = {
                                        'direction': direction,
                                        'width': getattr(current_decl, 'width', None)
                                    }
                                    self._dbg(f"  Port(from decl) {port_name}: {direction}")

                            # 移动到链表下一个节点
                            current_decl = getattr(current_decl, 'next', None)

            self._dbg(f"  Module {module_name} ports: {list(self.module_ports[module_name].keys())}")

    def _collect_project_module_info(self):
        """从 Project 对象中收集其他文件的模块信息"""
        if self.project is None:
            return

        self._dbg(f"Collecting module info from project: {self.project.project_name}")
        self._dbg(f"Project has {len(self.project.modules)} modules")

        for module_name, module_info in self.project.modules.items():
            if module_name in self.module_ports:
                # 跳过已存在的模块（当前文件的模块优先）
                continue

            module_node = module_info.get('ast_node')
            if module_node is None:
                continue

            self._dbg(f"  Adding project module: {module_name}")
            self.module_ports[module_name] = {}

            # 从 module_node 提取端口信息（与 _collect_module_info 类似）
            # 1. 从 portlist 提取端口信息
            if hasattr(module_node, 'portlist') and module_node.portlist:
                for port in module_node.portlist.ports:
                    if isinstance(port, Ioport) and port.first:
                        decl = port.first
                        port_name = None
                        direction = None

                        if isinstance(decl, Input):
                            port_name = decl.name
                            direction = 'input'
                        elif isinstance(decl, Output):
                            port_name = decl.name
                            direction = 'output'
                        elif isinstance(decl, Inout):
                            port_name = decl.name
                            direction = 'inout'

                        if port_name and direction:
                            self.module_ports[module_name][port_name] = {
                                'direction': direction,
                                'width': getattr(decl, 'width', None)
                            }
                            self._dbg(f"    Port {port_name}: {direction}")

            # 2. 从模块内部的声明提取端口信息
            for item in module_node.items:
                if isinstance(item, Decl):
                    for decl in item.list:
                        current_decl = decl
                        while current_decl:
                            port_name = None
                            direction = None

                            if isinstance(current_decl, Input):
                                port_name = current_decl.name
                                direction = 'input'
                            elif isinstance(current_decl, Output):
                                port_name = current_decl.name
                                direction = 'output'
                            elif isinstance(current_decl, Inout):
                                port_name = current_decl.name
                                direction = 'inout'

                            if port_name and direction:
                                if port_name not in self.module_ports[module_name]:
                                    self.module_ports[module_name][port_name] = {
                                        'direction': direction,
                                        'width': getattr(current_decl, 'width', None)
                                    }

                            current_decl = getattr(current_decl, 'next', None)

    def _collect_module_port_usage(self):
        """收集每个模块内部端口的使用情况（用于分支判断检测）"""
        for module in self._get_modules():
            module_name = module.name
            self.module_port_usage[module_name] = defaultdict(list)

            # 遍历模块内的所有语句，收集端口在分支条件中的使用
            for item in module.items:
                if isinstance(item, Always):
                    self._collect_conditional_usage(module_name, item)
                elif isinstance(item, Assign):
                    # assign中的条件表达式（三元运算符）
                    if hasattr(item.right, 'cond'):
                        self._record_conditional_expr(module_name, item.right.cond)

    def _collect_conditional_usage(self, module_name: str, node):
        """递归收集条件表达式中的端口使用"""
        if node is None:
            return

        # If语句的条件
        if isinstance(node, IfStatement):
            self._record_conditional_expr(module_name, node.cond)
            if node.true_statement:
                self._collect_conditional_usage(module_name, node.true_statement)
            if node.false_statement:
                self._collect_conditional_usage(module_name, node.false_statement)

        # Case语句的条件
        elif isinstance(node, CaseStatement):
            self._record_conditional_expr(module_name, node.comp)
            if hasattr(node, 'caselist'):
                for case in node.caselist:
                    if case.statement:
                        self._collect_conditional_usage(module_name, case.statement)

        # 递归处理其他语句
        elif hasattr(node, 'statements'):
            for stmt in node.statements:
                self._collect_conditional_usage(module_name, stmt)
        elif hasattr(node, 'statement'):
            self._collect_conditional_usage(module_name, node.statement)

    def _record_conditional_expr(self, module_name: str, expr):
        """记录条件表达式中使用的端口"""
        if expr is None:
            return

        # 提取表达式中的标识符
        identifiers = self._extract_identifiers(expr)
        for name in identifiers:
            self.module_port_usage[module_name][name].append({
                'expr': expr,
                'lineno': getattr(expr, 'lineno', 0)
            })

    def _extract_identifiers(self, expr) -> List[str]:
        """从表达式中提取所有标识符名"""
        identifiers = []
        if expr is None:
            return identifiers

        if isinstance(expr, Identifier):
            identifiers.append(expr.name)
        else:
            # 递归处理所有属性
            for attr_name in ['left', 'right', 'var', 'ptr', 'msb', 'lsb', 'true', 'false', 'cond']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                identifiers.extend(self._extract_identifiers(item))
                        elif hasattr(attr_val, '__dict__'):
                            identifiers.extend(self._extract_identifiers(attr_val))
        return identifiers

    def check(self) -> List[InstanceIssue]:
        """执行所有实例化检查"""
        self.issues = []

        # 收集所有实例化信息
        for module in self._get_modules():
            self._check_module_items(module.items, module.name)

        return self.issues

    def _check_module_items(self, items, parent_module_name: str = ""):
        """递归检查模块中的 items，包括 generate 块中的 instance

        Args:
            items: 模块中的 item 列表
            parent_module_name: 包含这些 items 的父模块名
        """
        for item in items:
            if isinstance(item, InstanceList):
                for inst in item.instances:
                    self._check_instance(inst, parent_module_name)
            elif isinstance(item, Instance):
                self._check_instance(item, parent_module_name)
            elif isinstance(item, GenerateStatement):
                # 递归处理 generate 块中的 items
                if hasattr(item, 'items') and item.items:
                    self._check_generate_items(item.items, parent_module_name)

    def _check_generate_items(self, items, parent_module_name: str = ""):
        """递归检查 generate 块中的 items

        Args:
            items: generate 块中的 item 列表
            parent_module_name: 包含这些 items 的父模块名
        """
        for item in items:
            if isinstance(item, InstanceList):
                for inst in item.instances:
                    self._check_instance(inst, parent_module_name)
            elif isinstance(item, Instance):
                self._check_instance(item, parent_module_name)
            elif isinstance(item, GenerateStatement):
                if hasattr(item, 'items') and item.items:
                    self._check_generate_items(item.items, parent_module_name)
            elif hasattr(item, 'statement') and item.statement:
                # ForStatement, IfStatement 等
                self._check_generate_items([item.statement], parent_module_name)
            elif hasattr(item, 'true_statement') or hasattr(item, 'false_statement'):
                # IfStatement
                if hasattr(item, 'true_statement') and item.true_statement:
                    self._check_generate_items([item.true_statement], parent_module_name)
                if hasattr(item, 'false_statement') and item.false_statement:
                    self._check_generate_items([item.false_statement], parent_module_name)
            elif hasattr(item, 'statements'):
                # Block
                self._check_generate_items(item.statements, parent_module_name)

    def _check_instance(self, instance: Instance, parent_module_name: str = ""):
        """检查单个实例化

        Args:
            instance: 实例节点
            parent_module_name: 包含该实例的父模块名（用于符号查找）
        """
        module_name = instance.module
        instance_name = instance.name
        lineno = instance.lineno

        self._dbg(f"Checking instance {instance_name} of module {module_name} in {parent_module_name}")

        # 获取该模块的端口信息
        port_info = self.module_ports.get(module_name, {})

        # 获取已连接的端口
        connected_ports = set()
        port_connections = {}  # port_name -> arg_expr

        # 获取模块的端口列表（按顺序）
        module_port_list = list(port_info.keys())

        if hasattr(instance, 'portlist') and instance.portlist:
            for i, port in enumerate(instance.portlist):
                if isinstance(port, PortArg):
                    port_name = port.portname
                    arg_expr = port.argname

                    # 位置端口连接时 portname 为 None，需要按位置映射
                    if port_name is None and i < len(module_port_list):
                        port_name = module_port_list[i]

                    if port_name is not None:
                        connected_ports.add(port_name)
                        port_connections[port_name] = arg_expr
                else:
                    # 其他类型的端口连接（直接表达式）
                    if i < len(module_port_list):
                        port_name = module_port_list[i]
                        connected_ports.add(port_name)
                        port_connections[port_name] = port

        # 检查1: 接口悬空（未连接的端口）
        if not port_info:
            # 模块定义未找到，无法检查悬空端口
            self._dbg(f"Module {module_name} definition not found, skipping floating port check")
            # 可选：添加一个info级别的提示
            self.issues.append(InstanceIssue(
                issue_type=InstanceIssueType.FLOATING_PORT,
                instance_name=instance_name,
                module_name=module_name,
                port_name="*",
                lineno=lineno,
                description=f"Cannot check ports for instance '{instance_name}' ({module_name}): module definition not found",
                severity="info"
            ))
        else:
            for port_name, info in port_info.items():
                if port_name not in connected_ports:
                    self.issues.append(InstanceIssue(
                        issue_type=InstanceIssueType.FLOATING_PORT,
                        instance_name=instance_name,
                        module_name=module_name,
                        port_name=port_name,
                        lineno=lineno,
                        description=f"Port '{port_name}' of instance '{instance_name}' ({module_name}) is floating (not connected)",
                        severity="warning"
                    ))
                    self._dbg(f"FLOATING_PORT: {instance_name}.{port_name}")

        # 检查2和3: 对每个连接的端口进行检查
        for port_name, arg_expr in port_connections.items():
            if port_name not in port_info:
                continue  # 端口信息不可用

            info = port_info[port_name]
            direction = info['direction']

            # 检查2: 常数分支判断
            if direction == 'input' and self._is_port_used_in_branch(module_name, port_name):
                if self._is_constant_expr(arg_expr):
                    self.issues.append(InstanceIssue(
                        issue_type=InstanceIssueType.CONSTANT_BRANCH,
                        instance_name=instance_name,
                        module_name=module_name,
                        port_name=port_name,
                        lineno=lineno,
                        description=f"Port '{port_name}' is used as branch condition in module '{module_name}' but connected to constant",
                        severity="error"
                    ))
                    self._dbg(f"CONSTANT_BRANCH: {instance_name}.{port_name}")

            # 检查3: input/output接反
            self._check_reversed_connection(instance_name, module_name, port_name, direction, arg_expr, lineno, parent_module_name)

    def _is_port_used_in_branch(self, module_name: str, port_name: str) -> bool:
        """检查端口是否在模块内部用于分支判断"""
        if module_name not in self.module_port_usage:
            return False
        return port_name in self.module_port_usage[module_name]

    def _is_constant_expr(self, expr) -> bool:
        """检查表达式是否为常数"""
        if expr is None:
            return True
        if isinstance(expr, IntConst):
            return True
        # 可以扩展检查其他常数类型，如parameter等
        return False

    def _check_reversed_connection(self, instance_name: str, module_name: str,
                                   port_name: str, port_direction: str,
                                   arg_expr, lineno: int, parent_module_name: str = ""):
        """检查input/output是否接反

        Args:
            instance_name: 实例名
            module_name: 子模块名（实例化的模块类型）
            port_name: 端口名
            port_direction: 端口方向
            arg_expr: 端口连接的表达式
            lineno: 行号
            parent_module_name: 父模块名（包含该实例的模块，用于符号查找）
        """
        if arg_expr is None:
            return

        # 获取连接的信号名
        signal_names = self._extract_identifiers(arg_expr)
        if not signal_names:
            return

        for signal_name in signal_names:
            # 在父模块中查找信号（信号属于父模块，不是子模块）
            symbol = self._lookup_symbol(signal_name, parent_module_name)
            if symbol is None:
                continue

            # 检查信号类型
            signal_type = symbol.type
            signal_direction = getattr(symbol, 'port_direction', None)

            # input端口应该连接可以驱动它的信号（如父模块的output、wire、reg）
            # output端口应该连接可以接收它的信号（如父模块的wire、reg）
            if port_direction == 'output':
                # output端口不应该连接到父模块的input
                # 这是方向反了：子模块在试图驱动父模块的输入
                if signal_type == SymbolType.INPUT:
                    self.issues.append(InstanceIssue(
                        issue_type=InstanceIssueType.REVERSED_CONNECTION,
                        instance_name=instance_name,
                        module_name=module_name,
                        port_name=port_name,
                        lineno=lineno,
                        description=f"Output port '{port_name}' connected to input signal '{signal_name}' - direction reversed",
                        severity="error"
                    ))
                    self._dbg(f"REVERSED_CONNECTION: {instance_name}.{port_name} -> {signal_name}")
    def _get_modules(self) -> List[ModuleDef]:
        """获取所有模块定义"""
        modules = []
        self._find_modules(self.ast, modules)
        return modules

    def _find_modules(self, node, modules: List[ModuleDef]):
        """递归查找模块定义"""
        if isinstance(node, ModuleDef):
            modules.append(node)

        for child in self._get_children(node):
            self._find_modules(child, modules)

    def _get_children(self, node) -> List[Any]:
        """获取子节点"""
        children = []
        if hasattr(node, '__dict__'):
            for attr_name, attr_val in node.__dict__.items():
                if isinstance(attr_val, (list, tuple)):
                    children.extend(attr_val)
                elif attr_val is not None and hasattr(attr_val, '__dict__'):
                    children.append(attr_val)
        return children

    def _lookup_symbol(self, name: str, module_name: str = "") -> Optional[Symbol]:
        """查找符号 - 从指定模块的 scope 中查找"""
        if module_name:
            # 在指定模块中查找
            return self.stb.lookup_in_module(module_name, name)
        # 如果没有指定模块，使用 root_scope 查找（向后兼容）
        return self.stb.lookup(name, self.stb.root_scope)

    def get_port_connections(self, conservative: bool = True) -> Dict[str, Dict[str, List[str]]]:
        """
        获取所有 instance 端口连接信息

        Args:
            conservative: 是否使用保守策略。
                         True: 任何出现在 instance 端口连接中的信号都视为既被驱动也被使用
                         False: 只根据已知端口方向判断

        Returns:
            {
                'driven_signals': [被 instance output 驱动的信号列表],
                'used_signals': [被 instance input 使用的信号列表]
            }
        """
        driven_signals = []  # 被 instance output 驱动的信号
        used_signals = []    # 被 instance input 使用的信号

        for module in self._get_modules():
            for item in module.items:
                if isinstance(item, InstanceList):
                    for inst in item.instances:
                        d, u = self._collect_instance_port_signals(inst, conservative)
                        driven_signals.extend(d)
                        used_signals.extend(u)
                elif isinstance(item, Instance):
                    d, u = self._collect_instance_port_signals(item, conservative)
                    driven_signals.extend(d)
                    used_signals.extend(u)

        return {
            'driven_signals': driven_signals,
            'used_signals': used_signals
        }

    def _collect_instance_port_signals(self, instance: Instance, conservative: bool = True) -> Tuple[List[str], List[str]]:
        """
        收集单个 instance 的端口连接信号

        Args:
            instance: 实例节点
            conservative: 是否使用保守策略

        Returns:
            (driven_signals, used_signals)
        """
        driven = []  # 被 output 驱动的信号
        used = []    # 被 input 使用的信号

        module_name = instance.module
        port_info = self.module_ports.get(module_name, {})

        # 保守策略：如果没有子模块定义，所有端口连接的信号都视为既被驱动也被使用
        has_port_info = bool(port_info)

        if not hasattr(instance, 'portlist') or not instance.portlist:
            return driven, used

        module_port_list = list(port_info.keys())

        for i, port in enumerate(instance.portlist):
            if isinstance(port, PortArg):
                port_name = port.portname
                arg_expr = port.argname

                # 位置端口连接时 portname 为 None，需要按位置映射
                if port_name is None and i < len(module_port_list):
                    port_name = module_port_list[i]

                signal_names = self._extract_identifiers(arg_expr) if arg_expr else []

                if not signal_names:
                    continue

                # 获取端口方向
                if port_name and port_name in port_info:
                    direction = port_info[port_name].get('direction', 'unknown')
                else:
                    direction = 'unknown'

                if direction == 'unknown' and conservative:
                    # 保守策略：未知方向的端口，信号既被使用也被驱动
                    driven.extend(signal_names)
                    used.extend(signal_names)
                    self._dbg(f"Instance unknown port (conservative): {instance.name}.{port_name} <-> {signal_names}")
                elif direction in ('output', 'inout'):
                    # output 端口驱动信号
                    driven.extend(signal_names)
                    self._dbg(f"Instance output drive: {instance.name}.{port_name} -> {signal_names}")
                elif direction in ('input',):
                    # input 端口使用信号
                    used.extend(signal_names)
                    self._dbg(f"Instance input use: {instance.name}.{port_name} <- {signal_names}")

        return driven, used

    def print_report(self):
        """打印检查报告"""
        print("\n" + "=" * 70)
        print("Instance Check Report")
        print("=" * 70)

        if not self.issues:
            print("No instance issues found")
            return

        # 按类型分组
        floating = [i for i in self.issues if i.issue_type == InstanceIssueType.FLOATING_PORT]
        constant = [i for i in self.issues if i.issue_type == InstanceIssueType.CONSTANT_BRANCH]
        reversed_conn = [i for i in self.issues if i.issue_type == InstanceIssueType.REVERSED_CONNECTION]

        if floating:
            print(f"\n[!] Floating Ports ({len(floating)}):")
            for issue in floating:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if constant:
            print(f"\n[!] Constant Branch Conditions ({len(constant)}):")
            for issue in constant:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if reversed_conn:
            print(f"\n[!] Reversed Connections ({len(reversed_conn)}):")
            for issue in reversed_conn:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        print(f"\nTotal: {len(self.issues)} instance issues")


class InstanceHierarchyChecker:
    """
    模块层次结构检查器
    基于 InstanceBuilder 提供的数据进行深度分析
    """

    def __init__(self, builder: InstanceBuilder, debug: bool = False, project_modules: Set[str] = None):
        self.builder = builder
        self.debug = debug
        self.issues: List[InstanceIssue] = []
        self.project_modules = project_modules or set()  # 项目中的所有模块名

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[InstanceChecker] {msg}")

    def check_all(self) -> List[InstanceIssue]:
        """执行所有检查"""
        self.issues = []
        self._check_circular_dependency()
        self._check_unresolved_modules()
        self._check_duplicate_instances()
        self._check_unused_modules()
        return self.issues

    def _check_circular_dependency(self):
        """检查循环依赖"""
        cycle = self.builder.has_circular_dependency()
        if cycle:
            cycle_str = " -> ".join(cycle)
            self.issues.append(InstanceIssue(
                issue_type=InstanceIssueType.CIRCULAR_DEPENDENCY,
                instance_name="",
                module_name=cycle[0],
                port_name="",
                lineno=0,
                description=f"Circular dependency detected: {cycle_str}",
                severity="error"
            ))
            self._dbg(f"Found circular dependency: {cycle_str}")

    def _check_unresolved_modules(self):
        """检查未解析的模块（实例化但未定义）"""
        defined_modules = set(self.builder.get_all_modules())
        # 合并项目中的模块（来自其他文件）
        all_known_modules = defined_modules | self.project_modules
        self._dbg(f"Checking unresolved modules: {len(defined_modules)} from current file, {len(self.project_modules)} from project")
        self._dbg(f"All known modules: {sorted(all_known_modules)[:10]}...")  # 只显示前10个
        for inst in self.builder.instances.values():
            if inst.module_type not in all_known_modules:
                self.issues.append(InstanceIssue(
                    issue_type=InstanceIssueType.UNRESOLVED_MODULE,
                    instance_name=inst.name,
                    module_name=inst.parent_module,
                    port_name="",
                    lineno=inst.lineno,
                    description=f"Unresolved module '{inst.module_type}' in instance '{inst.name}'",
                    severity="error"
                ))
                self._dbg(f"Unresolved module: {inst.module_type} in {inst.parent_module}.{inst.name}")

    def _check_duplicate_instances(self):
        """检查重复实例名（同一模块内）"""
        from collections import defaultdict
        instances_by_parent: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
        for global_name, inst in self.builder.instances.items():
            instances_by_parent[inst.parent_module][inst.name].append(inst.lineno)
        for parent, instances in instances_by_parent.items():
            for inst_name, lines in instances.items():
                if len(lines) > 1:
                    self.issues.append(InstanceIssue(
                        issue_type=InstanceIssueType.DUPLICATE_INSTANCE,
                        instance_name=inst_name,
                        module_name=parent,
                        port_name="",
                        lineno=lines[0],
                        description=f"Duplicate instance name '{inst_name}' in module '{parent}' (lines: {lines})",
                        severity="error"
                    ))
                    self._dbg(f"Duplicate instance: {parent}.{inst_name} at lines {lines}")

    def _check_unused_modules(self):
        """检查未使用的模块（定义但未被实例化）"""
        for module_name in self.builder.get_all_modules():
            if module_name in self.builder.top_modules:
                continue
            parents = self.builder.get_parent_modules(module_name)
            if not parents:
                instances = self.builder.get_instances_of_module(module_name)
                if not instances:
                    self.issues.append(InstanceIssue(
                        issue_type=InstanceIssueType.UNUSED_MODULE,
                        instance_name="",
                        module_name=module_name,
                        port_name="",
                        lineno=0,
                        description=f"Module '{module_name}' is defined but never instantiated",
                        severity="info"
                    ))
                    self._dbg(f"Unused module: {module_name}")

    def get_module_depth_report(self) -> Dict[str, Any]:
        """获取模块深度分析报告"""
        stats = self.builder.get_statistics()
        from collections import defaultdict
        modules_by_depth: Dict[int, List[str]] = defaultdict(list)
        def calculate_depth(module_name, visited=None):
            if visited is None:
                visited = set()
            if module_name in visited:
                return 0
            visited.add(module_name)
            hierarchy = self.builder.get_module_hierarchy(module_name)
            if not hierarchy or not hierarchy.instances:
                return 0
            max_depth = 0
            for inst in hierarchy.instances:
                child_depth = calculate_depth(inst.module_type, visited.copy())
                max_depth = max(max_depth, child_depth + 1)
            return max_depth
        for module in self.builder.get_all_modules():
            depth = calculate_depth(module)
            modules_by_depth[depth].append(module)
        return {
            "max_depth": stats["max_depth"],
            "modules_by_depth": dict(modules_by_depth),
            "total_modules": stats["total_modules"],
            "total_instances": stats["total_instances"]
        }

    def get_fan_out_report(self) -> Dict[str, int]:
        """获取模块扇出报告"""
        fan_out = {}
        for module in self.builder.get_all_modules():
            children = self.builder.get_child_instances(module)
            fan_out[module] = len(children)
        return fan_out

    def get_fan_in_report(self) -> Dict[str, int]:
        """获取模块扇入报告"""
        fan_in = {}
        for module in self.builder.get_all_modules():
            parents = self.builder.get_parent_modules(module)
            fan_in[module] = len(parents)
        return fan_in


class InstanceIssueType(Enum):
    """实例化问题类型"""
    FLOATING_PORT = "floating_port"
    CONSTANT_BRANCH = "constant_branch"
    REVERSED_CONNECTION = "reversed_connection"
    # Hierarchy analysis issues
    CIRCULAR_DEPENDENCY = "circular_dependency"
    UNRESOLVED_MODULE = "unresolved_module"
    DUPLICATE_INSTANCE = "duplicate_instance"
    UNUSED_MODULE = "unused_module"


def check_instance(ast, stb: SymbolTableBuilder, debug: bool = False) -> List[InstanceIssue]:
    """便捷函数：直接检查实例化问题"""
    checker = InstanceChecker(ast, stb, debug=debug)
    issues = checker.check()
    return issues


def check_instance_hierarchy(ast, stb: SymbolTableBuilder, debug: bool = False, project=None) -> List[InstanceIssue]:
    """便捷函数：检查模块层次结构问题"""
    builder = build_instance_hierarchy(ast, stb, debug=debug)
    # 从 project 中获取所有模块名
    project_modules = set()
    if project and hasattr(project, 'modules'):
        project_modules = set(project.modules.keys())
        if debug:
            print(f"[InstanceBuilder] Project has {len(project_modules)} modules: {sorted(project_modules)[:10]}...")
    checker = InstanceHierarchyChecker(builder, debug=debug, project_modules=project_modules)
    return checker.check_all()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python instance_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    from pyverilog.vparser.parser import parse
    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Check instances
    checker = InstanceChecker(ast, stb, debug=True)
    issues = checker.check()
    checker.print_report()
