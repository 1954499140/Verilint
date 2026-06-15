from enum import Enum, auto
from typing import Optional, Any, List, Union, Set
from dataclasses import dataclass, field

# 延迟导入 Pyverilog AST 类型，避免循环导入
def _get_ast_types():
    """获取 Pyverilog AST 类型"""
    try:
        from pyverilog.vparser.ast import (
            IntConst, Identifier, Operator, Plus, Minus,
            Times, Divide, Mod, Power
        )
        return {
            'IntConst': IntConst,
            'Identifier': Identifier,
            'Operator': Operator,
            'Plus': Plus,
            'Minus': Minus,
            'Times': Times,
            'Divide': Divide,
            'Mod': Mod,
            'Power': Power,
        }
    except ImportError:
        return {}


@dataclass
class DefSite:
    """
    定义点 (Definition Site)
    记录变量在哪里被定义/赋值
    """
    symbol: 'Symbol'                    # 被定义的符号
    lineno: int                         # 定义所在行号
    stmt: Any                           # 定义语句 (AST节点)
    def_type: str = ""                  # 定义类型: 'assign', 'blocking', 'nonblocking', 'initial', 'port'
    rhs_symbols: List['Symbol'] = field(default_factory=list)  # 右侧使用的符号（数据依赖）
    basic_block_id: Optional[int] = None  # 所属基本块ID

    def __repr__(self) -> str:
        return f"DefSite({self.symbol.name}, line={self.lineno}, type={self.def_type})"


@dataclass
class UseSite:
    """
    使用点 (Use Site)
    记录变量在哪里被使用
    """
    symbol: 'Symbol'                    # 被使用的符号
    lineno: int                         # 使用所在行号
    stmt: Any                           # 使用语句 (AST节点)
    use_type: str = ""                  # 使用类型: 'rhs', 'condition', 'index', 'instance_port'
    basic_block_id: Optional[int] = None  # 所属基本块ID

    def __repr__(self) -> str:
        return f"UseSite({self.symbol.name}, line={self.lineno}, type={self.use_type})"


@dataclass
class DataflowEdge:
    """
    数据流边
    连接定义点和使用点，表示数据依赖关系
    """
    source_def: DefSite                 # 源定义点
    target_use: UseSite                 # 目标使用点
    is_control_dependent: bool = False  # 是否是控制依赖

    def __repr__(self) -> str:
        return f"DataflowEdge({self.source_def.symbol.name} -> {self.target_use.symbol.name})"


class SymbolType(Enum):
    """符号类型枚举"""
    INPUT = "input"           # 输入端口
    OUTPUT = "output"         # 输出端口
    INOUT = "inout"           # 双向端口
    WIRE = "wire"             # 线网类型
    REG = "reg"               # 寄存器类型（阻塞赋值时标记）
    PARAMETER = "parameter"   # 参数
    LOCALPARAM = "localparam" # 本地参数
    INSTANCE = "instance"     # 模块实例
    IDENTIFIER = "identifier" # 一般标识符
    INTEGER = "integer"       # 整数类型
    REAL = "real"             # 实数类型
    TIME = "time"             # 时间类型
    FUNCTION = "function"     # 函数
    TASK = "task"             # 任务
    GENVAR = "genvar"         # generate变量
    ARRAY = "array"           # 数组
    TEMP = "temp"             # 临时变量（TAC用）
    LABEL = "label"           # 标签（TAC用）


class TACInfo:
    """
    三地址码相关信息
    用于符号在三地址码生成和优化中的管理
    """

    def __init__(self):
        # 常量传播
        self.const_value: Any = None                # 常量值
        self.is_compile_time_const: bool = False    # 是否是编译时常量

        # 临时变量编号（用于自动生成）
        self.temp_id: int = -1                      # 临时变量ID

        # 活跃性分析（简化版）
        self.use_chain: List[int] = []              # 使用位置链

    def add_use(self, position: int):
        """添加使用位置"""
        self.use_chain.append(position)

    def __repr__(self) -> str:
        return f"TACInfo(temp_id={self.temp_id}, const={self.const_value})"


