"""
Glitch Checker - 毛刺（竞争冒险）检查器

检测组合逻辑中的毛刺问题：
1. 当同一信号的正相和反相形式同时用于组合逻辑时，由于反相延迟可能导致毛刺
2. 典型模式：sel 和 ~sel 同时用于多路选择器或逻辑运算

常见触发模式：
- (sel & a) | (~sel & b)  - 2:1 多路选择器
- sel ? a : b 在使用反相信号时
- 任何同时使用 signal 和 ~signal 的组合逻辑
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, Assign, Identifier, Ulnot, Unot,
    And, Or, Xor, Land, Lor, Cond, Instance,
    Always, AlwaysComb
)

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder


class GlitchIssueType(Enum):
    """毛刺问题类型"""
    INVERTED_SIGNAL_PAIR = "inverted_signal_pair"  # 信号和反相同时使用


@dataclass
class GlitchIssue:
    """毛刺问题报告"""
    issue_type: GlitchIssueType
    signal_name: str          # 导致毛刺的信号名
    lineno: int
    description: str
    affected_output: str      # 受影响的输出信号
    severity: str = "warning"


@dataclass
class SignalSource:
    """信号来源信息"""
    name: str
    lineno: int
    is_inverted: bool         # 是否是反相形式
    original_signal: str      # 原始信号名（如果是反相）


class GlitchChecker:
    """
    毛刺检查器

    检测组合逻辑中可能导致毛刺的模式：
    - 信号和其反相信号同时出现在同一组合逻辑表达式中
    - 支持通过中间变量追踪反相关系
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False):
        self.ast = ast
        self.stb = stb
        self.issues: List[GlitchIssue] = []
        self.debug = debug

        # 存储每个赋值语句的AST节点
        self.assign_nodes: List[Assign] = []
        self.always_comb_nodes: List[Any] = []

        # 信号反相关系映射: signal_name -> original_signal_name
        # 例如: not_sel -> sel_sync (因为 not_sel = ~sel_sync)
        self.inverted_signals: Dict[str, str] = {}

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def check(self) -> List[GlitchIssue]:
        """执行毛刺检查"""
        self.issues = []
        self.assign_nodes = []
        self.always_comb_nodes = []
        self.inverted_signals = {}

        # 收集所有模块的组合逻辑
        for module in self._get_modules():
            self._collect_combinational_logic(module)

        # 首先收集所有反相信号关系
        self._collect_inverted_relationships()

        # 检查每个组合逻辑表达式
        self._check_assign_statements()
        self._check_always_comb_blocks()

        return self.issues

    def _collect_inverted_relationships(self):
        """收集反相信号关系（如 not_sel = ~sel_sync）"""
        for assign in self.assign_nodes:
            lhs_name = self._get_lhs_name(assign)
            rhs = assign.right

            # 检查 RHS 是否是单个反相操作
            if isinstance(rhs, (Ulnot, Unot)):
                if hasattr(rhs, 'right') and isinstance(rhs.right, Identifier):
                    orig_signal = rhs.right.name
                    self.inverted_signals[lhs_name] = orig_signal
                    self._dbg(f"Found inverted relationship: {lhs_name} = ~{orig_signal}")
            # 也检查通过括号包装的反相
            elif hasattr(rhs, 'var') and isinstance(rhs.var, (Ulnot, Unot)):
                if hasattr(rhs.var, 'right') and isinstance(rhs.var.right, Identifier):
                    orig_signal = rhs.var.right.name
                    self.inverted_signals[lhs_name] = orig_signal
                    self._dbg(f"Found inverted relationship: {lhs_name} = ~{orig_signal}")

    def _collect_combinational_logic(self, module: ModuleDef):
        """收集模块内的组合逻辑"""
        for item in module.items:
            if isinstance(item, Assign):
                self.assign_nodes.append(item)
                self._dbg(f"Found assign at line {item.lineno}: {self._get_lhs_name(item)}")
            elif isinstance(item, (Always, AlwaysComb)):
                # 检查是否是组合逻辑always块
                if self._is_combinational_always(item):
                    self.always_comb_nodes.append(item)
                    self._dbg(f"Found combinational always at line {item.lineno}")

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

    def _check_assign_statements(self):
        """检查assign语句中的毛刺问题"""
        for assign in self.assign_nodes:
            lhs_name = self._get_lhs_name(assign)
            rhs_expr = assign.right

            # 深度分析RHS表达式，追踪中间信号的反相关系
            signals = self._deep_analyze_expression(rhs_expr)

            # 检查是否有信号和其反相同时使用
            glitch_signals = self._find_inverted_pairs(signals)

            for orig_signal, inv_signal in glitch_signals:
                self.issues.append(GlitchIssue(
                    issue_type=GlitchIssueType.INVERTED_SIGNAL_PAIR,
                    signal_name=orig_signal,
                    lineno=assign.lineno,
                    description=f"Signal '{orig_signal}' and its inversion used together in assignment to '{lhs_name}' - potential glitch hazard",
                    affected_output=lhs_name,
                    severity="warning"
                ))
                self._dbg(f"GLITCH: {orig_signal} and ~{orig_signal} used in {lhs_name}")

    def _check_always_comb_blocks(self):
        """检查always组合块中的毛刺问题"""
        for always in self.always_comb_nodes:
            # 查找always块中的所有赋值
            assignments = self._find_assignments_in_statement(always.statement)

            for stmt, lhs_name in assignments:
                # 获取RHS表达式
                rhs = self._get_rhs_from_stmt(stmt)
                if rhs is None:
                    continue

                # 深度分析RHS表达式，追踪中间信号的反相关系
                signals = self._deep_analyze_expression(rhs)

                # 检查是否有信号和其反相同时使用
                glitch_signals = self._find_inverted_pairs(signals)

                for orig_signal, inv_signal in glitch_signals:
                    self.issues.append(GlitchIssue(
                        issue_type=GlitchIssueType.INVERTED_SIGNAL_PAIR,
                        signal_name=orig_signal,
                        lineno=getattr(stmt, 'lineno', getattr(always, 'lineno', 0)),
                        description=f"Signal '{orig_signal}' and its inversion used together in assignment to '{lhs_name}' - potential glitch hazard",
                        affected_output=lhs_name,
                        severity="warning"
                    ))
                    self._dbg(f"GLITCH: {orig_signal} and ~{orig_signal} used in {lhs_name}")

    def _deep_analyze_expression(self, expr) -> List[SignalSource]:
        """
        深度分析表达式，递归展开中间信号
        """
        all_signals = []
        self._deep_analyze_recursive(expr, all_signals, set())
        return all_signals

    def _deep_analyze_recursive(self, expr, signals: List[SignalSource], visited: set):
        """递归深度分析，展开中间信号"""
        if expr is None:
            return

        # 检查是否是标识符（可能是中间信号）
        if isinstance(expr, Identifier):
            signal_name = expr.name

            # 检查这个信号是否被定义为反相形式
            is_inverted_form = signal_name in self.inverted_signals

            # 首先检查是否是另一个assign的LHS（中间信号）
            # 这样我们可以追踪信号的完整来源链
            if signal_name not in visited:
                visited.add(signal_name)

                for assign in self.assign_nodes:
                    if self._get_lhs_name(assign) == signal_name:
                        if is_inverted_form:
                            # 如果这是反相形式，记录原始信号并递归展开
                            orig_name = self.inverted_signals[signal_name]
                            self._dbg(f"  Expanding inverted signal {signal_name} -> ~{orig_name}")
                            # 递归展开时不立即添加，让递归处理原始信号
                            self._deep_analyze_recursive(assign.right, signals, visited)
                        else:
                            # 普通中间信号，记录信号本身，然后递归展开追踪来源
                            self._dbg(f"  Found intermediate signal: {signal_name}, expanding...")
                            # 先记录这个信号（作为正相形式）
                            signals.append(SignalSource(
                                name=signal_name,
                                lineno=getattr(expr, 'lineno', 0),
                                is_inverted=False,
                                original_signal=signal_name
                            ))
                            # 然后递归展开追踪其来源
                            self._deep_analyze_recursive(assign.right, signals, visited)
                        return

                # 不是中间信号，检查是否是反相形式的最终信号
                if is_inverted_form:
                    orig_name = self.inverted_signals[signal_name]
                    signals.append(SignalSource(
                        name=f"~{orig_name} (via {signal_name})",
                        lineno=getattr(expr, 'lineno', 0),
                        is_inverted=True,
                        original_signal=orig_name
                    ))
                    self._dbg(f"  Found inverted signal: {signal_name} (~{orig_name})")
                    return

            # 普通信号（已访问过或不是中间信号）
            signals.append(SignalSource(
                name=signal_name,
                lineno=getattr(expr, 'lineno', 0),
                is_inverted=False,
                original_signal=signal_name
            ))
            self._dbg(f"  Found signal: {signal_name}")
            return

        # 检查是否是反相操作
        if isinstance(expr, (Ulnot, Unot)):
            if hasattr(expr, 'right'):
                if isinstance(expr.right, Identifier):
                    orig_name = expr.right.name
                    # 检查是否也是中间信号
                    if orig_name in self.inverted_signals:
                        orig_name = self.inverted_signals[orig_name]
                    signals.append(SignalSource(
                        name=f"~{orig_name}",
                        lineno=getattr(expr, 'lineno', 0),
                        is_inverted=True,
                        original_signal=orig_name
                    ))
                    self._dbg(f"  Found inverted signal: ~{orig_name}")
                else:
                    # 递归分析反相操作的内部
                    self._deep_analyze_recursive(expr.right, signals, visited)
            return

        # 递归处理子表达式
        for attr_name in ['left', 'right', 'var', 'cond', 'true_value', 'false_value']:
            if hasattr(expr, attr_name):
                attr_val = getattr(expr, attr_name)
                if attr_val is not None:
                    if isinstance(attr_val, (list, tuple)):
                        for item in attr_val:
                            self._deep_analyze_recursive(item, signals, visited)
                    else:
                        self._deep_analyze_recursive(attr_val, signals, visited)

    def _analyze_expression(self, expr, context_inverted: bool = False) -> List[SignalSource]:
        """
        分析表达式，提取所有信号及其形式（正相/反相）
        context_inverted: 当前表达式是否处于反相上下文中
        """
        signals = []
        self._analyze_expr_recursive(expr, signals, context_inverted)
        return signals

    def _analyze_expr_recursive(self, expr, signals: List[SignalSource], context_inverted: bool = False):
        """递归分析表达式"""
        if expr is None:
            return

        # 检查是否是反相操作: ~signal 或 !signal
        if isinstance(expr, (Ulnot, Unot)):
            if hasattr(expr, 'right') and isinstance(expr.right, Identifier):
                orig_name = expr.right.name
                signals.append(SignalSource(
                    name=f"~{orig_name}",
                    lineno=getattr(expr, 'lineno', 0),
                    is_inverted=True,
                    original_signal=orig_name
                ))
                self._dbg(f"  Found inverted signal: ~{orig_name}")
            return

        # 检查是否是标识符（正相信号或反相信号的中间变量）
        if isinstance(expr, Identifier):
            signal_name = expr.name
            # 检查这个信号是否是某个信号的反相形式
            if signal_name in self.inverted_signals:
                orig_name = self.inverted_signals[signal_name]
                signals.append(SignalSource(
                    name=f"~{orig_name} (via {signal_name})",
                    lineno=getattr(expr, 'lineno', 0),
                    is_inverted=True,
                    original_signal=orig_name
                ))
                self._dbg(f"  Found inverted signal via intermediate: {signal_name} (~{orig_name})")
            else:
                signals.append(SignalSource(
                    name=signal_name,
                    lineno=getattr(expr, 'lineno', 0),
                    is_inverted=context_inverted,
                    original_signal=signal_name
                ))
                self._dbg(f"  Found signal: {signal_name}")
            return

        # 递归处理子表达式
        for attr_name in ['left', 'right', 'var', 'cond', 'true_value', 'false_value']:
            if hasattr(expr, attr_name):
                attr_val = getattr(expr, attr_name)
                if attr_val is not None:
                    if isinstance(attr_val, (list, tuple)):
                        for item in attr_val:
                            self._analyze_expr_recursive(item, signals, context_inverted)
                    else:
                        self._analyze_expr_recursive(attr_val, signals, context_inverted)

    def _find_inverted_pairs(self, signals: List[SignalSource]) -> List[Tuple[str, str]]:
        """
        查找信号和其反相同时使用的对
        返回: [(原始信号名, 反相信号名), ...]
        """
        pairs = []

        self._dbg(f"  Analyzing {len(signals)} signals for inverted pairs")

        # 按原始信号名分组
        signal_groups: Dict[str, List[SignalSource]] = defaultdict(list)
        for sig in signals:
            signal_groups[sig.original_signal].append(sig)
            self._dbg(f"    Grouping: {sig.name} -> original={sig.original_signal}, inverted={sig.is_inverted}")

        self._dbg(f"  Signal groups: {dict(signal_groups)}")

        # 检查每个信号组是否同时有正相和反相
        for orig_name, sig_list in signal_groups.items():
            has_normal = any(not s.is_inverted for s in sig_list)
            has_inverted = any(s.is_inverted for s in sig_list)

            self._dbg(f"    {orig_name}: normal={has_normal}, inverted={has_inverted}, count={len(sig_list)}")

            if has_normal and has_inverted:
                pairs.append((orig_name, f"~{orig_name}"))
                self._dbg(f"  Found inverted pair: {orig_name} and ~{orig_name}")

        return pairs

    def _get_lhs_name(self, assign: Assign) -> str:
        """获取赋值左侧的信号名"""
        if hasattr(assign, 'left'):
            left = assign.left
            if hasattr(left, 'var'):
                left = left.var
            if isinstance(left, Identifier):
                return left.name
        return "unknown"

    def _find_assignments_in_statement(self, stmt) -> List[Tuple[Any, str]]:
        """递归查找语句中的所有赋值，返回[(stmt, lhs_name), ...]"""
        assignments = []

        if stmt is None:
            return assignments

        # 阻塞或非阻塞赋值
        if hasattr(stmt, 'left'):
            lhs = stmt.left
            if hasattr(lhs, 'var'):
                lhs = lhs.var
            if isinstance(lhs, Identifier):
                assignments.append((stmt, lhs.name))

        # 递归处理块语句
        if hasattr(stmt, 'statements'):
            for s in stmt.statements:
                assignments.extend(self._find_assignments_in_statement(s))

        # if语句
        if hasattr(stmt, 'true_statement') and stmt.true_statement:
            assignments.extend(self._find_assignments_in_statement(stmt.true_statement))
        if hasattr(stmt, 'false_statement') and stmt.false_statement:
            assignments.extend(self._find_assignments_in_statement(stmt.false_statement))

        # case语句
        if hasattr(stmt, 'caselist'):
            for case in stmt.caselist:
                if case.statement:
                    assignments.extend(self._find_assignments_in_statement(case.statement))

        return assignments

    def _get_rhs_from_stmt(self, stmt) -> Optional[Any]:
        """从赋值语句中获取右侧表达式"""
        if hasattr(stmt, 'right'):
            return stmt.right
        return None

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
        print("Glitch Check Report")
        print("=" * 70)

        if not self.issues:
            print("No glitch hazards found")
            return

        # 按类型分组
        inverted_pairs = [i for i in self.issues
                         if i.issue_type == GlitchIssueType.INVERTED_SIGNAL_PAIR]

        if inverted_pairs:
            print(f"\n[!] Inverted Signal Pairs ({len(inverted_pairs)}):")
            for issue in inverted_pairs:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        print(f"\nTotal: {len(self.issues)} glitch hazards")


def check_glitch(ast, stb: SymbolTableBuilder, debug: bool = False) -> List[GlitchIssue]:
    """便捷函数：直接检查毛刺问题"""
    checker = GlitchChecker(ast, stb, debug=debug)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python glitch_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    from pyverilog.vparser.parser import parse

    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Check glitch
    checker = GlitchChecker(ast, stb, debug=True)
    issues = checker.check()
    checker.print_report()
