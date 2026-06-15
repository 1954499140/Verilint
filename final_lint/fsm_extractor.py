from typing import Optional, Dict, List, Any, Set, Tuple
from enum import Enum
from dataclasses import dataclass, field
from pyverilog.vparser.ast import *
from symbol import Symbol
from dfg_builder import DataflowGraph, DFGBuilder, AlwaysBlockInfo


class StateEncoding(Enum):
    """状态编码方式"""
    BINARY = "binary"           # 二进制编码
    ONE_HOT = "one_hot"         # 独热码
    GRAY = "gray"               # 格雷码
    ONE_COLD = "one_cold"       # 独冷码
    UNKNOWN = "unknown"         # 未知


@dataclass
class State:
    """
    FSM状态
    """
    name: str                           # 状态名
    value: Any                          # 状态值（可以是整数或位向量）
    encoding: StateEncoding = StateEncoding.UNKNOWN
    is_initial: bool = False            # 是否是初始状态
    lineno: int = 0                     # 定义行号

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, State):
            return self.name == other.name
        return False


@dataclass
class StateTransition:
    """
    状态转换
    """
    from_state: State                   # 源状态
    to_state: State                     # 目标状态
    condition: str                      # 转换条件（字符串表示）
    condition_expr: Any = None          # 条件表达式（AST节点）
    lineno: int = 0                     # 行号
    is_default: bool = False            # 是否是默认转换

    def __repr__(self) -> str:
        return f"{self.from_state.name} --[{self.condition}]--> {self.to_state.name}"


@dataclass
class FSM:
    """
    有限状态机
    """
    name: str                           # FSM名称（通常基于always块位置）
    state_variable: str                 # 状态变量名
    states: Dict[str, State] = field(default_factory=dict)
    transitions: List[StateTransition] = field(default_factory=list)

    # 状态机属性
    encoding: StateEncoding = StateEncoding.UNKNOWN
    is_mealy: bool = False              # 是否是Mealy机（输出与输入和状态有关）
    is_moore: bool = False              # 是否是Moore机（输出仅与状态有关）

    # 时钟和复位
    clock_signal: Optional[str] = None
    reset_signal: Optional[str] = None

    # 所属的always块信息
    always_lineno: int = 0
    sensitivity_list: List[str] = field(default_factory=list)

    def add_state(self, state: State):
        """添加状态"""
        self.states[state.name] = state

    def add_transition(self, transition: StateTransition):
        """添加状态转换"""
        self.transitions.append(transition)

    def get_initial_state(self) -> Optional[State]:
        """获取初始状态"""
        for state in self.states.values():
            if state.is_initial:
                return state
        return None

    def get_transitions_from(self, state_name: str) -> List[StateTransition]:
        """获取从指定状态出发的所有转换"""
        return [t for t in self.transitions if t.from_state.name == state_name]

    def get_transitions_to(self, state_name: str) -> List[StateTransition]:
        """获取到达指定状态的所有转换"""
        return [t for t in self.transitions if t.to_state.name == state_name]

    def detect_encoding(self):
        """检测状态编码方式"""
        if not self.states:
            return

        state_values = []
        for state in self.states.values():
            if isinstance(state.value, int):
                state_values.append(state.value)

        if not state_values:
            return

        # 检查独热码
        one_hot_count = sum(1 for v in state_values if bin(v).count('1') == 1)
        if one_hot_count == len(state_values):
            self.encoding = StateEncoding.ONE_HOT
            return

        # 检查独冷码
        one_cold_count = sum(1 for v in state_values if bin(~v & 0xFF).count('1') == 1)
        if one_cold_count == len(state_values):
            self.encoding = StateEncoding.ONE_COLD
            return

        # 检查格雷码
        is_gray = True
        sorted_values = sorted(state_values)
        for i in range(len(sorted_values) - 1):
            xor = sorted_values[i] ^ sorted_values[i + 1]
            if bin(xor).count('1') != 1:
                is_gray = False
                break
        if is_gray:
            self.encoding = StateEncoding.GRAY
            return

        # 默认为二进制编码
        self.encoding = StateEncoding.BINARY

    def print_fsm(self):
        """打印FSM信息"""
        print(f"\n{'='*60}")
        print(f"FSM: {self.name}")
        print(f"{'='*60}")
        print(f"State Variable: {self.state_variable}")
        print(f"Clock: {self.clock_signal}")
        print(f"Reset: {self.reset_signal}")
        print(f"Encoding: {self.encoding.value}")
        print(f"Line: {self.always_lineno}")

        print(f"\nStates ({len(self.states)}):")
        for state in self.states.values():
            initial_mark = " (initial)" if state.is_initial else ""
            print(f"  {state.name} = {state.value}{initial_mark}")

        print(f"\nTransitions ({len(self.transitions)}):")
        for trans in self.transitions:
            print(f"  {trans}")