class Symbol:
    """
    Verilog符号类
    用于表示Verilog代码中的各种符号（信号、端口、实例等）
    """

    def __init__(
        self,
        name: str,
        symbol_type: SymbolType,
        scope_level: int,
        lineno: int,
        node: Any = None
    ):
        # 基本属性
        self.name = name                    # 符号名称
        self.type = symbol_type             # 符号类型
        self.scope_level = scope_level      # 作用域层级
        self.lineno = lineno                # 定义行号
        self.node = node                    # 关联的AST节点

        # 位宽信息
        self.width_msb: Optional[int] = None   # 最高位
        self.width_lsb: Optional[int] = None   # 最低位
        self.width: int = 1                    # 位宽（默认1位）

        # 数组维度（用于数组类型）
        self.array_dimensions: List[tuple] = []  # [(msb, lsb), ...]
        self.is_array: bool = False

        # 初始值
        self.initial_value: Any = None
        self.has_initial: bool = False

        # 赋值相关标记
        self.is_blocking_assigned: bool = False   # 是否被阻塞赋值(=)
        self.is_nonblocking_assigned: bool = False # 是否被非阻塞赋值(<=)
        self.is_assigned: bool = False            # 是否被赋值
        self.is_used: bool = False                # 是否被使用

        # 端口特定属性
        self.port_direction: Optional[str] = None  # 端口方向（用于区分input/output/inout）
        self.is_signed: bool = False               # 是否有符号

        # 实例特定属性
        self.instance_module: Optional[str] = None  # 实例对应的模块名
        self.instance_params: dict = {}             # 实例参数

        # 参数特定属性
        self.param_value: Any = None                # 参数值
        self.is_param: bool = False                 # 是否是参数

        # 其他标记
        self.is_initialized: bool = False           # 是否已初始化
        self.is_constant: bool = False              # 是否是常量

        # 时钟和复位信号标记
        self.is_clock: bool = False                 # 是否是时钟信号
        self.is_async_reset: bool = False           # 是否是异步复位信号
        self.is_sync_reset: bool = False            # 是否是同步复位信号
        self.clock_source: Optional[str] = None     # 时钟源（用于衍生时钟）
        self.in_unknown_instance: bool = False      # 是否在未知/未定义的实例中

        # 三地址码(TAC)相关信息
        self.tac_info: Optional[TACInfo] = None     # TAC生成信息
        if symbol_type in (SymbolType.TEMP, SymbolType.LABEL):
            self.tac_info = TACInfo()

        # TAC指令关联
        self.def_tac: Optional[Any] = None          # 定义该符号的TAC指令
        self.use_tacs: List[Any] = []               # 使用该符号的TAC指令列表

        # 定义-使用链 (Def-Use Chain)
        self.def_sites: List['DefSite'] = []        # 所有定义点（支持多次赋值）
        self.use_sites: List['UseSite'] = []        # 所有使用点
        self.dataflow_edges: List['DataflowEdge'] = []  # 数据流边（从定义到使用）

        # 控制流相关
        self.is_basic_block_leader: bool = False    # 是否是基本块入口
        self.basic_block_id: Optional[int] = None   # 所属基本块ID

    def __repr__(self) -> str:
        return f"Symbol(name='{self.name}', type={self.type.value}, line={self.lineno})"

    def __str__(self) -> str:
        type_str = self.type.value
        width_str = f"[{self.width_msb}:{self.width_lsb}]" if self.width_msb is not None else ""
        return f"{type_str} {width_str} {self.name}"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Symbol):
            return False
        return self.name == other.name and self.scope_level == other.scope_level

    def __hash__(self) -> int:
        return hash((self.name, self.scope_level))

    # ============ 类型检查方法 ============

    def is_port(self) -> bool:
        """检查是否是端口类型"""
        return self.type in (SymbolType.INPUT, SymbolType.OUTPUT, SymbolType.INOUT)

    def is_input(self) -> bool:
        """检查是否是输入端口"""
        return self.type == SymbolType.INPUT

    def is_output(self) -> bool:
        """检查是否是输出端口"""
        return self.type == SymbolType.OUTPUT

    def is_reg_type(self) -> bool:
        """检查是否是reg类型"""
        return self.type == SymbolType.REG

    def is_wire_type(self) -> bool:
        """检查是否是wire类型"""
        return self.type == SymbolType.WIRE

    def is_instance_type(self) -> bool:
        """检查是否是实例类型"""
        return self.type == SymbolType.INSTANCE

    def is_param_type(self) -> bool:
        """检查是否是参数类型"""
        return self.type in (SymbolType.PARAMETER, SymbolType.LOCALPARAM)

    # ============ 位宽设置方法 ============

    def set_width(self, msb: Optional[int], lsb: Optional[int] = 0):
        """
        设置位宽
        支持具体的整数值，当 msb/lsb 为 None 时表示位宽未知或依赖于参数
        """
        self.width_msb = msb
        self.width_lsb = lsb

        # 只有当 msb 和 lsb 都是具体数值时才计算位宽
        if msb is not None and lsb is not None:
            self.width = abs(msb - lsb) + 1
        else:
            # 位宽未知，保持默认值1或根据上下文推断
            self.width = 1

    def set_width_expr(self, msb_expr: Any, lsb_expr: Any = None):
        """
        设置位宽表达式（用于位宽依赖于 parameter 的情况）
        如: [PARAM-1:0] 或 [WIDTH-1:0]

        msb_expr 和 lsb_expr 可以是:
        - IntConst: 整数常量
        - Identifier: 参数名（如 PARAM）
        - Operator: 表达式（如 PARAM-1）
        """
        # 存储原始表达式（用于后续解析）
        self._width_msb_expr = msb_expr
        self._width_lsb_expr = lsb_expr

        # 尝试计算具体的位宽值
        msb_val = self._eval_width_expr(msb_expr)
        lsb_val = self._eval_width_expr(lsb_expr) if lsb_expr is not None else 0

        self.width_msb = msb_val
        self.width_lsb = lsb_val

        if msb_val is not None and lsb_val is not None:
            self.width = abs(msb_val - lsb_val) + 1

    def _eval_width_expr(self, expr) -> Optional[int]:
        """
        尝试计算位宽表达式的值
        支持 IntConst、Identifier（parameter）、Operator
        """
        ast_types = _get_ast_types()
        IntConst = ast_types.get('IntConst')
        Identifier = ast_types.get('Identifier')
        Operator = ast_types.get('Operator')
        Plus = ast_types.get('Plus')
        Minus = ast_types.get('Minus')
        Times = ast_types.get('Times')
        Divide = ast_types.get('Divide')

        if expr is None:
            return 0

        # 整数常量
        if IntConst and isinstance(expr, IntConst):
            try:
                value = expr.value
                if isinstance(value, str):
                    # 处理不同进制
                    if value.startswith("'b") or value.startswith("'B"):
                        return int(value[2:], 2)
                    elif value.startswith("'o") or value.startswith("'O"):
                        return int(value[2:], 8)
                    elif value.startswith("'h") or value.startswith("'H"):
                        return int(value[2:], 16)
                    elif value.startswith("'d") or value.startswith("'D"):
                        return int(value[2:], 10)
                    else:
                        return int(value, 0)
                return int(value)
            except (ValueError, TypeError):
                return None

        # 参数引用 - 如果符号表中有这个参数且已计算常量值
        if Identifier and isinstance(expr, Identifier):
            # 这里暂时返回None，因为符号表可能还没完全构建
            # 后续可以通过符号链接来解析
            return None

        # 运算符表达式（简单处理）
        if Operator and isinstance(expr, Operator):
            left_val = self._eval_width_expr(expr.left) if hasattr(expr, 'left') else None
            right_val = self._eval_width_expr(expr.right) if hasattr(expr, 'right') else None

            if left_val is not None and right_val is not None:
                if Plus and isinstance(expr, Plus):
                    return left_val + right_val
                elif Minus and isinstance(expr, Minus):
                    return left_val - right_val
                elif Times and isinstance(expr, Times):
                    return left_val * right_val
                elif Divide and isinstance(expr, Divide) and right_val != 0:
                    return left_val // right_val

        return None

    def update_width_from_param(self, param_name: str, param_value: int):
        """
        当 parameter 的值确定后，更新依赖于该参数的位宽
        在符号表构建完成后调用
        """
        if hasattr(self, '_width_msb_expr') and self._width_msb_expr is not None:
            new_msb = self._eval_width_expr_with_param(self._width_msb_expr, param_name, param_value)
            if new_msb is not None:
                self.width_msb = new_msb

        if hasattr(self, '_width_lsb_expr') and self._width_lsb_expr is not None:
            new_lsb = self._eval_width_expr_with_param(self._width_lsb_expr, param_name, param_value)
            if new_lsb is not None:
                self.width_lsb = new_lsb

        # 重新计算位宽
        if self.width_msb is not None and self.width_lsb is not None:
            self.width = abs(self.width_msb - self.width_lsb) + 1

    def _eval_width_expr_with_param(self, expr, param_name: str, param_value: int) -> Optional[int]:
        """
        在已知参数值的情况下计算表达式
        """
        ast_types = _get_ast_types()
        IntConst = ast_types.get('IntConst')
        Identifier = ast_types.get('Identifier')
        Operator = ast_types.get('Operator')
        Plus = ast_types.get('Plus')
        Minus = ast_types.get('Minus')
        Times = ast_types.get('Times')
        Divide = ast_types.get('Divide')

        if expr is None:
            return 0

        if IntConst and isinstance(expr, IntConst):
            return self._eval_width_expr(expr)

        if Identifier and isinstance(expr, Identifier):
            if expr.name == param_name:
                return param_value
            return None

        if Operator and isinstance(expr, Operator):
            left_val = self._eval_width_expr_with_param(expr.left, param_name, param_value) if hasattr(expr, 'left') else None
            right_val = self._eval_width_expr_with_param(expr.right, param_name, param_value) if hasattr(expr, 'right') else None

            if left_val is not None and right_val is not None:
                if Plus and isinstance(expr, Plus):
                    return left_val + right_val
                elif Minus and isinstance(expr, Minus):
                    return left_val - right_val
                elif Times and isinstance(expr, Times):
                    return left_val * right_val
                elif Divide and isinstance(expr, Divide) and right_val != 0:
                    return left_val // right_val

        return None

    def get_width_expr_str(self) -> str:
        """获取位宽的字符串表示"""
        if hasattr(self, '_width_msb_expr') and self._width_msb_expr is not None:
            msb_str = self._expr_to_str(self._width_msb_expr)
            lsb_str = self._expr_to_str(self._width_lsb_expr) if hasattr(self, '_width_lsb_expr') and self._width_lsb_expr else "0"
            return f"[{msb_str}:{lsb_str}]"
        elif self.width_msb is not None and self.width_lsb is not None:
            return f"[{self.width_msb}:{self.width_lsb}]"
        return ""

    def _expr_to_str(self, expr) -> str:
        """将表达式转换为字符串"""
        ast_types = _get_ast_types()
        IntConst = ast_types.get('IntConst')
        Identifier = ast_types.get('Identifier')
        Operator = ast_types.get('Operator')

        if expr is None:
            return "0"
        if IntConst and isinstance(expr, IntConst):
            return str(expr.value)
        if Identifier and isinstance(expr, Identifier):
            return expr.name
        if Operator and isinstance(expr, Operator):
            left = self._expr_to_str(expr.left) if hasattr(expr, 'left') else ""
            right = self._expr_to_str(expr.right) if hasattr(expr, 'right') else ""
            op = self._get_op_str(expr)
            return f"{left}{op}{right}"
        return str(expr)

    def _get_op_str(self, op) -> str:
        """获取运算符的字符串表示"""
        ast_types = _get_ast_types()
        Plus = ast_types.get('Plus')
        Minus = ast_types.get('Minus')
        Times = ast_types.get('Times')
        Divide = ast_types.get('Divide')
        Mod = ast_types.get('Mod')
        Power = ast_types.get('Power')

        op_map = {}
        if Plus:
            op_map[Plus] = "+"
        if Minus:
            op_map[Minus] = "-"
        if Times:
            op_map[Times] = "*"
        if Divide:
            op_map[Divide] = "/"
        if Mod:
            op_map[Mod] = "%"
        if Power:
            op_map[Power] = "**"

        return op_map.get(type(op), "?")

    def set_array_dimensions(self, dimensions: List[tuple]):
        """设置数组维度 [(msb1, lsb1), (msb2, lsb2), ...]"""
        self.array_dimensions = dimensions
        self.is_array = len(dimensions) > 0

    # ============ 赋值标记方法 ============

    def mark_blocking_assigned(self):
        """
        标记为阻塞赋值
        如果是wire类型，自动升级为reg类型
        """
        self.is_blocking_assigned = True
        self.is_assigned = True
        if self.type == SymbolType.WIRE:
            self.type = SymbolType.REG
            self._update_reg_type()

    def mark_nonblocking_assigned(self):
        """标记为非阻塞赋值"""
        self.is_nonblocking_assigned = True
        self.is_assigned = True

    def mark_used(self):
        """标记为已使用"""
        self.is_used = True

    def mark_initialized(self):
        """标记为已初始化"""
        self.is_initialized = True

    def _update_reg_type(self):
        """更新为reg类型时的额外处理"""
        # reg类型通常用于过程赋值
        pass

    # ============ 实例特定方法 ============

    def set_instance_info(self, module_name: str, params: dict = None):
        """设置实例信息"""
        if self.type == SymbolType.INSTANCE:
            self.instance_module = module_name
            self.instance_params = params or {}

    # ============ 参数特定方法 ============

    def set_param_value(self, value: Any):
        """设置参数值"""
        if self.is_param_type():
            self.param_value = value
            self.initial_value = value
            self.has_initial = True
            self.is_constant = True
            self.is_initialized = True

    # ============ 三地址码(TAC)方法 ============

    def is_temp(self) -> bool:
        """检查是否是临时变量"""
        return self.type == SymbolType.TEMP

    def is_label(self) -> bool:
        """检查是否是标签"""
        return self.type == SymbolType.LABEL

    def init_tac_info(self):
        """初始化TAC信息"""
        if self.tac_info is None:
            self.tac_info = TACInfo()

    def set_const_value(self, value: Any):
        """设置常量值（用于常量传播）"""
        self.init_tac_info()
        self.tac_info.const_value = value
        self.tac_info.is_compile_time_const = True

    def get_const_value(self) -> Any:
        """获取常量值"""
        if self.tac_info and self.tac_info.is_compile_time_const:
            return self.tac_info.const_value
        return None

    def is_const(self) -> bool:
        """检查是否是编译时常量"""
        if self.tac_info:
            return self.tac_info.is_compile_time_const
        return False

    def add_def_tac(self, tac: Any):
        """添加定义该符号的TAC指令"""
        self.def_tac = tac

    def add_use_tac(self, tac: Any):
        """添加使用该符号的TAC指令"""
        if tac not in self.use_tacs:
            self.use_tacs.append(tac)

    def get_use_count(self) -> int:
        """获取使用次数"""
        return len(self.use_tacs)

    def set_basic_block(self, block_id: int, is_leader: bool = False):
        """设置基本块信息"""
        self.basic_block_id = block_id
        self.is_basic_block_leader = is_leader

    def get_tac_repr(self) -> str:
        """获取TAC表示形式"""
        if self.is_label():
            return f"{self.name}:"
        if self.is_temp():
            return self.name
        return self.name

    # ============ 定义-使用链方法 ============

    def add_def_site(self, lineno: int, stmt: Any, def_type: str = "",
                     rhs_symbols: List['Symbol'] = None,
                     basic_block_id: Optional[int] = None) -> DefSite:
        """
        添加定义点
        返回创建的 DefSite 对象
        """
        def_site = DefSite(
            symbol=self,
            lineno=lineno,
            stmt=stmt,
            def_type=def_type,
            rhs_symbols=rhs_symbols or [],
            basic_block_id=basic_block_id
        )
        self.def_sites.append(def_site)
        return def_site

    def add_use_site(self, lineno: int, stmt: Any, use_type: str = "",
                     basic_block_id: Optional[int] = None) -> UseSite:
        """
        添加使用点
        返回创建的 UseSite 对象
        """
        use_site = UseSite(
            symbol=self,
            lineno=lineno,
            stmt=stmt,
            use_type=use_type,
            basic_block_id=basic_block_id
        )
        self.use_sites.append(use_site)
        self.mark_used()  # 标记符号已被使用
        return use_site

    def add_dataflow_edge(self, def_site: DefSite, use_site: UseSite,
                          is_control_dependent: bool = False) -> DataflowEdge:
        """
        添加数据流边，连接定义点和使用点
        """
        edge = DataflowEdge(
            source_def=def_site,
            target_use=use_site,
            is_control_dependent=is_control_dependent
        )
        self.dataflow_edges.append(edge)
        return edge

    def get_last_def_before(self, lineno: int) -> Optional[DefSite]:
        """
        获取指定行号之前的最后一个定义点
        用于数据流分析，找到变量的定义来源
        """
        last_def = None
        for def_site in self.def_sites:
            if def_site.lineno <= lineno:
                if last_def is None or def_site.lineno > last_def.lineno:
                    last_def = def_site
        return last_def

    def get_uses_after_def(self, def_site: DefSite) -> List[UseSite]:
        """
        获取指定定义点之后的所有使用点
        """
        result = []
        for use_site in self.use_sites:
            if use_site.lineno >= def_site.lineno:
                result.append(use_site)
        return result

    def get_reaching_defs(self, lineno: int) -> List[DefSite]:
        """
        获取能够到达指定行号的所有定义点（Reaching Definitions）
        """
        return [d for d in self.def_sites if d.lineno <= lineno]

    def has_def_at(self, lineno: int) -> bool:
        """检查在指定行号是否有定义"""
        return any(d.lineno == lineno for d in self.def_sites)

    def has_use_at(self, lineno: int) -> bool:
        """检查在指定行号是否有使用"""
        return any(u.lineno == lineno for u in self.use_sites)

    def get_defs_by_type(self, def_type: str) -> List[DefSite]:
        """按类型获取定义点"""
        return [d for d in self.def_sites if d.def_type == def_type]

    def get_uses_by_type(self, use_type: str) -> List[UseSite]:
        """按类型获取使用点"""
        return [u for u in self.use_sites if u.use_type == use_type]

    def get_dataflow_successors(self) -> List['Symbol']:
        """
        获取数据流后继（即使用此符号定义的其他符号）
        """
        successors = []
        for def_site in self.def_sites:
            for edge in self.dataflow_edges:
                if edge.source_def == def_site:
                    target_sym = edge.target_use.symbol
                    if target_sym != self and target_sym not in successors:
                        successors.append(target_sym)
        return successors

    def get_dataflow_predecessors(self) -> List['Symbol']:
        """
        获取数据流前驱（即定义此符号使用的符号）
        """
        predecessors = []
        for def_site in self.def_sites:
            for sym in def_site.rhs_symbols:
                if sym != self and sym not in predecessors:
                    predecessors.append(sym)
        return predecessors

    def is_dead_def(self, def_site: DefSite) -> bool:
        """
        检查某个定义是否是死定义（没有被后续使用）
        """
        for use_site in self.use_sites:
            if use_site.lineno > def_site.lineno:
                return False
        return True

    def get_use_count_in_stmt(self, stmt: Any) -> int:
        """统计在特定语句中的使用次数"""
        return sum(1 for u in self.use_sites if u.stmt == stmt)

    # ============ 序列化方法 ============

    def to_dict(self) -> dict:
        """转换为字典表示"""
        return {
            "name": self.name,
            "type": self.type.value,
            "scope_level": self.scope_level,
            "lineno": self.lineno,
            "width": self.width,
            "width_msb": self.width_msb,
            "width_lsb": self.width_lsb,
            "is_array": self.is_array,
            "array_dimensions": self.array_dimensions,
            "is_initialized": self.is_initialized,
            "is_assigned": self.is_assigned,
            "is_blocking_assigned": self.is_blocking_assigned,
            "is_nonblocking_assigned": self.is_nonblocking_assigned,
            "is_used": self.is_used,
            "instance_module": self.instance_module,
            "param_value": str(self.param_value) if self.param_value is not None else None,
            "is_temp": self.is_temp(),
            "is_label": self.is_label(),
            "is_const": self.is_const(),
            "const_value": str(self.get_const_value()) if self.get_const_value() is not None else None,
            "basic_block_id": self.basic_block_id,
            "use_count": self.get_use_count(),
            "def_sites_count": len(self.def_sites),
            "use_sites_count": len(self.use_sites),
            "dataflow_edges_count": len(self.dataflow_edges),
        }


