from typing import List, Set, Dict, Optional
from dataclasses import dataclass
from enum import Enum
from cycle_table_builder import CycleTableBuilder, CycleList, Cycle
from dfg_builder import DFGBuilder
from symbol import Symbol


class ResetIssueType(Enum):
    NOT_RESET = "not_reset"  # 未复位


@dataclass
class ResetIssue:
    symbol_name: str  # 信号名（如果是分组报告，则为空）
    issue_type: ResetIssueType  # 问题类型
    always_name: str  # always块名
    lineno: int = 0  # 行号（always块的行号）
    description: str = ""  # 描述
    missing_vars: List[str] = None  # 缺少复位的变量列表（用于分组报告）


class ResetChecker:
    def __init__(self, cycle_builder: CycleTableBuilder, debug: bool = False):
        self.cycle_builder = cycle_builder
        self.issues: List[ResetIssue] = []
        self.debug = debug
        self.reported: Set[Symbol] = set()

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def check(self) -> List[ResetIssue]:
        """执行复位检测 - 按always块分组报告"""
        self.issues = []
        self.reported = set()

        # 按always块收集问题
        from collections import defaultdict
        issues_by_always: Dict[str, List[ResetIssue]] = defaultdict(list)

        # 遍历所有 always 块的周期列表
        for always_name, cycle_list in self.cycle_builder.always_cycle.items():
            block_issues = self._check_cycle_list(always_name, cycle_list)
            if block_issues:
                issues_by_always[always_name] = block_issues

        # 按always块生成汇总报告
        for always_name, block_issues in issues_by_always.items():
            always_lineno = self._get_always_lineno(always_name)
            var_list = [issue.symbol_name for issue in block_issues]

            # 创建一个汇总issue，包含该always块中所有缺少复位的变量
            summary_issue = ResetIssue(
                symbol_name="",  # 空表示这是分组报告
                issue_type=ResetIssueType.NOT_RESET,
                always_name=always_name,
                lineno=always_lineno,
                description=f"Always block at line {always_lineno}: {len(var_list)} signal(s) lack reset: {', '.join(var_list)}",
                missing_vars=var_list
            )
            self.issues.append(summary_issue)

        return self.issues

    def _check_cycle_list(self, always_name: str, cycle_list: CycleList) -> List[ResetIssue]:
        """检查单个 always 块的周期列表，返回该块中的问题列表"""
        self._dbg(f"\nChecking: {always_name}")

        block_issues = []

        # 获取需要检查的寄存器
        regs_to_check = cycle_list.registers
        if not regs_to_check:
            self._dbg(f"  No registers to check")
            return block_issues

        self._dbg(f"  Registers to check: {[getattr(r, 'name', str(r)) for r in regs_to_check]}")

        # 检查每个周期，看是否有状态为 -1 的
        flag = 0
        for cycle in cycle_list.cycles:
            if -1 in cycle.symbol_state.values():
                flag = 1
            else:
                flag = 0

        self._dbg(f"  Flag: {flag}")

        if flag == 0:
            self._dbg(f"  All registers are properly reset")
            return block_issues

        # 存在未定义状态，检查 cycle 0
        cycle_0 = cycle_list.get_cycle(0)
        if not cycle_0:
            return block_issues

        # 找出在 cycle 0 为 UNDEFINED 的寄存器
        for reg in regs_to_check:
            if reg in self.reported:
                continue

            if reg not in cycle_0.symbol_state:
                continue

            state = cycle_0.symbol_state[reg]
            if state == -1:
                issue = self._check_propagation(reg, regs_to_check, cycle_list, always_name)
                if issue:
                    block_issues.append(issue)

        return block_issues

    def _check_propagation(self, reg: Symbol, cyclic_regs: Set[Symbol],
                           cycle_list: CycleList, always_name: str) -> Optional[ResetIssue]:
        """检查未复位状态的传播，返回问题issue"""
        # 获取该寄存器的依赖
        # 如果该寄存器的依赖都是已定义的（有复位的），但它自己没复位
        # 则它是一个传播源头

        # 简化逻辑：只要有循环依赖且 cycle 0 为 -1，就报告
        # 因为循环依赖意味着它的值依赖于自身或其他寄存器的上一周期值
        # 如果初始未定义，会导致无限期未定义

        return self._report_issue(reg, always_name)

    def _report_issue(self, symbol: Symbol, always_name: str) -> Optional[ResetIssue]:
        """创建复位问题issue - lineno为always块行号"""
        if symbol in self.reported:
            return None

        self.reported.add(symbol)

        symbol_name = getattr(symbol, 'name', str(symbol))
        # 获取always块行号，而非变量声明行号
        always_lineno = self._get_always_lineno(always_name)

        issue = ResetIssue(
            symbol_name=symbol_name,
            issue_type=ResetIssueType.NOT_RESET,
            always_name=always_name,
            lineno=always_lineno,  # 使用always块行号
            description=f"Signal '{symbol_name}' lacks reset assignment"
        )
        self._dbg(f"  REPORTED: {symbol_name} (always at line {always_lineno})")
        return issue

    def _get_always_lineno(self, always_name: str) -> int:
        """从always块名称中提取行号"""
        # always_name format: module_always_X_seq/comb or similar
        import re
        # Match pattern like "top_module_always_14_seq"
        match = re.search(r'always_(\d+)_(seq|comb)', always_name)
        if match:
            return int(match.group(1))
        # Fallback: match any _number_ pattern
        match = re.search(r'_(\d+)_[a-z]+$', always_name)
        if match:
            return int(match.group(1))
        return 0

    def print_report(self):
        """打印检测报告 - 按always块分组显示"""
        print("\n" + "=" * 70)
        print("Reset Check Report")
        print("=" * 70)

        if not self.issues:
            print("No reset issues found")
            return

        print(f"\n[Warning] Found {len(self.issues)} always block(s) with reset issues:")

        for issue in self.issues:
            always_lineno = self._get_always_lineno(issue.always_name)
            if issue.missing_vars:
                print(f"\n  [Always Block at line {always_lineno:3d}] Missing reset for: {', '.join(issue.missing_vars)}")
            else:
                # Fallback for legacy format
                print(f"\n  [Always Block at line {always_lineno:3d}] Missing reset for: {issue.symbol_name}")


def check_reset(dfg_builder: DFGBuilder, debug: bool = False) -> List[ResetIssue]:
    """便捷函数：直接检查复位问题"""
    cycle_builder = CycleTableBuilder(dfg_builder)
    cycle_builder.build()

    checker = ResetChecker(cycle_builder, debug=debug)
    issues = checker.check()

    return issues
