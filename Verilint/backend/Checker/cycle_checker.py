"""
Cycle Checker - 循环依赖检查器

检测 Verilog 中的各种循环依赖问题:
1. 组合逻辑循环 (Combinational Loop)
   - assign 语句形成的环路
   - always @(*) 块内的自赋值

2. 时序逻辑循环依赖 (Sequential Cycle)
   - 跨 always_ff 块的循环依赖
   - 启动条件导致的死锁 (如 fifo.v 中的问题)
   - 状态机循环依赖

3. 混合循环 (Mixed Cycle)
   - 组合逻辑和时序逻辑交织形成的循环
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum, auto
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, Always, AlwaysComb, AlwaysFF, Assign, Identifier,
    NonblockingSubstitution, BlockingSubstitution, Lvalue, IfStatement,
    Partselect, Pointer, Concat, Decl
)

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder
from symbol import Symbol, SymbolType


class CycleType(Enum):
    """循环类型"""
    COMBINATIONAL_DIRECT = auto()    # 直接组合循环 (a = a)
    COMBINATIONAL_INDIRECT = auto()  # 间接组合循环 (a->b->c->a)
    SEQUENTIAL_CYCLE = auto()        # 时序逻辑循环依赖
    INITIALIZATION_DEADLOCK = auto() # 初始化死锁 (如 fifo.v)


@dataclass
class CycleIssue:
    """循环问题报告"""
    cycle_type: CycleType
    signals: List[str]
    lineno: int
    description: str
    severity: str = "error"
    loop_path: List[Tuple[str, int, str]] = field(default_factory=list)  # (信号, 行号, 位置)
    details: str = ""  # 详细说明


@dataclass
class AlwaysBlockInfo:
    """always块信息"""
    name: str
    lineno: int
    is_sequential: bool
    is_combinational: bool
    clock_signal: Optional[str] = None
    reset_signal: Optional[str] = None
    driven_signals: Set[str] = field(default_factory=set)
    used_signals: Set[str] = field(default_factory=set)
    ast_node: Any = None


class CycleChecker:
    """
    循环依赖检查器

    检测:
    - 组合逻辑循环
    - 时序逻辑循环依赖
    - 启动条件死锁
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False):
        self.ast = ast
        self.stb = stb
        self.issues: List[CycleIssue] = []
        self.debug = debug

        # always块列表
        self.always_blocks: List[AlwaysBlockInfo] = []

        # 信号到always块的映射
        self.signal_to_always: Dict[str, List[AlwaysBlockInfo]] = defaultdict(list)

        # 组合逻辑依赖图
        self.comb_deps: Dict[str, Set[str]] = defaultdict(set)

        # 时序逻辑依赖图 (信号 -> 驱动它的always块)
        self.seq_deps: Dict[str, List[AlwaysBlockInfo]] = defaultdict(list)

        # 信号定义位置
        self.signal_defs: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def check(self) -> List[CycleIssue]:
        """执行所有循环检查"""
        self.issues = []
        self.always_blocks.clear()
        self.comb_deps.clear()
        self.seq_deps.clear()

        # 收集所有 always 块信息
        self._collect_always_blocks()

        # 检查1: 组合逻辑循环
        self._check_combinational_loops()

        # 检查2: 时序逻辑循环依赖
        self._detect_cross_always_cycles()

        # 检查3: 初始化死锁 (如 fifo.v 问题)
        # self._check_initialization_deadlock()

        return self.issues

    def _collect_always_blocks(self):
        """收集所有 always 块信息"""
        for module in self._get_modules():
            self._dbg(f"Collecting from module: {module.name}, items: {len(module.items)}")
            self._collect_module_always_blocks(module)

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
            for attr_val in node.__dict__.values():
                if isinstance(attr_val, (list, tuple)):
                    children.extend(attr_val)
                elif attr_val is not None and hasattr(attr_val, '__dict__'):
                    children.append(attr_val)
        return children

    def _collect_module_always_blocks(self, module: ModuleDef):
        """收集模块内的 always 块信息"""
        always_counter = 0
        self._dbg(f"Processing {len(module.items)} items")

        for item in module.items:
            if isinstance(item, (Always, AlwaysComb, AlwaysFF)):
                info = self._analyze_always_block(item, f"always_{always_counter}")
                self.always_blocks.append(info)
                self._dbg(f"Added always block: {info.name} at line {info.lineno}, driven={info.driven_signals}, used={info.used_signals}")
                always_counter += 1

                # 建立信号到 always 块的映射
                for sig in info.driven_signals:
                    self.signal_to_always[sig].append(info)
                    self.seq_deps[sig].append(info)

    def _analyze_always_block(self, always_node, name: str) -> AlwaysBlockInfo:
        """分析 always 块"""
        info = AlwaysBlockInfo(
            name=name,
            lineno=always_node.lineno,
            is_sequential=False,
            is_combinational=False,
            ast_node=always_node
        )

        # 判断类型
        if isinstance(always_node, AlwaysComb):
            info.is_combinational = True
        elif isinstance(always_node, AlwaysFF):
            info.is_sequential = True
        elif hasattr(always_node, 'sens_list') and always_node.sens_list:
            info.is_sequential = self._has_clock_edge(always_node.sens_list)
            info.is_combinational = not info.is_sequential

            # 提取时钟和复位信号
            if info.is_sequential:
                info.clock_signal = self._extract_clock_signal(always_node.sens_list)
                info.reset_signal = self._extract_reset_signal(always_node.sens_list)

        # 收集驱动和使用的信号
        # 首先收集所有使用的信号（包括if条件中的）
        all_used = self._collect_all_used_signals(always_node.statement)
        info.used_signals.update(all_used)
        assignments = self._find_assignments(always_node.statement)
        for stmt, is_blocking in assignments:
            targets = self._get_assignment_targets(stmt)
            sources = self._extract_identifiers(stmt.right)

            info.driven_signals.update(targets)
            info.used_signals.update(sources)

            # 记录组合逻辑依赖
            if info.is_combinational:
                for target in targets:
                    for source in sources:
                        if target != source:
                            self.comb_deps[target].add(source)
                    self.signal_defs[target].append((stmt.lineno, 'always_comb'))

        return info

    def _has_clock_edge(self, sens_list) -> bool:
        """检查敏感列表是否包含时钟边沿"""
        if hasattr(sens_list, 'list'):
            for sens in sens_list.list:
                if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                    return True
        return False

    def _extract_clock_signal(self, sens_list) -> Optional[str]:
        """提取时钟信号名"""
        if hasattr(sens_list, 'list'):
            for sens in sens_list.list:
                if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                    if hasattr(sens, 'sig') and isinstance(sens.sig, Identifier):
                        return sens.sig.name
        return None

    def _extract_reset_signal(self, sens_list) -> Optional[str]:
        """提取复位信号名 (第二个边沿敏感信号)"""
        edge_signals = []
        if hasattr(sens_list, 'list'):
            for sens in sens_list.list:
                if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                    if hasattr(sens, 'sig') and isinstance(sens.sig, Identifier):
                        edge_signals.append(sens.sig.name)
        # 返回第二个边沿信号（如果有）
        return edge_signals[1] if len(edge_signals) > 1 else None

    def _check_combinational_loops(self):
        """检查组合逻辑循环"""
        # 检查直接自循环
        for module in self._get_modules():
            self._dbg(f"Collecting from module: {module.name}, items: {len(module.items)}")
            self._check_module_combinational_loops(module)

        # 使用 DFS 检查间接循环
        self._detect_combinational_cycles()

    def _check_module_combinational_loops(self, module: ModuleDef):
        """检查模块内的组合逻辑循环"""
        for item in module.items:
            if isinstance(item, Assign):
                self._check_assign_loop(item)
            elif isinstance(item, Always) and self._is_combinational_always(item):
                self._check_always_comb_loops(item)

    def _is_combinational_always(self, always_node) -> bool:
        """检查是否是组合逻辑 always"""
        if isinstance(always_node, AlwaysComb):
            return True
        if not hasattr(always_node, 'sens_list') or not always_node.sens_list:
            return False
        if hasattr(always_node.sens_list, 'list'):
            for sens in always_node.sens_list.list:
                if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                    return False
        return True

    def _check_assign_loop(self, assign_node: Assign):
        """检查 assign 语句的循环"""
        targets = self._get_assignment_targets(assign_node)
        sources = self._extract_identifiers(assign_node.right)

        for target in targets:
            self.signal_defs[target].append((assign_node.lineno, 'assign'))
            if target in sources:
                a=1
                # self._report_direct_loop(target, assign_node.lineno, 'assign')
            for source in sources:
                if source != target:
                    self.comb_deps[target].add(source)

    def _check_always_comb_loops(self, always_node):
        """检查组合逻辑 always 块的循环"""
        assignments = self._find_assignments(always_node.statement)
        for stmt, is_blocking in assignments:
            targets = self._get_assignment_targets(stmt)
            sources = self._extract_identifiers(stmt.right)

            for target in targets:
                stmt_type = 'blocking' if is_blocking else 'nonblocking'
                self.signal_defs[target].append((stmt.lineno, f'always_{stmt_type}'))

                if target in sources:
                    # self._report_direct_loop(target, stmt.lineno, stmt_type)
                    a=1
                for source in sources:
                    if source != target:
                        self.comb_deps[target].add(source)

    def _detect_combinational_cycles(self):
        """使用 DFS 检测组合逻辑环路"""
        visited = set()
        rec_stack = set()
        path = []

        def dfs(node):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self.comb_deps.get(node, set()):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    self._report_indirect_loop(cycle)
                    return True

            path.pop()
            rec_stack.remove(node)
            return False

        for node in list(self.comb_deps.keys()):
            if node not in visited:
                dfs(node)

