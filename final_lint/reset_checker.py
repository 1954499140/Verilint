"""
基于CycleTable的复位检测器 (保守策略)

检测原理：
- 只检查信号是否在复位条件(if rst/rst_n)的分支中被赋值
- 如果不在复位分支中赋值，即报告为未复位
- 保守策略：宁可误报，也不漏报
"""

from typing import List, Set
from dataclasses import dataclass
from enum import Enum
from pyverilog.vparser.ast import Block, IfStatement, NonblockingSubstitution, BlockingSubstitution, Lvalue, Identifier, Operator
from cycle_table_builder import CycleTableBuilder, CycleTable
from dfg_builder import DFGBuilder


class ResetIssueType(Enum):
    """复位问题类型"""
    NOT_RESET = "not_reset"              # 未复位
    UNSTABLE = "unstable"                # 条件依赖导致状态不稳定


@dataclass
class ResetIssue:
    """复位问题"""
    symbol_name: str                     # 信号名
    issue_type: ResetIssueType          # 问题类型
    always_name: str                     # always块名
    lineno: int = 0                      # 行号
    description: str = ""               # 描述


class ResetChecker:
    """保守策略复位检测器"""

    def __init__(self, cycle_builder: CycleTableBuilder, debug: bool = False):
        self.cycle_builder = cycle_builder
        self.issues: List[ResetIssue] = []
        self.debug = debug

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def check(self) -> List[ResetIssue]:
        """执行复位检测"""
        self.issues = []

        for table_name, table in self.cycle_builder.cycle_tables.items():
            if table.is_sequential:
                self._check_table(table)

        return self.issues

    def _check_table(self, table: CycleTable):
        """检查单个周期表格"""
        # 找到always块AST
        always_node = None
        for _, dfg in self.cycle_builder.dfg_builder.dfgs.items():
            for sg_name, sg in dfg.subgraphs.items():
                if sg_name == table.always_name:
                    always_node = sg.always_ast_node
                    break
            if always_node:
                break

        if not always_node:
            return

        # 获取该always块中所有被赋值的符号
        assigned_symbols = self._get_all_assigned_symbols(always_node.statement)

        # 获取在复位分支中被赋值的符号
        reset_symbols = self._get_reset_assigned_symbols(always_node.statement, table.reset_signal)

        self._dbg(f"Table: {table.always_name}")
        self._dbg(f"  All assigned: {assigned_symbols}")
        self._dbg(f"  Reset assigned: {reset_symbols}")

        # 保守策略：所有被赋值的符号都应该在复位分支中被赋值
        for symbol_name in assigned_symbols:
            # 检查是否有条件依赖
            cond_deps = self._get_conditional_dependencies(always_node, symbol_name, table.reset_signal)

            if symbol_name not in reset_symbols:
                # 完全未复位
                symbol = self._lookup_symbol(symbol_name)
                if symbol:
                    issue = ResetIssue(
                        symbol_name=symbol_name,
                        issue_type=ResetIssueType.NOT_RESET,
                        always_name=table.always_name,
                        lineno=symbol.lineno,
                        description=f"Signal '{symbol_name}' not assigned in reset branch"
                    )
                    self.issues.append(issue)
            elif cond_deps:
                # 复位了但有条件依赖，可能导致不稳定状态
                symbol = self._lookup_symbol(symbol_name)
                if symbol:
                    issue = ResetIssue(
                        symbol_name=symbol_name,
                        issue_type=ResetIssueType.UNSTABLE,
                        always_name=table.always_name,
                        lineno=symbol.lineno,
                        description=f"Signal '{symbol_name}' has conditional dependencies: {', '.join(cond_deps)}"
                    )
                    self.issues.append(issue)

    def _get_all_assigned_symbols(self, stmt) -> Set[str]:
        """获取语句中所有被赋值的符号"""

        assigned = set()

        if stmt is None:
            return assigned

        if isinstance(stmt, Block):
            for s in stmt.statements:
                assigned.update(self._get_all_assigned_symbols(s))

        elif isinstance(stmt, IfStatement):
            assigned.update(self._get_all_assigned_symbols(stmt.true_statement))
            assigned.update(self._get_all_assigned_symbols(stmt.false_statement))

        elif isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            if isinstance(lval, Identifier):
                assigned.add(lval.name)

        return assigned

    def _get_reset_assigned_symbols(self, stmt, reset_signal: str) -> Set[str]:
        """获取在复位分支中被赋值的符号"""
        if stmt is None or not reset_signal:
            return set()

        reset_symbols = set()
        self._collect_reset_symbols(stmt, reset_signal, reset_symbols, in_reset=False)
        return reset_symbols

    def _collect_reset_symbols(self, stmt, reset_signal: str, reset_symbols: Set, in_reset: bool):
        """递归收集复位分支中的赋值符号"""
        if stmt is None:
            return

        if isinstance(stmt, Block):
            for s in stmt.statements:
                self._collect_reset_symbols(s, reset_signal, reset_symbols, in_reset)

        elif isinstance(stmt, IfStatement):
            # 检查条件是否是复位条件
            cond_str = self._cond_to_str(stmt.cond).lower()
            is_reset_cond = reset_signal.lower() in cond_str

            self._dbg(f"  If cond: {cond_str}, is_reset={is_reset_cond}")

            # true分支 - 如果是复位条件，则标记为在复位分支中
            if stmt.true_statement:
                self._collect_reset_symbols(stmt.true_statement, reset_signal, reset_symbols,
                                            in_reset or is_reset_cond)

            # false分支 - 正常处理
            if stmt.false_statement:
                self._collect_reset_symbols(stmt.false_statement, reset_signal, reset_symbols, in_reset)

        elif isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            if in_reset:
                lval = stmt.left
                if isinstance(lval, Lvalue):
                    lval = lval.var
                if isinstance(lval, Identifier):
                    reset_symbols.add(lval.name)
                    self._dbg(f"    Found reset assignment: {lval.name}")

    def _get_conditional_dependencies(self, stmt, symbol_name: str, reset_signal: str) -> Set[str]:
        """获取符号的条件依赖（在复位分支中赋值时依赖的非复位条件）"""
        cond_deps = set()
        self._collect_cond_deps(stmt, symbol_name, reset_signal, cond_deps, in_reset=False)
        return cond_deps

    def _collect_cond_deps(self, stmt, symbol_name: str, reset_signal: str,
                           cond_deps: Set, in_reset: bool, current_conds: Set = None):
        """递归收集条件依赖"""
        if current_conds is None:
            current_conds = set()

        if stmt is None:
            return

        if isinstance(stmt, Block):
            for s in stmt.statements:
                self._collect_cond_deps(s, symbol_name, reset_signal, cond_deps, in_reset, current_conds)

        elif isinstance(stmt, IfStatement):
            cond_str = self._cond_to_str(stmt.cond).lower()
            is_reset_cond = reset_signal.lower() in cond_str

            # Collect condition dependencies (excluding reset)
            cond_syms = self._extract_symbols_from_cond(stmt.cond)

            if stmt.true_statement:
                new_conds = current_conds | cond_syms if not is_reset_cond else current_conds
                self._collect_cond_deps(stmt.true_statement, symbol_name, reset_signal,
                                        cond_deps, in_reset or is_reset_cond, new_conds)

            if stmt.false_statement:
                new_conds = current_conds | cond_syms if not is_reset_cond else current_conds
                self._collect_cond_deps(stmt.false_statement, symbol_name, reset_signal,
                                        cond_deps, in_reset and not is_reset_cond, new_conds)

        elif isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            if in_reset:
                lval = stmt.left
                if isinstance(lval, Lvalue):
                    lval = lval.var
                if isinstance(lval, Identifier) and lval.name == symbol_name:
                    # Found assignment in reset branch, check if it has condition dependencies
                    cond_deps.update(current_conds)

    def _extract_symbols_from_cond(self, cond) -> Set[str]:
        """从条件表达式中提取符号"""
        symbols = set()

        if isinstance(cond, Identifier):
            symbols.add(cond.name)
        elif isinstance(cond, Operator):
            if hasattr(cond, 'left'):
                symbols.update(self._extract_symbols_from_cond(cond.left))
            if hasattr(cond, 'right'):
                symbols.update(self._extract_symbols_from_cond(cond.right))
        return symbols

    def _cond_to_str(self, cond) -> str:
        """将条件表达式转换为字符串"""
        if isinstance(cond, Identifier):
            return cond.name
        return str(cond)

    def _lookup_symbol(self, name: str):
        """查找符号"""
        return self.cycle_builder.dfg_builder.stb.lookup(name,
            self.cycle_builder.dfg_builder.stb.root_scope)

    def print_report(self):
        """打印检测报告"""
        print("\n" + "=" * 70)
        print("复位检测报告 (保守策略)")
        print("=" * 70)

        if not self.issues:
            print("未发现未复位信号")
            return

        print(f"\n[警告] 发现 {len(self.issues)} 个未在复位分支中赋值的信号:")
        for issue in self.issues:
            print(f"  - {issue.symbol_name:20s} (line {issue.lineno:3d})")



def check_reset(dfg_builder: DFGBuilder, debug: bool = False) -> List[ResetIssue]:
    """便捷函数：直接检查复位问题"""
    cycle_builder = CycleTableBuilder(dfg_builder)
    cycle_builder.build()

    checker = ResetChecker(cycle_builder, debug=debug)
    issues = checker.check()

    return issues