class FSMExtractor:
    """
    FSM提取器 - 基于代码结构分析识别状态机
    """

    def __init__(self, dfg_builder: DFGBuilder):
        self.dfg_builder = dfg_builder
        self.fsms: List[FSM] = []
        self.current_fsm: Optional[FSM] = None

    def extract(self) -> List[FSM]:
        """
        从所有模块中提取FSM
        基于代码结构分析，而非字符串匹配
        """
        self.fsms = []

        for module_name, dfg in self.dfg_builder.dfgs.items():
            self._extract_from_module(module_name, dfg)

        return self.fsms

    def _extract_from_module(self, module_name: str, dfg: DataflowGraph):
        """从单个模块中提取FSM"""
        # 1. 先收集所有子图（时序和组合）
        seq_subgraphs = []
        comb_subgraphs = []

        for subgraph_name, subgraph in dfg.subgraphs.items():
            if subgraph.is_sequential:
                seq_subgraphs.append((subgraph_name, subgraph))
            elif subgraph.is_combinational:
                comb_subgraphs.append((subgraph_name, subgraph))

        # 2. 对每个时序子图，尝试提取FSM
        for subgraph_name, seq_subgraph in seq_subgraphs:
            fsm = self._extract_fsm_from_always(subgraph_name, seq_subgraph, comb_subgraphs)
            if fsm:
                self.fsms.append(fsm)

    def _extract_fsm_from_always(self, subgraph_name: str, seq_subgraph: DataflowGraph,
                                   comb_subgraphs: List[Tuple[str, DataflowGraph]]) -> Optional[FSM]:
        """
        从always块中提取FSM
        基于代码结构分析：
        1. 检查时序逻辑块（posedge/negedge 敏感列表）
        2. 识别状态寄存器（非阻塞赋值的左值）
        3. 查找复位分支中的初始状态赋值
        4. 在组合逻辑中查找 case(state) 状态跳转
        """
        # 1. 检查是否为有效的时序逻辑块
        if not self._is_valid_sequential_block(seq_subgraph):
            return None

        # 2. 识别状态变量（基于代码结构而非字符串匹配）
        state_var_info = self._identify_state_variable_by_structure(seq_subgraph, comb_subgraphs)
        if not state_var_info:
            return None

        state_var, next_state_var, initial_state = state_var_info
        print(f"[DEBUG] Found state variable: {state_var}, next_state: {next_state_var}, initial: {initial_state}")

        # 3. 创建FSM对象
        fsm = FSM(
            name=subgraph_name,
            state_variable=state_var,
            clock_signal=seq_subgraph.clock_signal,
            reset_signal=seq_subgraph.reset_signal,
            always_lineno=seq_subgraph.name.split('_')[-2] if '_always_' in seq_subgraph.name else 0,
            sensitivity_list=seq_subgraph.sensitivity_list
        )
        self.current_fsm = fsm

        # 4. 提取状态定义
        states_found = self._extract_states_by_structure(fsm, state_var, next_state_var, comb_subgraphs)
        if not states_found:
            self.current_fsm = None
            return None

        print(f"[DEBUG] States extracted: {list(fsm.states.keys())}")

        # 5. 设置初始状态
        if initial_state and initial_state in fsm.states:
            fsm.states[initial_state].is_initial = True
        elif fsm.states:
            # 如果没有明确的初始状态，选择值最小的作为初始状态
            sorted_states = sorted(fsm.states.values(), key=lambda s: s.value if isinstance(s.value, int) else 0)
            sorted_states[0].is_initial = True

        # 6. 提取状态转换
        self._extract_transitions_by_structure(fsm, state_var, next_state_var, comb_subgraphs)
        print(f"[DEBUG] Transitions extracted: {len(fsm.transitions)}")

        # 7. 检测编码方式
        fsm.detect_encoding()

        self.current_fsm = None

        # 只返回有效FSM（至少有两个状态和一条转换）
        if len(fsm.states) >= 2 and fsm.transitions:
            return fsm
        return None

    def _is_valid_sequential_block(self, subgraph: DataflowGraph) -> bool:
        """
        检查是否为有效的时序逻辑块
        - 敏感列表包含 posedge/negedge 时钟信号
        """
        if not subgraph.is_sequential:
            return False

        # 检查时序信号
        has_clock = False
        for sig in subgraph.sensitivity_list:
            if 'posedge' in sig or 'negedge' in sig:
                has_clock = True
                break

        return has_clock

    def _identify_state_variable_by_structure(self, seq_subgraph: DataflowGraph,
                                               comb_subgraphs: List[Tuple[str, DataflowGraph]]) -> Optional[Tuple[str, str, str]]:
        """
        基于代码结构识别状态变量
        返回: (state_var, next_state_var, initial_state) 或 None
        """
        candidates = []

        # 遍历时序子图中的所有符号定义
        for symbol_name, defs in seq_subgraph.symbol_defs.items():
            # 跳过输出端口
            if any(d.symbol.is_output() for d in defs if hasattr(d, 'symbol')):
                continue

            score = 0
            next_state_var = None
            initial_state = None

            # 收集所有非阻塞赋值
            nonblocking_defs = [d for d in defs if d.def_type == 'nonblocking']
            if not nonblocking_defs:
                continue

            # 检查赋值模式
            assigned_values = set()
            for d in nonblocking_defs:
                if hasattr(d, 'ast_node') and d.ast_node:
                    value = self._get_assigned_value(d.ast_node)
                    if value:
                        assigned_values.add(value)

            # 检查是否有初始状态赋值（复位分支）
            for d in nonblocking_defs:
                if self._is_in_reset_branch(d):
                    initial_value = self._get_assigned_value(d.ast_node)
                    if initial_value:
                        initial_state = initial_value
                        score += 5

            # 检查是否在组合逻辑中有对应的 case 语句
            has_case_transition = False
            for comb_name, comb_subgraph in comb_subgraphs:
                case_info = self._find_case_on_variable(comb_subgraph, symbol_name)
                if case_info:
                    has_case_transition = True
                    next_state_var = case_info.get('next_state_var')
                    score += 10

            # 检查赋值给该变量的表达式类型
            for d in nonblocking_defs:
                if hasattr(d, 'ast_node') and d.ast_node:
                    # 检查是否是其他变量赋值给它
                    src = self._get_assignment_source(d.ast_node)
                    if src and src != symbol_name:
                        # 检查源变量是否在组合逻辑中定义
                        for comb_name, comb_subgraph in comb_subgraphs:
                            if src in comb_subgraph.symbol_defs:
                                next_state_var = src
                                score += 3
                                break

            if score > 0:
                candidates.append((symbol_name, next_state_var, initial_state, score))

        # 返回得分最高的候选
        if candidates:
            candidates.sort(key=lambda x: x[3], reverse=True)
            best = candidates[0]
            return (best[0], best[1], best[2])

        return None

    def _get_assigned_value(self, ast_node) -> Optional[str]:
        """从赋值节点获取赋值的值"""
        if hasattr(ast_node, 'right'):
            right = ast_node.right
            if isinstance(right, Identifier):
                return right.name
            elif isinstance(right, IntConst):
                return right.value
        return None

    def _get_assignment_source(self, ast_node) -> Optional[str]:
        """获取赋值的源变量名"""
        if hasattr(ast_node, 'right'):
            right = ast_node.right
            if isinstance(right, Identifier):
                return right.name
        return None

    def _is_in_reset_branch(self, def_node) -> bool:
        """检查赋值是否在复位分支中"""
        # 通过遍历父节点检查是否在 if (reset) 分支中
        # 这需要访问原始AST，暂时简化处理
        # 检查行号附近是否有复位相关代码
        if hasattr(def_node, 'lineno') and def_node.lineno:
            lineno = def_node.lineno
            # 这里可以通过符号表或AST进一步检查
            # 简化：假设第一处赋值通常是复位赋值
            return True  # 简化处理，后续可以完善
        return False

    def _find_case_on_variable(self, subgraph: DataflowGraph, var_name: str) -> Optional[Dict]:
        """
        查找对指定变量的 case 语句
        返回 case 语句信息
        """
        # 检查符号使用
        if var_name not in subgraph.symbol_uses:
            return None

        uses = subgraph.symbol_uses[var_name]
        for use in uses:
            if use.use_type == 'condition':
                # 该变量作为条件使用，可能是 case/casex/casez
                # 查找相邻的赋值
                next_state_var = None
                for def_name in subgraph.symbol_defs.keys():
                    # 查找在组合逻辑中定义的下一状态变量
                    defs = subgraph.symbol_defs[def_name]
                    blocking_defs = [d for d in defs if d.def_type == 'blocking']
                    if blocking_defs:
                        next_state_var = def_name
                        break

                return {
                    'use': use,
                    'next_state_var': next_state_var
                }

        return None

    def _extract_states_by_structure(self, fsm: FSM, state_var: str,
                                      next_state_var: str,
                                      comb_subgraphs: List[Tuple[str, DataflowGraph]]) -> bool:
        """
        从 case 语句中提取状态定义
        返回是否成功提取到状态
        """
        states_found = False

        # 遍历组合逻辑子图
        for comb_name, comb_subgraph in comb_subgraphs:
            # 查找 case 语句
            case_items = self._find_case_items_for_variable(comb_subgraph, state_var)

            for case_item in case_items:
                # case_item 是 case 分支
                state_value = self._extract_state_from_case_item(case_item)
                if state_value:
                    state_name = f"STATE_{state_value}"
                    if isinstance(state_value, str) and not state_value.isdigit():
                        state_name = state_value

                    state = State(
                        name=state_name,
                        value=state_value,
                        lineno=getattr(case_item, 'lineno', 0)
                    )
                    fsm.add_state(state)
                    states_found = True

        return states_found

    def _find_case_items_for_variable(self, subgraph: DataflowGraph, var_name: str) -> List[Any]:
        """查找对变量进行 case 判断的所有 case 分支"""
        case_items = []

        # 从子图的AST节点中查找
        if not hasattr(subgraph, 'ast_node') or not subgraph.ast_node:
            return case_items

        def find_case_in_node(node, target_var):
            if node is None:
                return

            if isinstance(node, CaseStatement):
                # 检查 case 的条件是否是我们的目标变量
                if hasattr(node, 'comp'):
                    comp = node.comp
                    if isinstance(comp, Identifier) and comp.name == target_var:
                        # 找到匹配的 case，收集所有分支
                        if hasattr(node, 'caselist'):
                            for case_item in node.caselist:
                                case_items.append(case_item)

            # 递归遍历子节点
            for attr_name in ['statement', 'true_statement', 'false_statement', 'statements']:
                if hasattr(node, attr_name):
                    attr_val = getattr(node, attr_name)
                    if isinstance(attr_val, list):
                        for item in attr_val:
                            find_case_in_node(item, target_var)
                    elif attr_val is not None:
                        find_case_in_node(attr_val, target_var)

        find_case_in_node(subgraph.ast_node, var_name)
        return case_items

    def _extract_state_from_case_item(self, case_item) -> Any:
        """从 case 分支中提取状态值"""
        if not hasattr(case_item, 'cond'):
            return None

        cond = case_item.cond
        if isinstance(cond, IntConst):
            # 直接是整数常量
            return self._parse_int_const(cond)
        elif isinstance(cond, Identifier):
            # 是参数或常量名
            return cond.name
        elif isinstance(cond, list):
            # 多个条件，取第一个
            if cond:
                first = cond[0]
                if isinstance(first, IntConst):
                    return self._parse_int_const(first)
                elif isinstance(first, Identifier):
                    return first.name

        return None

    def _extract_transitions_by_structure(self, fsm: FSM, state_var: str,
                                          next_state_var: str,
                                          comb_subgraphs: List[Tuple[str, DataflowGraph]]):
        """
        从 case 语句中提取状态转换
        """
        for comb_name, comb_subgraph in comb_subgraphs:
            self._extract_transitions_from_subgraph(fsm, comb_subgraph, state_var, next_state_var)

    def _extract_transitions_from_subgraph(self, fsm: FSM, subgraph: DataflowGraph,
                                           state_var: str, next_state_var: str):
        """从子图中提取状态转换"""
        if not hasattr(subgraph, 'ast_node') or not subgraph.ast_node:
            return

        def extract_from_node(node, current_state=None, condition=""):
            if node is None:
                return

            if isinstance(node, CaseStatement):
                # 检查是否是状态 case
                if hasattr(node, 'comp') and isinstance(node.comp, Identifier):
                    if node.comp.name == state_var:
                        # 处理每个 case 分支
                        if hasattr(node, 'caselist'):
                            for case_item in node.caselist:
                                from_state = self._extract_state_from_case_item(case_item)
                                if from_state:
                                    state_name = f"STATE_{from_state}" if isinstance(from_state, int) else str(from_state)
                                    if state_name in fsm.states:
                                        # 查找该分支中的下一状态赋值
                                        if hasattr(case_item, 'statement'):
                                            extract_from_node(case_item.statement, state_name, condition)

            elif isinstance(node, IfStatement):
                # 处理 if 语句中的条件
                if hasattr(node, 'cond'):
                    cond_str = self._expr_to_str(node.cond)
                    if condition:
                        cond_str = f"{condition} && {cond_str}"

                    if node.true_statement:
                        extract_from_node(node.true_statement, current_state, cond_str)
                    if node.false_statement:
                        extract_from_node(node.false_statement, current_state, condition)

            elif isinstance(node, BlockingSubstitution) or isinstance(node, NonblockingSubstitution):
                # 检查是否是下一状态赋值
                if hasattr(node, 'left') and isinstance(node.left, Identifier):
                    if node.left.name == next_state_var or node.left.name == state_var:
                        # 找到状态赋值
                        to_state = self._get_assigned_value(node)
                        if to_state and current_state:
                            to_state_name = f"STATE_{to_state}" if isinstance(to_state, int) else str(to_state)
                            if to_state_name in fsm.states:
                                transition = StateTransition(
                                    from_state=fsm.states[current_state],
                                    to_state=fsm.states[to_state_name],
                                    condition=condition,
                                    condition_expr=node,
                                    lineno=getattr(node, 'lineno', 0)
                                )
                                fsm.add_transition(transition)

            elif hasattr(node, 'statement'):
                extract_from_node(node.statement, current_state, condition)

            elif hasattr(node, 'statements'):
                for stmt in node.statements:
                    extract_from_node(stmt, current_state, condition)

        extract_from_node(subgraph.ast_node)

    def _parse_int_const(self, int_const: IntConst) -> Optional[int]:
        """解析整数常量"""
        try:
            value = int_const.value
            if value.startswith("'b") or value.startswith("'B"):
                return int(value[2:], 2)
            elif value.startswith("'h") or value.startswith("'H"):
                return int(value[2:], 16)
            elif value.startswith("'d") or value.startswith("'D"):
                return int(value[2:])
            elif value.startswith("'o") or value.startswith("'O"):
                return int(value[2:], 8)
            else:
                return int(value)
        except:
            return None

    def _expr_to_str(self, expr) -> str:
        """将表达式转换为字符串"""
        if expr is None:
            return ""
        if isinstance(expr, Identifier):
            return expr.name
        elif isinstance(expr, IntConst):
            return expr.value
        elif hasattr(expr, 'left') and hasattr(expr, 'right'):
            op = self._get_operator_str(expr)
            left = self._expr_to_str(expr.left)
            right = self._expr_to_str(expr.right)
            return f"{left} {op} {right}"
        return str(expr)

    def _get_operator_str(self, expr) -> str:
        """获取操作符字符串"""
        from pyverilog.vparser.ast import (Eq, NotEq, LessThan, GreaterThan,
                                           LessEq, GreaterEq, Plus, Minus,
                                           Times, Divide, Mod, Power,
                                           And, Or, Xor, Land, Lor)
        if isinstance(expr, Eq):
            return "=="
        elif isinstance(expr, NotEq):
            return "!="
        elif isinstance(expr, LessThan):
            return "<"
        elif isinstance(expr, GreaterThan):
            return ">"
        elif isinstance(expr, LessEq):
            return "<="
        elif isinstance(expr, GreaterEq):
            return ">="
        elif isinstance(expr, Plus):
            return "+"
        elif isinstance(expr, Minus):
            return "-"
        elif isinstance(expr, Times):
            return "*"
        elif isinstance(expr, Divide):
            return "/"
        elif isinstance(expr, Mod):
            return "%"
        elif isinstance(expr, Power):
            return "**"
        elif isinstance(expr, And):
            return "&"
        elif isinstance(expr, Or):
            return "|"
        elif isinstance(expr, Xor):
            return "^"
        elif isinstance(expr, Land):
            return "&&"
        elif isinstance(expr, Lor):
            return "||"
        return "?"

    def get_fsms(self) -> List[FSM]:
        """获取所有提取的FSM"""
        return self.fsms

    def get_fsm_by_name(self, name: str) -> Optional[FSM]:
        """通过名称获取FSM"""
        for fsm in self.fsms:
            if fsm.name == name:
                return fsm
        return None

    def print_all_fsms(self):
        """打印所有FSM"""
        if not self.fsms:
            print("No FSMs found")
            return

        for fsm in self.fsms:
            fsm.print_fsm()