# ============ 符号工厂函数 ============

def create_input_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建输入端口符号"""
    symbol = Symbol(name, SymbolType.INPUT, scope_level, lineno, node)
    symbol.port_direction = "input"
    return symbol


def create_output_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建输出端口符号"""
    symbol = Symbol(name, SymbolType.OUTPUT, scope_level, lineno, node)
    symbol.port_direction = "output"
    return symbol


def create_inout_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建双向端口符号"""
    symbol = Symbol(name, SymbolType.INOUT, scope_level, lineno, node)
    symbol.port_direction = "inout"
    return symbol


def create_wire_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建wire符号"""
    return Symbol(name, SymbolType.WIRE, scope_level, lineno, node)


def create_reg_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建reg符号"""
    return Symbol(name, SymbolType.REG, scope_level, lineno, node)


def create_parameter_symbol(name: str, scope_level: int, lineno: int, value: Any = None, node: Any = None) -> Symbol:
    """创建参数符号"""
    symbol = Symbol(name, SymbolType.PARAMETER, scope_level, lineno, node)
    symbol.is_param = True
    if value is not None:
        symbol.set_param_value(value)
    return symbol


def create_instance_symbol(name: str, module_name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建实例符号"""
    symbol = Symbol(name, SymbolType.INSTANCE, scope_level, lineno, node)
    symbol.set_instance_info(module_name)
    return symbol