# 新增方法：构建跨 always 块依赖图并检测循环

    def _build_cross_always_dependency_graph(self):
        """构建跨 always 块的依赖图"""
        graph = defaultdict(set)
        for block_a in self.always_blocks:
            for block_b in self.always_blocks:
                if block_a == block_b:
                    continue
                shared_signals = block_a.driven_signals & block_b.used_signals
                if shared_signals:
                    graph[block_a.name].add(block_b.name)
                    self._dbg(f"Cross-always dependency: {block_a.name} -> {block_b.name} via {shared_signals}")
        return graph

    def _detect_cross_always_cycles(self):
        """检测跨 always 块的循环依赖"""
        graph = self._build_cross_always_dependency_graph()
        visited = set()
        rec_stack = set()
        path = []
        path_blocks = []

        def dfs(block_name):
            visited.add(block_name)
            rec_stack.add(block_name)
            path.append(block_name)
            block = next((b for b in self.always_blocks if b.name == block_name), None)
            if block:
                path_blocks.append(block)

            for neighbor in graph.get(block_name, set()):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycle_blocks = path_blocks[cycle_start:]
                    # self._report_cross_always_cycle(cycle, cycle_blocks)
                    return True

            path.pop()
            if path_blocks:
                path_blocks.pop()
            rec_stack.remove(block_name)
            return False

        for block_name in list(graph.keys()):
            if block_name not in visited:
                dfs(block_name)

    def _report_cross_always_cycle(self, cycle, blocks):
        """报告跨 always 块的循环依赖"""
        signal_chain = []
        for i, block in enumerate(blocks):
            next_block = blocks[(i + 1) % len(blocks)]
            shared = block.driven_signals & next_block.used_signals
            if shared:
                signal_chain.extend(sorted(shared))

        seen = set()
        unique_chain = []
        for sig in signal_chain:
            if sig not in seen:
                seen.add(sig)
                unique_chain.append(sig)

        main_block = blocks[0] if blocks else None
        if not main_block:
            return

        is_startup_deadlock = self._is_startup_deadlock_pattern(blocks, unique_chain)
        cycle_desc = " -> ".join(unique_chain)
        if unique_chain:
            cycle_desc += f" -> {unique_chain[0]}"

        if is_startup_deadlock:
            description = f"Startup deadlock detected: {cycle_desc}"
            details = f"Cross-always-block cycle forms a startup deadlock. Initial values may prevent circuit from starting. Chain: {cycle_desc}"
            severity = 'error'
            cycle_type = CycleType.INITIALIZATION_DEADLOCK
        else:
            description = f"Cross-always-block cycle: {cycle_desc}"
            details = f"Sequential always blocks form circular dependency: {cycle_desc}"
            severity = 'warning'
            cycle_type = CycleType.SEQUENTIAL_CYCLE

        self.issues.append(CycleIssue(
            cycle_type=cycle_type,
            signals=unique_chain,
            lineno=main_block.lineno,
            description=description,
            severity=severity,
            details=details
        ))
        self._dbg(f"CROSS-ALWAYS CYCLE: {' -> '.join(cycle)}")

    def _is_startup_deadlock_pattern(self, blocks, signal_chain):
        """检查是否是启动死锁模式"""
        for block in blocks:
            for driven_sig in block.driven_signals:
                if driven_sig in signal_chain and driven_sig in block.used_signals:
                    if self._signal_controls_its_update(block, driven_sig):
                        return True
        return False

    def _signal_controls_its_update(self, block, signal):
        """检查信号是否控制着自己的更新条件"""
        if not block.ast_node or not hasattr(block.ast_node, 'statement'):
            return False
        return self._check_conditional_control(block.ast_node.statement, signal)

    def _check_conditional_control(self, stmt, signal, in_condition=False):
        """递归检查信号是否控制着包含自身赋值的更新"""
        if stmt is None:
            return False
        if isinstance(stmt, IfStatement):
            cond_signals = set(self._extract_identifiers(stmt.cond))
            signal_in_cond = signal in cond_signals
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                if signal_in_cond and self._has_assignment_to(stmt.true_statement, signal):
                    return True
                if self._check_conditional_control(stmt.true_statement, signal, in_condition or signal_in_cond):
                    return True
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                if signal_in_cond and self._has_assignment_to(stmt.false_statement, signal):
                    return True
                if self._check_conditional_control(stmt.false_statement, signal, in_condition or signal_in_cond):
                    return True
        if hasattr(stmt, 'statements'):
            for s in stmt.statements:
                if self._check_conditional_control(s, signal, in_condition):
                    return True
        if hasattr(stmt, 'caselist'):
            for case in stmt.caselist:
                if case.statement and self._check_conditional_control(case.statement, signal, in_condition):
                    return True
        return False

    def _has_assignment_to(self, stmt, signal):
        """检查语句是否包含对特定信号的赋值"""
        if stmt is None:
            return False
        assignments = self._find_assignments(stmt)
        for assign_stmt, _ in assignments:
            targets = self._get_assignment_targets(assign_stmt)
            if signal in targets:
                return True
        return False

    # def _check_initialization_deadlock(self):
    #     """
    #     检查初始化死锁 (如 fifo.v 中的问题)

    #     模式:
    #     1. 信号 A 初始为某值
    #     2. 在 always_ff 中，A 控制 B 的更新
    #     3. B 控制 C 的更新
    #     4. C 的某个条件影响 A 的更新
    #     5. 如果初始状态导致条件不满足，A 永远不会被更新，形成死锁
    #     """
    #     for block in self.always_blocks:
    #         if not block.is_sequential:
    #             continue

            # 检查是否存在 "初始化值阻止更新" 的模式
            # 即：某个信号控制着自己的更新条件
            # self._check_self_blocking_pattern(block)

    def _check_self_blocking_pattern(self, block: AlwaysBlockInfo):
        """
        检查自阻塞模式:
        信号 X 控制着一个条件，该条件为真时 X 不会被更新
        这意味着如果 X 的初始值使条件为真，X 将永远不会被更新
        """
        for driven_sig in block.driven_signals:
            if driven_sig in block.used_signals:
                # 信号既被驱动又被使用，可能形成自阻塞
                # 检查是否是条件控制的模式
                if self._is_conditional_update(block, driven_sig):
                    self.issues.append(CycleIssue(
                        cycle_type=CycleType.INITIALIZATION_DEADLOCK,
                        signals=[driven_sig],
                        lineno=block.lineno,
                        description=f"Potential initialization deadlock: "
                                   f"'{driven_sig}' controls its own update condition",
                        severity='warning',
                        details=f"Signal '{driven_sig}' is used in a condition that "
                               f"controls whether it gets updated. If the initial value "
                               f"prevents the update, the signal will never change."
                    ))
                    self._dbg(f"INIT DEADLOCK: {driven_sig} at line {block.lineno}")

    def _is_conditional_update(self, block: AlwaysBlockInfo, signal: str) -> bool:
        """检查信号是否通过条件控制更新"""
        # 简化的检查：如果信号在 used_signals 中，且在 ast 中有条件结构
        # 实际实现需要更复杂的 AST 分析
        if block.ast_node and hasattr(block.ast_node, 'statement'):
            return self._has_conditional_with_signal(block.ast_node.statement, signal)
        return False

    def _has_conditional_with_signal(self, stmt, signal: str) -> bool:
        """递归检查语句中是否有包含信号的条件"""
        if stmt is None:
            return False

        # 检查 if 语句
        if isinstance(stmt, IfStatement):
            cond_signals = self._extract_identifiers(stmt.cond)
            if signal in cond_signals:
                return True
            # 递归检查分支
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                if self._has_conditional_with_signal(stmt.true_statement, signal):
                    return True
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                if self._has_conditional_with_signal(stmt.false_statement, signal):
                    return True

        # 递归检查块语句
        if hasattr(stmt, 'statements'):
            for s in stmt.statements:
                if self._has_conditional_with_signal(s, signal):
                    return True

        return False

    def _collect_all_used_signals(self, stmt) -> Set[str]:
        """收集语句中所有使用的信号（包括if条件中）"""
        used = set()
        if stmt is None:
            return used
        
        # 如果是赋值语句，收集右侧
        if isinstance(stmt, (BlockingSubstitution, NonblockingSubstitution)):
            used.update(self._extract_identifiers(stmt.right))
        
        # 如果是if语句，收集条件和分支中的信号
        if isinstance(stmt, IfStatement):
            used.update(self._extract_identifiers(stmt.cond))
            if hasattr(stmt, "true_statement") and stmt.true_statement:
                used.update(self._collect_all_used_signals(stmt.true_statement))
            if hasattr(stmt, "false_statement") and stmt.false_statement:
                used.update(self._collect_all_used_signals(stmt.false_statement))
        
        # 递归处理块语句
        if hasattr(stmt, "statements"):
            for s in stmt.statements:
                used.update(self._collect_all_used_signals(s))
        
        return used

    def _find_assignments(self, stmt) -> List[Tuple[Any, bool]]:
        """查找所有赋值语句"""
        assignments = []
        if stmt is None:
            return assignments

        if isinstance(stmt, BlockingSubstitution):
            assignments.append((stmt, True))
        elif isinstance(stmt, NonblockingSubstitution):
            assignments.append((stmt, False))
        elif hasattr(stmt, 'statements'):
            for s in stmt.statements:
                assignments.extend(self._find_assignments(s))
        elif hasattr(stmt, 'true_statement') or hasattr(stmt, 'false_statement'):
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                assignments.extend(self._find_assignments(stmt.true_statement))
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                assignments.extend(self._find_assignments(stmt.false_statement))
        elif hasattr(stmt, 'caselist'):
            for case in stmt.caselist:
                if case.statement:
                    assignments.extend(self._find_assignments(case.statement))

        return assignments

    def _get_assignment_targets(self, stmt) -> List[str]:
        """获取赋值目标"""
        targets = []
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_identifiers_from_lval(lval))
        elif isinstance(stmt, Assign):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_identifiers_from_lval(lval))
        return targets

    def _extract_identifiers_from_lval(self, lval) -> List[str]:
        """从左值提取标识符"""
        identifiers = []
        if isinstance(lval, Identifier):
            identifiers.append(lval.name)
        elif isinstance(lval, (Partselect, Pointer)):
            if isinstance(lval.var, Identifier):
                identifiers.append(lval.var.name)
        elif isinstance(lval, Concat):
            if hasattr(lval, 'list') and lval.list:
                for item in lval.list:
                    identifiers.extend(self._extract_identifiers_from_lval(item))
        return identifiers

    def _extract_identifiers(self, expr) -> List[str]:
        """从表达式提取所有标识符"""
        identifiers = []
        if expr is None:
            return identifiers

        if isinstance(expr, Identifier):
            identifiers.append(expr.name)
        elif isinstance(expr, (Partselect, Pointer)):
            if hasattr(expr, 'var') and isinstance(expr.var, Identifier):
                identifiers.append(expr.var.name)
        elif isinstance(expr, Concat):
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    identifiers.extend(self._extract_identifiers(item))
        else:
            for attr_name in ['left', 'right', 'var', 'ptr', 'msb', 'lsb', 'cond', 'true_value', 'false_value']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                identifiers.extend(self._extract_identifiers(item))
                        elif hasattr(attr_val, '__dict__'):
                            identifiers.extend(self._extract_identifiers(attr_val))
        return identifiers

    def _report_direct_loop(self, signal: str, lineno: int, stmt_type: str):
        """报告直接自循环"""
        self.issues.append(CycleIssue(
            cycle_type=CycleType.COMBINATIONAL_DIRECT,
            signals=[signal],
            lineno=lineno,
            description=f"Direct combinational loop: '{signal}' is assigned to itself",
            severity='error',
            loop_path=[(signal, lineno, stmt_type)]
        ))
        self._dbg(f"DIRECT LOOP: {signal} at line {lineno}")

    def _report_indirect_loop(self, cycle: List[str]):
        """报告间接循环"""
        loop_path = []
        for signal in cycle[:-1]:
            locations = self.signal_defs.get(signal, [(0, 'unknown')])
            lineno = locations[0][0] if locations else 0
            loop_path.append((signal, lineno, 'assign' if lineno > 0 else 'unknown'))

        signals_in_loop = cycle[:-1]
        main_lineno = loop_path[0][1] if loop_path else 0

        self.issues.append(CycleIssue(
            cycle_type=CycleType.COMBINATIONAL_INDIRECT,
            signals=signals_in_loop,
            lineno=main_lineno,
            description=f"Combinational loop: {' -> '.join(signals_in_loop)} -> {signals_in_loop[0]}",
            severity='error',
            loop_path=loop_path,
            details=f"Circular dependency detected in combinational logic"
        ))
        self._dbg(f"INDIRECT LOOP: {' -> '.join(signals_in_loop)}")

    def print_report(self):
        """打印检查报告"""
        print("\n" + "=" * 70)
        print("Cycle Dependency Check Report")
        print("=" * 70)

        if not self.issues:
            print("No cycle dependencies found")
            return

        # 按类型分组
        by_type = defaultdict(list)
        for issue in self.issues:
            by_type[issue.cycle_type].append(issue)

        type_names = {
            CycleType.COMBINATIONAL_DIRECT: "Combinational Direct Loops",
            CycleType.COMBINATIONAL_INDIRECT: "Combinational Indirect Loops",
            CycleType.SEQUENTIAL_CYCLE: "Sequential Cycle Dependencies",
            CycleType.INITIALIZATION_DEADLOCK: "Initialization Deadlocks"
        }

        for cycle_type, issues in by_type.items():
            name = type_names.get(cycle_type, str(cycle_type))
            print(f"\n[!] {name} ({len(issues)}):")
            for issue in issues:
                print(f"  Line {issue.lineno:3d}: {issue.description}")
                if issue.details:
                    print(f"         Details: {issue.details}")

        print(f"\nTotal: {len(self.issues)} cycle issues")


def check_cycles(ast, stb: SymbolTableBuilder, debug: bool = False) -> List[CycleIssue]:
    """便捷函数：检查所有循环依赖"""
    checker = CycleChecker(ast, stb, debug=debug)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cycle_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    from pyverilog.vparser.parser import parse
    ast, _ = parse([verilog_file])

    stb = SymbolTableBuilder()
    stb.build(ast)

    checker = CycleChecker(ast, stb, debug=True)
    issues = checker.check()
    checker.print_report()
