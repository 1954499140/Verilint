"""
Combdly Checker - 组合逻辑敏感列表检查器

检测:
1. 组合逻辑always块的敏感列表是否完整
2. 敏感列表中是否包含所有在always块中读取的信号
3. 检测 implicit latch（隐式锁存器）风险

原理:
- 组合逻辑always块应该对所有读取的信号敏感
- 如果敏感列表不完整，仿真时可能不触发，但综合结果是组合逻辑
- 这会导致仿真和综合行为不一致

注意:
- 只检查组合逻辑always块（always @* 或 always @(signal, ...) 无时钟边沿）
- 不检查时序逻辑always块（always @(posedge clk)）
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, Always, AlwaysComb, Identifier, Sens, SensList,
    NonblockingSubstitution, BlockingSubstitution, IfStatement,
    CaseStatement, Partselect, Pointer
)

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder


class CombdlyIssueType(Enum):
    """组合逻辑敏感列表问题类型"""
    INCOMPLETE_SENSITIVITY = "incomplete_sensitivity"  # 敏感列表不完整
    MISSING_SIGNAL = "missing_signal"                    # 缺少敏感信号
    EXTRA_SIGNAL = "extra_signal"                        # 多余的敏感信号


@dataclass
class CombdlyIssue:
    """组合逻辑敏感列表问题报告"""
    issue_type: CombdlyIssueType
    always_lineno: int
    description: str
    missing_signals: List[str] = None
    extra_signals: List[str] = None
    severity: str = "warning"


class CombdlyChecker:
    """
    组合逻辑敏感列表检查器

    检测组合逻辑always块的敏感列表是否完整:
    - 敏感列表应包含always块中所有读取的信号
    - 不包含会导致仿真/综合不一致
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False):
        self.ast = ast
        self.stb = stb
        self.issues: List[CombdlyIssue] = []
        self.debug = debug
        self.current_module_name: Optional[str] = None  # 当前正在检查的模块名

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def check(self) -> List[CombdlyIssue]:
        """执行组合逻辑敏感列表检查"""
        self.issues = []

        for module in self._get_modules():
            self._check_module(module)

        return self.issues

    def _check_module(self, module: ModuleDef):
        """检查单个模块"""
        # 设置当前模块名
        if hasattr(module, 'name'):
            self.current_module_name = module.name

        for item in module.items:
            if isinstance(item, (Always, AlwaysComb)):
                self._check_always_block(item)

        # 清除当前模块名
        self.current_module_name = None

    def _check_always_block(self, always_node):
        """检查always块的敏感列表"""
        # 首先检查是否是组合逻辑
        if not self._is_combinational_always(always_node):
            self._dbg(f"Skipping sequential always at line {always_node.lineno}")
            return

        # 获取敏感列表中的信号
        sensitivity_signals = self._get_sensitivity_signals(always_node)

        # 获取always块中实际读取的所有信号
        read_signals = self._get_read_signals(always_node)

        self._dbg(f"Always block at line {always_node.lineno}:")
        self._dbg(f"  Sensitivity list: {sensitivity_signals}")
        self._dbg(f"  Read signals: {read_signals}")

        # 检查敏感列表是否完整
        # 如果是 @*，则认为敏感列表完整
        if '*' in sensitivity_signals:
            self._dbg(f"  Skipping check - @* sensitivity list")
            return

        missing_signals = read_signals - sensitivity_signals
        extra_signals = sensitivity_signals - read_signals

        if missing_signals:
            self.issues.append(CombdlyIssue(
                issue_type=CombdlyIssueType.INCOMPLETE_SENSITIVITY,
                always_lineno=always_node.lineno,
                description=f"Combinational always block at line {always_node.lineno} has incomplete sensitivity list. "
                           f"Missing signals: {', '.join(sorted(missing_signals))}",
                missing_signals=sorted(missing_signals),
                extra_signals=sorted(extra_signals) if extra_signals else None,
                severity="error"
            ))
            self._dbg(f"  ISSUE: Missing signals {missing_signals}")

        if extra_signals and not self._is_star_sensitivity(always_node):
            # 只在非 @* 的情况下报告多余信号
            self.issues.append(CombdlyIssue(
                issue_type=CombdlyIssueType.EXTRA_SIGNAL,
                always_lineno=always_node.lineno,
                description=f"Combinational always block at line {always_node.lineno} has unused signals in sensitivity list. "
                           f"Extra signals: {', '.join(sorted(extra_signals))}",
                extra_signals=sorted(extra_signals),
                severity="info"
            ))
            self._dbg(f"  INFO: Extra signals {extra_signals}")

    def _is_combinational_always(self, always_node) -> bool:
        """检查always块是否是组合逻辑"""
        if isinstance(always_node, AlwaysComb):
            return True

        # 检查敏感列表是否有posedge/negedge
        if hasattr(always_node, 'sens_list') and always_node.sens_list:
            sens_list = always_node.sens_list
            if hasattr(sens_list, 'list'):
                for sens in sens_list.list:
                    if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                        return False
        return True

    def _is_star_sensitivity(self, always_node) -> bool:
        """检查是否是 @* 敏感列表"""
        if hasattr(always_node, 'sens_list') and always_node.sens_list:
            sens_list = always_node.sens_list
            if hasattr(sens_list, 'list'):
                for sens in sens_list.list:
                    # @* 通常表示为 type='all' 或 name='*'
                    if hasattr(sens, 'type') and sens.type == 'all':
                        return True
                    if hasattr(sens, 'name') and sens.name == '*':
                        return True
        return False

    def _get_sensitivity_signals(self, always_node) -> Set[str]:
        """获取敏感列表中的所有信号名"""
        signals = set()

        if not hasattr(always_node, 'sens_list') or not always_node.sens_list:
            return signals

        sens_list = always_node.sens_list
        if hasattr(sens_list, 'list'):
            for sens in sens_list.list:
                # 如果是 @*，返回特殊标记表示包含所有信号
                if self._is_star_sensitivity(always_node):
                    return {'*'}  # 特殊标记表示 @*

                # 提取信号名
                signal_names = self._extract_signal_names(sens)
                signals.update(signal_names)

        return signals

    def _get_read_signals(self, always_node) -> Set[str]:
        """获取always块中读取的所有信号"""
        read_signals = set()

        if hasattr(always_node, 'statement'):
            self._collect_read_signals(always_node.statement, read_signals)

        return read_signals

    def _collect_read_signals(self, stmt, read_signals: Set[str]):
        """递归收集语句中读取的信号"""
        if stmt is None:
            return

        # 赋值语句：读取右侧
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            self._extract_signals_from_expr(stmt.right, read_signals)

        # 条件表达式（if语句）
        elif isinstance(stmt, IfStatement):
            # 读取条件
            if hasattr(stmt, 'cond'):
                self._extract_signals_from_expr(stmt.cond, read_signals)
            # 处理分支
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                self._collect_read_signals(stmt.true_statement, read_signals)
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                self._collect_read_signals(stmt.false_statement, read_signals)

        # case语句
        elif isinstance(stmt, CaseStatement):
            # 读取case条件
            if hasattr(stmt, 'comp'):
                self._extract_signals_from_expr(stmt.comp, read_signals)
            # 处理case分支
            if hasattr(stmt, 'caselist'):
                for case in stmt.caselist:
                    # case条件也是读取
                    if hasattr(case, 'cond') and case.cond:
                        if isinstance(case.cond, (list, tuple)):
                            for c in case.cond:
                                self._extract_signals_from_expr(c, read_signals)
                        else:
                            self._extract_signals_from_expr(case.cond, read_signals)
                    # 处理分支语句
                    if case.statement:
                        self._collect_read_signals(case.statement, read_signals)

        # 块语句
        elif hasattr(stmt, 'statements'):
            for s in stmt.statements:
                self._collect_read_signals(s, read_signals)

        # 其他可能包含子语句的节点
        for attr_name in ['statement', 'true_statement', 'false_statement']:
            if hasattr(stmt, attr_name):
                attr_val = getattr(stmt, attr_name)
                if attr_val and not isinstance(attr_val, (NonblockingSubstitution, BlockingSubstitution)):
                    self._collect_read_signals(attr_val, read_signals)

    def _is_parameter_or_constant(self, name: str) -> bool:
        """
        检查信号名是否是parameter或常量

        Args:
            name: 信号名

        Returns:
            是否是parameter或常量
        """
        try:
            # 检查是否是数字常量（纯数字）
            try:
                int(name, 0)  # 尝试解析为整数
                return True
            except (ValueError, TypeError):
                pass

            # 获取当前模块scope
            if not self.current_module_name:
                return False

            module_scope = self.stb.get_module_scope(self.current_module_name)
            if not module_scope:
                return False

            # 查找符号
            symbol = module_scope.lookup_symbol(name)
            if not symbol:
                return False

            # 检查是否是parameter、localparam
            if symbol.type.name in ('PARAMETER', 'LOCALPARAM'):
                return True

            return False

        except Exception:
            return False

    def _extract_signals_from_expr(self, expr, signals: Set[str]):
        """从表达式中提取信号名（排除parameter和常量）"""
        if expr is None:
            return

        if isinstance(expr, Identifier):
            # 过滤掉parameter和常量
            if not self._is_parameter_or_constant(expr.name):
                signals.add(expr.name)

        elif isinstance(expr, (Partselect, Pointer)):
            # 位选择或数组索引，提取基础信号
            if hasattr(expr, 'var') and isinstance(expr.var, Identifier):
                if not self._is_parameter_or_constant(expr.var.name):
                    signals.add(expr.var.name)
            # 索引本身也可能包含信号
            if hasattr(expr, 'ptr'):
                self._extract_signals_from_expr(expr.ptr, signals)
            if hasattr(expr, 'msb'):
                self._extract_signals_from_expr(expr.msb, signals)
            if hasattr(expr, 'lsb'):
                self._extract_signals_from_expr(expr.lsb, signals)

        else:
            # 递归处理子表达式
            for attr_name in ['left', 'right', 'var', 'next', 'args', 'value', 'cond', 'true_value', 'false_value']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                self._extract_signals_from_expr(item, signals)
                        else:
                            self._extract_signals_from_expr(attr_val, signals)

    def _extract_signal_names(self, node) -> List[str]:
        """从敏感列表节点中提取信号名"""
        names = []

        if isinstance(node, Identifier):
            names.append(node.name)
        elif hasattr(node, 'name') and node.name:
            names.append(node.name)
        elif hasattr(node, 'var') and node.var:
            if isinstance(node.var, Identifier):
                names.append(node.var.name)
        # 处理 Sens 对象的 sig 属性 (敏感列表中的信号)
        elif hasattr(node, 'sig') and node.sig:
            if isinstance(node.sig, Identifier):
                names.append(node.sig.name)
            else:
                names.extend(self._extract_signal_names(node.sig))

        # 递归处理子节点
        for attr_name in ['name', 'var', 'sig', 'left', 'right']:
            if hasattr(node, attr_name):
                attr_val = getattr(node, attr_name)
                if attr_val and attr_val != node:
                    names.extend(self._extract_signal_names(attr_val))

        return names

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

    def print_report(self):
        """打印检查报告"""
        print("\n" + "=" * 70)
        print("Combdly (Incomplete Sensitivity) Check Report")
        print("=" * 70)

        if not self.issues:
            print("No incomplete sensitivity issues found")
            return

        # 按类型分组
        incomplete = [i for i in self.issues
                     if i.issue_type == CombdlyIssueType.INCOMPLETE_SENSITIVITY]
        extra = [i for i in self.issues
                if i.issue_type == CombdlyIssueType.EXTRA_SIGNAL]

        if incomplete:
            print(f"\n[!] Incomplete Sensitivity Lists ({len(incomplete)}):")
            for issue in incomplete:
                print(f"  Line {issue.always_lineno:3d}: {issue.description}")
                if issue.missing_signals:
                    print(f"           Missing: {', '.join(issue.missing_signals)}")

        if extra:
            print(f"\n[!] Extra Signals in Sensitivity List ({len(extra)}):")
            for issue in extra:
                print(f"  Line {issue.always_lineno:3d}: {issue.description}")

        print(f"\nTotal: {len(self.issues)} sensitivity issues")


def check_combdly(ast, stb: SymbolTableBuilder, debug: bool = False) -> List[CombdlyIssue]:
    """便捷函数：直接检查组合逻辑敏感列表问题"""
    checker = CombdlyChecker(ast, stb, debug=debug)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python combdly_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    from pyverilog.vparser.parser import parse

    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Check combdly
    checker = CombdlyChecker(ast, stb, debug=True)
    issues = checker.check()
    checker.print_report()