def create_identifier_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建一般标识符符号（默认wire类型）"""
    return Symbol(name, SymbolType.IDENTIFIER, scope_level, lineno, node)


# ============ TAC相关工厂函数 ============

_temp_counter: int = 0  # 临时变量计数器
_label_counter: int = 0  # 标签计数器


def reset_tac_counters():
    """重置TAC计数器"""
    global _temp_counter, _label_counter
    _temp_counter = 0
    _label_counter = 0


def create_temp_symbol(scope_level: int, lineno: int, width: int = 1, node: Any = None) -> Symbol:
    """
    创建临时变量符号（用于TAC）
    自动生成唯一名称如: t0, t1, t2...
    """
    global _temp_counter
    name = f"t{_temp_counter}"
    _temp_counter += 1

    symbol = Symbol(name, SymbolType.TEMP, scope_level, lineno, node)
    symbol.width = width
    symbol.init_tac_info()
    symbol.tac_info.temp_id = _temp_counter - 1
    return symbol


def create_label_symbol(label_name: Optional[str] = None, scope_level: int = 0, lineno: int = 0, node: Any = None) -> Symbol:
    """
    创建标签符号（用于TAC）
    如果未提供名称，自动生成如: L0, L1, L2...
    """
    global _label_counter
    if label_name is None:
        name = f"L{_label_counter}"
        _label_counter += 1
    else:
        name = label_name

    symbol = Symbol(name, SymbolType.LABEL, scope_level, lineno, node)
    symbol.init_tac_info()
    return symbol


def create_integer_symbol(name: str, scope_level: int, lineno: int, node: Any = None) -> Symbol:
    """创建integer类型符号"""
    return Symbol(name, SymbolType.INTEGER, scope_level, lineno, node)
