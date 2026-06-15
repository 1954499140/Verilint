"""
时钟周期表格构建器

核心逻辑：
1. 识别时序逻辑过程块中的物理寄存器（被时钟边沿触发的非阻塞赋值）
2. 分析寄存器间的数据依赖关系，构建依赖图
3. 检测循环依赖（环）- 只关注有循环依赖的寄存器
4. 根据复位情况构建循环表，看未复位状态是否会传递
"""

from typing import Optional, Dict, List, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pyverilog.vparser.ast import *
from symbol import Symbol, SymbolType
from symbol_table_builder import SymbolTableBuilder
from dfg_builder import DataflowGraph, DFGBuilder


@dataclass
class Cycle:
    """单个时钟周期的状态"""
    # symbol -> state: -1 = undefined, 1 = defined
    symbol_state: Dict[Symbol, int] = field(default_factory=dict)


@dataclass
class CycleList:
    """一个 always 块的所有周期"""
    always_name: str
    always_node: Always
    registers: Set[Symbol] = field(default_factory=set)  # 有循环依赖的寄存器
    cycles: List[Cycle] = field(default_factory=list)

    def add_cycle(self, cycle: Cycle):
        self.cycles.append(cycle)

    def get_cycle(self, cycle_id: int) -> Optional[Cycle]:
        if 0 <= cycle_id < len(self.cycles):
            return self.cycles[cycle_id]
        return None


class CycleTableBuilder:
    """
    时钟周期表格构建器

    只关注有循环依赖的寄存器，检查未复位状态是否会传递
    """

    def __init__(self, dfg_builder: DFGBuilder):
        self.dfg_builder = dfg_builder
        self.always_cycle: Dict[str, CycleList] = {}

    def build(self) -> Dict[str, CycleList]:
        """为所有模块构建周期表格"""
        self.always_cycle = {}

        for module_name, dfg in self.dfg_builder.dfgs.items():
            self._build_for_module(module_name, dfg)

        return self.always_cycle

    def _build_for_module(self, module_name: str, dfg: DataflowGraph):
        """为单个模块构建周期表格"""
        for subgraph_name, subgraph in dfg.subgraphs.items():
            if subgraph.is_sequential:
                self._build_always_cycle(subgraph_name, subgraph)

    def _build_always_cycle(self, always_name: str, subgraph: DataflowGraph):
        """为单个 always 块构建周期列表"""
        always_node = subgraph.always_ast_node
        if not always_node or not isinstance(always_node, Always):
            return

        # 1. 识别所有物理寄存器（非阻塞赋值的左值）
        all_registers = self._identify_all_registers(always_node)
        if not all_registers:
            return

        # 2. 构建寄存器间的依赖图
        dependency_graph = self._build_dependency_graph(always_node, all_registers)

        # 3. 检测循环依赖（只关注有环的寄存器）
        cyclic_registers = self._find_cyclic_registers(all_registers, dependency_graph)
        if not cyclic_registers:
            return

        # 4. 分析复位情况：哪些寄存器在复位分支中被赋值（针对所有寄存器）
        reset_registers = self._analyze_reset_logic(always_node, all_registers)

        # 5. 如果存在循环依赖，检查该always块中所有没有复位的寄存器
        #（包括虽然没有循环依赖但通过控制流参与该过程的寄存器）
        registers_to_check = all_registers

        cycle_list = CycleList(always_name, always_node, registers_to_check)

        # 6. 构建周期表（5个周期），看未复位状态是否会传递
        self._build_cycles(cycle_list, registers_to_check, reset_registers, dependency_graph)

        self.always_cycle[always_name] = cycle_list

    def _identify_all_registers(self, always_node: Always) -> Set[Symbol]:
        """识别所有物理寄存器（非阻塞赋值的左值变量）"""
        registers = set()

        if not hasattr(always_node, 'statement') or not always_node.statement:
            return registers

        self._collect_registers_recursive(always_node.statement, registers)
        return registers

    def _collect_registers_recursive(self, stmt, registers: Set[Symbol]):
        """递归收集寄存器"""
        if stmt is None:
            return

        if isinstance(stmt, NonblockingSubstitution):
            # 非阻塞赋值的左值是寄存器
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var

            symbol = None
            if isinstance(lval, Identifier):
                symbol = self._lookup_symbol(lval.name)
            elif isinstance(lval, Partselect) and hasattr(lval, 'var'):
                symbol = self._lookup_symbol(str(lval.var))

            if symbol:
                registers.add(symbol)

        elif isinstance(stmt, Block):
            for s in stmt.statements:
                self._collect_registers_recursive(s, registers)

        elif isinstance(stmt, IfStatement):
            self._collect_registers_recursive(stmt.true_statement, registers)
            self._collect_registers_recursive(stmt.false_statement, registers)

        elif isinstance(stmt, CaseStatement):
            for case in stmt.caselist:
                if case.statement:
                    self._collect_registers_recursive(case.statement, registers)

    def _build_dependency_graph(self, always_node: Always, registers: Set[Symbol]) -> Dict[Symbol, Set[Symbol]]:
        """构建寄存器间的依赖图"""
        graph = {reg: set() for reg in registers}

        if not hasattr(always_node, 'statement') or not always_node.statement:
            return graph

        self._build_deps_recursive(always_node.statement, registers, graph)
        return graph

    def _build_deps_recursive(self, stmt, registers: Set[Symbol], graph: Dict[Symbol, Set[Symbol]]):
        """递归构建依赖关系"""
        if stmt is None:
            return

        if isinstance(stmt, NonblockingSubstitution):
            # 分析这个赋值的依赖
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var

            lsymbol = None
            if isinstance(lval, Identifier):
                lsymbol = self._lookup_symbol(lval.name)
            elif isinstance(lval, Partselect) and hasattr(lval, 'var'):
                lsymbol = self._lookup_symbol(str(lval.var))

            if lsymbol and lsymbol in registers:
                # 收集右值中的寄存器依赖
                rval = stmt.right
                if isinstance(rval, Rvalue):
                    rval = rval.var
                deps = self._extract_deps_from_expr(rval, registers)
                graph[lsymbol].update(deps)

        elif isinstance(stmt, Block):
            for s in stmt.statements:
                self._build_deps_recursive(s, registers, graph)

        elif isinstance(stmt, IfStatement):
            self._build_deps_recursive(stmt.true_statement, registers, graph)
            self._build_deps_recursive(stmt.false_statement, registers, graph)

        elif isinstance(stmt, CaseStatement):
            for case in stmt.caselist:
                if case.statement:
                    self._build_deps_recursive(case.statement, registers, graph)

    def _extract_deps_from_expr(self, expr, registers: Set[Symbol]) -> Set[Symbol]:
        """从表达式中提取寄存器依赖"""
        deps = set()

        if expr is None:
            return deps

        if isinstance(expr, Identifier):
            symbol = self._lookup_symbol(expr.name)
            if symbol and symbol in registers:
                deps.add(symbol)

        elif isinstance(expr, (Pointer, Partselect)):
            if isinstance(expr.var, (Identifier, Variable)):
                symbol = self._lookup_symbol(expr.var.name)
                if symbol and symbol in registers:
                    deps.add(symbol)
            # 递归检查索引表达式
            if hasattr(expr, 'ptr'):
                deps.update(self._extract_deps_from_expr(expr.ptr, registers))
            if hasattr(expr, 'msb'):
                deps.update(self._extract_deps_from_expr(expr.msb, registers))
            if hasattr(expr, 'lsb'):
                deps.update(self._extract_deps_from_expr(expr.lsb, registers))

        elif isinstance(expr, (UnaryOperator, Uplus, Uminus, Ulnot, Unot)):
            deps.update(self._extract_deps_from_expr(expr.right, registers))

        elif isinstance(expr, Cond):
            deps.update(self._extract_deps_from_expr(expr.cond, registers))
            deps.update(self._extract_deps_from_expr(expr.true_value, registers))
            deps.update(self._extract_deps_from_expr(expr.false_value, registers))

        elif isinstance(expr, Operator):
            deps.update(self._extract_deps_from_expr(expr.left, registers))
            deps.update(self._extract_deps_from_expr(expr.right, registers))

        elif isinstance(expr, Cond):
            deps.update(self._extract_deps_from_expr(expr.cond, registers))
            deps.update(self._extract_deps_from_expr(expr.true_value, registers))
            deps.update(self._extract_deps_from_expr(expr.false_value, registers))

        elif isinstance(expr, Concat):
            if hasattr(expr, 'list'):
                for item in expr.list:
                    deps.update(self._extract_deps_from_expr(item, registers))

        return deps

    def _find_cyclic_registers(self, registers: Set[Symbol], graph: Dict[Symbol, Set[Symbol]]) -> Set[Symbol]:
        """使用 DFS 检测循环依赖，返回所有在环中的寄存器（排除自环）"""
        cyclic = set()
        visited = set()
        rec_stack = set()

        def dfs(node, path):
            """path 记录当前 DFS 路径"""
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                # 排除自环（A -> A）
                if neighbor == node:
                    continue

                if neighbor not in visited:
                    if dfs(neighbor, path):
                        cyclic.add(node)
                        return True
                elif neighbor in rec_stack and neighbor in path:
                    # 发现环，且环长度 >= 2
                    # 找到环中所有节点
                    try:
                        cycle_start_idx = path.index(neighbor)
                        cycle_nodes = path[cycle_start_idx:]
                        if len(cycle_nodes) >= 2:
                            cyclic.update(cycle_nodes)
                            return True
                    except ValueError:
                        # neighbor not in path, might be from a different DFS branch
                        pass

            path.pop()
            rec_stack.remove(node)
            return False

        for reg in registers:
            if reg not in visited:
                dfs(reg, [])

        return cyclic

    def _analyze_reset_logic(self, always_node: Always, registers: Set[Symbol]) -> Set[Symbol]:
        """分析复位逻辑，返回在复位分支中被赋值的寄存器"""
        reset_registers = set()

        if not hasattr(always_node, 'statement') or not always_node.statement:
            return reset_registers

        # 检测复位信号
        reset_signals = self._detect_reset_signals(always_node)

        # 收集复位分支中的赋值
        self._collect_reset_assignments(always_node.statement, reset_signals, reset_registers, False)

        return reset_registers

    def _detect_reset_signals(self, always_node: Always) -> Set[str]:
        """检测复位信号名称"""
        reset_sigs = set()

        def visit(node):
            if isinstance(node, IfStatement):
                cond_str = self._cond_to_str(node.cond).lower()
                if 'reset' in cond_str or 'rst' in cond_str:
                    sig = self._extract_reset_signal(node.cond)
                    if sig:
                        reset_sigs.add(sig.lower())

            for attr in node.__dict__.values():
                if isinstance(attr, (list, tuple)):
                    for item in attr:
                        if item and hasattr(item, '__dict__'):
                            visit(item)
                elif attr and hasattr(attr, '__dict__'):
                    visit(attr)

        if always_node and hasattr(always_node, 'statement'):
            visit(always_node)
        return reset_sigs

    def _extract_reset_signal(self, cond) -> Optional[str]:
        """从条件表达式中提取复位信号名"""
        if isinstance(cond, Identifier):
            name = cond.name.lower()
            if 'reset' in name or 'rst' in name:
                return cond.name
            return None

        if hasattr(cond, 'left') and hasattr(cond, 'right'):
            left = self._extract_reset_signal(cond.left)
            if left:
                return left
            right = self._extract_reset_signal(cond.right)
            if right:
                return right

        if hasattr(cond, 'var'):
            return self._extract_reset_signal(cond.var)

        return None

    def _collect_reset_assignments(self, stmt, reset_signals: Set[str], assigned: Set[Symbol], in_reset: bool):
        """递归收集复位分支中的赋值"""
        if stmt is None:
            return

        if isinstance(stmt, Block):
            for s in stmt.statements:
                self._collect_reset_assignments(s, reset_signals, assigned, in_reset)

        elif isinstance(stmt, IfStatement):
            cond_str = self._cond_to_str(stmt.cond).lower()
            is_reset_branch = any(rs in cond_str for rs in reset_signals)

            # True分支
            if stmt.true_statement:
                self._collect_reset_assignments(stmt.true_statement, reset_signals, assigned,
                                               in_reset or is_reset_branch)

            # False分支
            if stmt.false_statement:
                self._collect_reset_assignments(stmt.false_statement, reset_signals, assigned,
                                               in_reset and not is_reset_branch)

        elif isinstance(stmt, NonblockingSubstitution):
            if in_reset:
                lval = stmt.left
                if isinstance(lval, Lvalue):
                    lval = lval.var

                symbol = None
                if isinstance(lval, Identifier):
                    symbol = self._lookup_symbol(lval.name)
                elif isinstance(lval, Partselect) and hasattr(lval, 'var'):
                    symbol = self._lookup_symbol(str(lval.var))

                if symbol:
                    assigned.add(symbol)

    def _build_cycles(self, cycle_list: CycleList, cyclic_regs: Set[Symbol],
                      reset_regs: Set[Symbol], dependency_graph: Dict[Symbol, Set[Symbol]]):
        """构建周期表（5个周期）"""
        # Cycle 0: 复位状态
        cycle_0 = Cycle()
        for reg in cyclic_regs:
            if reg in reset_regs:
                cycle_0.symbol_state[reg] = 1  # 已复位
            else:
                cycle_0.symbol_state[reg] = -1  # 未复位
        cycle_list.add_cycle(cycle_0)

        # Cycle 1-4: 模拟运行时状态
        for i in range(1, 5):
            prev_cycle = cycle_list.get_cycle(i - 1)
            cur_cycle = Cycle()

            for reg in cyclic_regs:
                if reg in reset_regs:
                    # 有复位的寄存器保持已定义
                    cur_cycle.symbol_state[reg] = 1
                else:
                    # 检查依赖是否都是已定义的
                    deps = dependency_graph.get(reg, set())
                    deps_defined = all(
                        prev_cycle.symbol_state.get(dep, -1) == 1
                        for dep in deps if dep in cyclic_regs
                    )

                    if prev_cycle.symbol_state.get(reg, -1) == 1:
                        # 上一周期已定义
                        cur_cycle.symbol_state[reg] = 1
                    # elif deps_defined and len(deps) > 0:
                    #     # 依赖都是已定义的，但自己没有复位
                    #     # 这种情况下状态可能仍然未定义（因为没有初始值）
                    #     cur_cycle.symbol_state[reg] = -1
                    else:
                        # 有依赖未定义
                        cur_cycle.symbol_state[reg] = -1

            cycle_list.add_cycle(cur_cycle)

    def _cond_to_str(self, cond) -> str:
        """将条件转换为字符串"""
        if isinstance(cond, Identifier):
            return cond.name
        return str(cond)

    def _lookup_symbol(self, name: str) -> Optional[Symbol]:
        """查找符号"""
        return self.dfg_builder.stb.lookup(name, self.dfg_builder.stb.root_scope)

    def get_cycle_list(self, always_name: str) -> Optional[CycleList]:
        """获取指定 always 块的周期列表"""
        return self.always_cycle.get(always_name)

    def get_all_cycle_lists(self) -> Dict[str, CycleList]:
        """获取所有周期列表"""
        return self.always_cycle
