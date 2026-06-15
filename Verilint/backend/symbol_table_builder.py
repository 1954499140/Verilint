from typing import Optional, Dict, List, Any, Tuple, Set
from pyverilog.vparser.ast import *
from symbol import (
    Symbol, SymbolType, TACInfo,
    create_input_symbol, create_output_symbol, create_inout_symbol,
    create_wire_symbol, create_reg_symbol, create_parameter_symbol,
    create_instance_symbol, create_identifier_symbol, create_temp_symbol,
    create_label_symbol, create_integer_symbol, reset_tac_counters
)


class Scope:
    """
    作用域类
    表示符号表中的一个作用域层级
    """

    def __init__(self, name: str, level: int, parent: Optional['Scope'] = None, node: Any = None):
        self.name = name                # 作用域名称（如模块名、always块名）
        self.level = level              # 作用域层级（0为根作用域）
        self.parent = parent            # 父作用域
        self.node = node                # 关联的AST节点

        self.symbols: Dict[str, Symbol] = {}        # 当前作用域的符号表
        self.children: List['Scope'] = []           # 子作用域列表

        # 按类型分类的符号（便于快速查询）
        self.inputs: Dict[str, Symbol] = {}
        self.outputs: Dict[str, Symbol] = {}
        self.inouts: Dict[str, Symbol] = {}
        self.wires: Dict[str, Symbol] = {}
        self.regs: Dict[str, Symbol] = {}
        self.parameters: Dict[str, Symbol] = {}
        self.instances: Dict[str, Symbol] = {}
        self.temps: Dict[str, Symbol] = {}
        self.labels: Dict[str, Symbol] = {}

    def add_symbol(self, symbol: Symbol) -> bool:
        """
        添加符号到当前作用域
        返回是否添加成功（同名符号已存在则返回False）
        """
        if symbol.name in self.symbols:
            return False

        self.symbols[symbol.name] = symbol

        # 按类型分类存储
        if symbol.type == SymbolType.INPUT:
            self.inputs[symbol.name] = symbol
        elif symbol.type == SymbolType.OUTPUT:
            self.outputs[symbol.name] = symbol
        elif symbol.type == SymbolType.INOUT:
            self.inouts[symbol.name] = symbol
        elif symbol.type == SymbolType.WIRE:
            self.wires[symbol.name] = symbol
        elif symbol.type == SymbolType.REG:
            self.regs[symbol.name] = symbol
        elif symbol.type in (SymbolType.PARAMETER, SymbolType.LOCALPARAM):
            self.parameters[symbol.name] = symbol
        elif symbol.type == SymbolType.INSTANCE:
            self.instances[symbol.name] = symbol
        elif symbol.type == SymbolType.TEMP:
            self.temps[symbol.name] = symbol
        elif symbol.type == SymbolType.LABEL:
            self.labels[symbol.name] = symbol

        return True

    def get_symbol(self, name: str) -> Optional[Symbol]:
        """
        在当前作用域查找符号
        只查找当前作用域，不向上查找父作用域
        """
        return self.symbols.get(name)

    def lookup_symbol(self, name: str) -> Optional[Symbol]:
        """
        查找符号（从当前作用域向上递归查找）
        """
        current = self
        while current is not None:
            symbol = current.get_symbol(name)
            if symbol is not None:
                return symbol
            current = current.parent
        return None

    def has_symbol(self, name: str) -> bool:
        """检查符号是否存在于当前作用域"""
        return name in self.symbols

    def create_child_scope(self, name: str, node: Any = None) -> 'Scope':
        """创建子作用域"""
        child = Scope(name, self.level + 1, self, node)
        self.children.append(child)
        return child

    def get_all_symbols(self) -> List[Symbol]:
        """获取当前作用域的所有符号"""
        return list(self.symbols.values())

    def __repr__(self) -> str:
        return f"Scope(name='{self.name}', level={self.level}, symbols={len(self.symbols)})"


class SymbolTableBuilder:
    """
    符号表构建器
    基于 Pyverilog AST 构建层级化的符号表
    """

    def __init__(self):
        self.root_scope: Optional[Scope] = None     # 根作用域
        self.current_scope: Optional[Scope] = None  # 当前作用域
        self.all_scopes: List[Scope] = []           # 所有作用域列表

        # 模块信息
        self.modules: Dict[str, Scope] = {}         # 模块名到作用域的映射
        self.current_module: Optional[Scope] = None # 当前正在处理的模块
        self.unknown_instance_modules: Set[str] = set()  # 未定义/未知的实例模块名

        # 错误和警告
        self.errors: List[str] = []
        self.warnings: List[str] = []

        # 临时变量和标签计数器
        self._temp_counter: int = 0
        self._label_counter: int = 0

    def reset(self):
        """重置构建器状态"""
        self.root_scope = None
        self.current_scope = None
        self.all_scopes = []
        self.modules = {}
        self.current_module = None
        self.errors = []
        self.warnings = []
        self._temp_counter = 0
        self._label_counter = 0
        self.unknown_instance_modules: Set[str] = set()
        reset_tac_counters()

    def register_known_modules(self, module_names: List[str]):
        """
        预注册已知模块名（用于多文件解析场景）
        在调用 build() 之前调用此方法，可以避免这些模块被标记为未知实例
        """
        for name in module_names:
            if name not in self.modules:
                self.modules[name] = None  # 占位符，表示模块已知但未加载

    def build(self, ast: Source) -> Optional[Scope]:
        """
        从 Pyverilog AST 构建符号表
        返回根作用域
        """
        self.reset()

        if ast is None or not hasattr(ast, 'description'):
            self.errors.append("Invalid AST: missing description")
            return None

        # 创建根作用域
        self.root_scope = Scope("global", 0, None, ast)
        self.current_scope = self.root_scope
        self.all_scopes.append(self.root_scope)

        # 第一步：收集所有模块定义（不处理内部）
        description = ast.description
        module_defs = []
        for definition in description.definitions:
            if isinstance(definition, ModuleDef):
                module_defs.append(definition)
                # 先记录模块名，用于后续判断实例是否未知
                self.modules[definition.name] = None  # 占位，稍后填充

        # 第二步：处理所有模块内部
        for definition in module_defs:
        
            self._process_module(definition)

        # 后处理：清除未知实例中信号的时钟/复位标记
        # self._post_process_unknown_instances()

        return self.root_scope

    def merge_modules_from(self, other_stb: 'SymbolTableBuilder'):
        """
        从另一个 SymbolTableBuilder 合并模块信息
        用于多文件解析场景，先解析所有文件收集模块名，再逐个处理
        """
        for module_name, module_scope in other_stb.modules.items():
            if module_name not in self.modules:
                self.modules[module_name] = module_scope

    def _post_process_unknown_instances(self):
        """
        后处理：清除所有标记为 in_unknown_instance 的符号的时钟/复位标记
        这样这些信号就不会参与时钟/复位信号的讨论
        """
        cleared_count = 0
        for scope in self.all_scopes:
            for symbol in scope.symbols.values():
                if symbol.in_unknown_instance:
                    if symbol.is_clock or symbol.is_async_reset or symbol.is_sync_reset:
                        symbol.is_clock = False
                        symbol.is_async_reset = False
                        symbol.is_sync_reset = False
                        cleared_count += 1

        if cleared_count > 0:
            print(f"[SymbolTable] Cleared clock/reset flags for {cleared_count} signals in unknown instances")

    def _process_module(self, module: ModuleDef):
        """处理模块定义"""
        module_name = module.name

        # 创建模块作用域（作为根作用域的子作用域）
        module_scope = self.root_scope.create_child_scope(module_name, module)
        module_scope.level = 1  # 模块层级固定为1
        self.all_scopes.append(module_scope)

        self.current_scope = module_scope
        self.current_module = module_scope
        # 更新模块映射（之前是占位符None，现在填入实际scope）
        self.modules[module_name] = module_scope
        
        # 处理参数列表
        if module.paramlist:
            self._process_paramlist(module.paramlist)
           
        # 处理端口列表
        if module.portlist:
            self._process_portlist(module.portlist)

        # 处理模块内部项
        for item in module.items:
            self._process_module_item(item)
        self.current_module = None
    def _process_paramlist(self, paramlist: Paramlist):
        """处理参数列表"""
        for param in paramlist.params:
            self._process_param(param)
    def _process_rvalue(self, rvalue):
        """
        处理右侧表达式 (Rvalue)
        递归遍历表达式，提取所有标识符并标记为已使用

        Args:
            rvalue: 右侧表达式（可以是 Rvalue 包装器或任意表达式）
        """
        if rvalue is None:
            return

        # 解包 Rvalue
        if isinstance(rvalue, Rvalue):
            rvalue = rvalue.var

        # 标识符 - 直接标记为使用
        if isinstance(rvalue, Identifier):
            symbol = self.current_scope.lookup_symbol(rvalue.name)
            if symbol:
                symbol.mark_used()
            else:
                # 未定义的标识符，创建隐式声明（wire）
                symbol = create_wire_symbol(
                    rvalue.name,
                    self.current_scope.level,
                    getattr(rvalue, 'lineno', 0),
                    rvalue
                )
                self._add_symbol(symbol)
                symbol.mark_used()
        elif isinstance(rvalue, IntConst):
            return self._eval_const_expr(rvalue)
        # 位选择或数组索引 (a[0], arr[idx])
        elif isinstance(rvalue, Pointer):
            # 处理基础变量
            if isinstance(rvalue.var, Identifier):
                symbol = self.current_scope.lookup_symbol(rvalue.var.name)
                if symbol:
                    symbol.mark_used()
                else:
                    symbol = create_wire_symbol(
                        rvalue.var.name,
                        self.current_scope.level,
                        getattr(rvalue, 'lineno', 0),
                        rvalue.var
                    )
                    self._add_symbol(symbol)
                    symbol.mark_used()
            # 递归处理索引表达式
            if hasattr(rvalue, 'ptr') and rvalue.ptr:
                self._process_rvalue(rvalue.ptr)

        # 部分选择 (a[7:0])
        elif isinstance(rvalue, Partselect):
            # 处理基础变量
            if isinstance(rvalue.var, Identifier):
                symbol = self.current_scope.lookup_symbol(rvalue.var.name)
                if symbol:
                    symbol.mark_used()
                else:
                    symbol = create_wire_symbol(
                        rvalue.var.name,
                        self.current_scope.level,
                        getattr(rvalue, 'lineno', 0),
                        rvalue.var
                    )
                    self._add_symbol(symbol)
                    symbol.mark_used()
            # 递归处理位宽范围
            if hasattr(rvalue, 'msb') and rvalue.msb:
                self._process_rvalue(rvalue.msb)
            if hasattr(rvalue, 'lsb') and rvalue.lsb:
                self._process_rvalue(rvalue.lsb)

        # 拼接操作 ({a, b, c})
        elif isinstance(rvalue, Concat):
            if hasattr(rvalue, 'list') and rvalue.list:
                for item in rvalue.list:
                    self._process_rvalue(item)

        # 重复操作 ({4{a}})
        elif isinstance(rvalue, Repeat):
            if hasattr(rvalue, 'value') and rvalue.value:
                self._process_rvalue(rvalue.value)
            if hasattr(rvalue, 'times') and rvalue.times:
                self._process_rvalue(rvalue.times)

        # 条件表达式 (cond ? true : false)
        elif hasattr(rvalue, 'cond') and hasattr(rvalue, 'true_value') and hasattr(rvalue, 'false_value'):
            self._process_rvalue(rvalue.cond)
            self._process_rvalue(rvalue.true_value)
            self._process_rvalue(rvalue.false_value)

        # 函数调用
        elif isinstance(rvalue, FunctionCall):
            # 处理函数名
            if hasattr(rvalue, 'name') and isinstance(rvalue.name, Identifier):
                symbol = self.current_scope.lookup_symbol(rvalue.name.name)
                if symbol:
                    symbol.mark_used()
            # 处理参数
            if hasattr(rvalue, 'args') and rvalue.args:
                for arg in rvalue.args:
                    self._process_rvalue(arg)

        # 系统函数调用 ($display, $monitor 等)
        elif isinstance(rvalue, SystemCall):
            if hasattr(rvalue, 'args') and rvalue.args:
                for arg in rvalue.args:
                    self._process_rvalue(arg)

        # 一元运算符
        elif isinstance(rvalue, (Uplus, Uminus, Ulnot, Unot, Uand, Unand, Uor, Unor, Uxor, Uxnor)):
            right_val = None
            if hasattr(rvalue, 'right') and rvalue.right:
                right_val = self._process_rvalue(rvalue.right)
            # 计算结果
            if right_val is not None and isinstance(right_val, int):
                if isinstance(rvalue, Uplus):
                    return +right_val
                elif isinstance(rvalue, Uminus):
                    return -right_val
                elif isinstance(rvalue, Ulnot):
                    return 1 if right_val == 0 else 0
                elif isinstance(rvalue, Unot):
                    return ~right_val
                elif isinstance(rvalue, Uand):
                    # 缩减与：所有位都为1时返回1
                    width = right_val.bit_length() if right_val > 0 else 1
                    mask = (1 << width) - 1
                    return 1 if right_val == mask else 0
                elif isinstance(rvalue, Unand):
                    width = right_val.bit_length() if right_val > 0 else 1
                    mask = (1 << width) - 1
                    return 0 if right_val == mask else 1
                elif isinstance(rvalue, Uor):
                    return 1 if right_val != 0 else 0
                elif isinstance(rvalue, Unor):
                    return 0 if right_val != 0 else 1
                elif isinstance(rvalue, Uxor):
                    # 缩减异或：计算所有位的奇偶性
                    result = 0
                    val = right_val
                    while val:
                        result ^= val & 1
                        val >>= 1
                    return result
                elif isinstance(rvalue, Uxnor):
                    result = 0
                    val = right_val
                    while val:
                        result ^= val & 1
                        val >>= 1
                    return 1 - result
            return int(right_val) if right_val is not None else None

        # 二元运算符
        elif hasattr(rvalue, 'left') and hasattr(rvalue, 'right'):
            left_val = self._process_rvalue(rvalue.left)
            right_val = self._process_rvalue(rvalue.right)
            # 计算常量表达式结果
            if left_val is not None and right_val is not None:
                if isinstance(rvalue, Plus):
                    return left_val + right_val
                elif isinstance(rvalue, Minus):
                    return left_val - right_val
                elif isinstance(rvalue, Times):
                    return left_val * right_val
                elif isinstance(rvalue, Divide):
                    return left_val // right_val if right_val != 0 else None
                elif isinstance(rvalue, Mod):
                    return left_val % right_val if right_val != 0 else None
                elif isinstance(rvalue, Power):
                    return left_val ** right_val
                elif isinstance(rvalue, Sll):
                    return left_val << right_val
                elif isinstance(rvalue, Srl):
                    return left_val >> right_val
                elif isinstance(rvalue, Sla):
                    return left_val << right_val
                elif isinstance(rvalue, Sra):
                    # 算术右移：保留符号位
                    if left_val < 0:
                        width = max(left_val.bit_length(), abs(right_val)) + 1
                        sign_mask = ~((1 << width) - 1)
                        return (left_val >> right_val) | sign_mask
                    return left_val >> right_val
                elif isinstance(rvalue, And):
                    return left_val & right_val
                elif isinstance(rvalue, Or):
                    return left_val | right_val
                elif isinstance(rvalue, Xor):
                    return left_val ^ right_val
                elif isinstance(rvalue, Xnor):
                    return ~(left_val ^ right_val)
                elif isinstance(rvalue, Land):
                    return 1 if (left_val != 0 and right_val != 0) else 0
                elif isinstance(rvalue, Lor):
                    return 1 if (left_val != 0 or right_val != 0) else 0
                elif isinstance(rvalue, Eq):
                    return 1 if left_val == right_val else 0
                elif isinstance(rvalue, NotEq):
                    return 1 if left_val != right_val else 0
                elif isinstance(rvalue, LessThan):
                    return 1 if left_val < right_val else 0
                elif isinstance(rvalue, GreaterThan):
                    return 1 if left_val > right_val else 0
                elif isinstance(rvalue, LessEq):
                    return 1 if left_val <= right_val else 0
                elif isinstance(rvalue, GreaterEq):
                    return 1 if left_val >= right_val else 0

        # 通用递归处理 - 尝试常见属性
        else:
            for attr_name in ['var', 'next', 'args', 'value', 'expr']:
                if hasattr(rvalue, attr_name):
                    attr_val = getattr(rvalue, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                self._process_rvalue(item)
                        else:
                            self._process_rvalue(attr_val)

    def _process_param(self, param):
        """处理单个参数"""
        # 先检查是否是 Decl，如果是则递归处理其中的列表
        if isinstance(param, Decl):
            for p in param.list:
                self._process_param(p)
            return

        # 现在 param 应该是 Parameter 或 Localparam
        if isinstance(param, Parameter):
            # 获取参数值的 AST 节点（用于后续计算）
            value_node = param.value if hasattr(param, 'value') else None
            # 处理 value 中的标识符引用（标记为使用）
            if value_node is not None:
                self._process_rvalue(value_node)

            symbol = create_parameter_symbol(
                param.name,
                self.current_scope.level,
                param.lineno,
                value_node,  # 保存 AST 节点，不是计算值
                param
            )
            # 尝试解析参数值
            if hasattr(param, 'value') and param.value is not None:
                const_val = self._eval_const_expr(param.value)
                if const_val is not None:
                    symbol.set_const_value(const_val)

            self._add_symbol(symbol)

    def _process_portlist(self, portlist: Portlist):
        """处理端口列表"""
        for port in portlist.ports:
            self._process_port(port)

    def _process_port(self, port):
        """处理单个端口"""
        if isinstance(port, Ioport):
            # 确定端口方向和类型
            first = port.first

            if isinstance(first, Input):
                symbol = create_input_symbol(
                    first.name,
                    self.current_scope.level,
                    port.lineno,
                    port
                )
                # 设置位宽
                if hasattr(first, 'width') and first.width is not None:
                    msb, lsb = self._extract_width(first.width)
                    symbol.set_width(msb, lsb)
                symbol.mark_initialized()  # 输入端口默认已初始化

            elif isinstance(first, Output):
                symbol = create_output_symbol(
                    first.name,
                    self.current_scope.level,
                    port.lineno,
                    port
                )
                if hasattr(first, 'width') and first.width is not None:
                    msb, lsb = self._extract_width(first.width)
                    symbol.set_width(msb, lsb)

            elif isinstance(first, Inout):
                symbol = create_inout_symbol(
                    first.name,
                    self.current_scope.level,
                    port.lineno,
                    port
                )
                if hasattr(first, 'width') and first.width is not None:
                    msb, lsb = self._extract_width(first.width)
                    symbol.set_width(msb, lsb)
                symbol.mark_initialized()

            elif isinstance(first, Wire):
                symbol = create_wire_symbol(
                    first.name,
                    self.current_scope.level,
                    port.lineno,
                    port
                )
                if hasattr(first, 'width') and first.width is not None:
                    msb, lsb = self._extract_width(first.width)
                    symbol.set_width(msb, lsb)

            elif isinstance(first, Reg):
                symbol = create_reg_symbol(
                    first.name,
                    self.current_scope.level,
                    port.lineno,
                    port
                )
                if hasattr(first, 'width') and first.width is not None:
                    msb, lsb = self._extract_width(first.width)
                    symbol.set_width(msb, lsb)
                symbol.mark_blocking_assigned()

            else:
                # 默认作为wire处理
                symbol = create_wire_symbol(
                    str(first.name) if hasattr(first, 'name') else str(first),
                    self.current_scope.level,
                    port.lineno,
                    port
                )

            self._add_symbol(symbol)

    def _process_module_item(self, item):
        """处理模块内部项"""
        if isinstance(item, Decl):
            self._process_decl(item)
        elif isinstance(item, Always):
            self._process_always(item)
        elif isinstance(item, Assign):
            self._process_assign(item)
        elif isinstance(item, InstanceList):
            self._process_instance_list(item)
        elif isinstance(item, GenerateStatement):
            self._process_generate(item)
        elif isinstance(item, Initial):
            self._process_initial(item)
        elif isinstance(item, Function):
            self._process_function(item)
        elif isinstance(item, Task):
            self._process_task(item)

    def _process_decl(self, decl: Decl):
        """处理声明语句"""
        for item in decl.list:
            if isinstance(item, Input):
                symbol = create_input_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                if hasattr(item, 'width') and item.width is not None:
                    msb, lsb = self._extract_width(item.width)
                    symbol.set_width(msb, lsb)
                symbol.mark_initialized()  # input端口默认已初始化
                self._add_symbol(symbol)

            elif isinstance(item, Output):
                symbol = create_output_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                if hasattr(item, 'width') and item.width is not None:
                    msb, lsb = self._extract_width(item.width)
                    symbol.set_width(msb, lsb)
                self._add_symbol(symbol)

            elif isinstance(item, Inout):
                symbol = create_inout_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                if hasattr(item, 'width') and item.width is not None:
                    msb, lsb = self._extract_width(item.width)
                    symbol.set_width(msb, lsb)
                symbol.mark_initialized()
                self._add_symbol(symbol)

            elif isinstance(item, Wire):
                symbol = create_wire_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                if hasattr(item, 'width') and item.width is not None:
                    msb, lsb = self._extract_width(item.width)
                    symbol.set_width(msb, lsb)
                # 提取数组维度
                if hasattr(item, 'dimensions') and item.dimensions is not None:
                    dims = self._extract_dimensions(item.dimensions)
                    if dims:
                        symbol.set_array_dimensions(dims)
                self._add_symbol(symbol)

            elif isinstance(item, Reg):
                symbol = create_reg_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                if hasattr(item, 'width') and item.width is not None:
                    msb, lsb = self._extract_width(item.width)
                    symbol.set_width(msb, lsb)
                # 提取数组维度
                if hasattr(item, 'dimensions') and item.dimensions is not None:
                    dims = self._extract_dimensions(item.dimensions)
                    if dims:
                        symbol.set_array_dimensions(dims)
                # 检查是否有初始值
                if hasattr(item, 'value') and item.value is not None:
                    symbol.initial_value = item.value
                    symbol.has_initial = True
                    symbol.mark_blocking_assigned()
                # 无论是否有初始值，都要添加符号
                self._add_symbol(symbol)

            elif isinstance(item, Integer):
                symbol = create_integer_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item
                )
                self._add_symbol(symbol)

            elif isinstance(item, Parameter):
                self._process_param(item)

            elif isinstance(item, Localparam):
                symbol = create_parameter_symbol(
                    item.name,
                    self.current_scope.level,
                    item.lineno,
                    item.value if hasattr(item, 'value') else item,
                    item
                )
                symbol.type = SymbolType.LOCALPARAM
                const_val = self._eval_const_expr(item.value) if hasattr(item, 'value') else None
                if const_val is not None:
                    symbol.set_const_value(const_val)
                self._add_symbol(symbol)

    def _process_always(self, always: Always):
        """处理 always 块，识别时钟和复位信号"""
        # 为 always 块创建新作用域
        scope_name = f"always_{always.lineno}"
        always_scope = self.current_scope.create_child_scope(scope_name, always)
        always_scope.level = self.current_scope.level + 1
        self.all_scopes.append(always_scope)

        prev_scope = self.current_scope
        self.current_scope = always_scope

        # 分析敏感列表，识别时钟和异步复位
        sens_info = self._analyze_sensitivity_list(always)

        # 根据敏感列表类型进行不同处理
        if sens_info['is_sequential']:
            # 时序逻辑：进一步分析内部结构识别同步复位
            self._analyze_sequential_block(always, sens_info)

        # 处理 always 块内的语句
        if always.statement:
            self._process_statement(always.statement)

        self.current_scope = prev_scope

    def _analyze_sensitivity_list(self, always: Always) -> dict:
        """
        分析敏感列表，识别边沿触发信号
        返回: {
            'is_sequential': bool,      # 是否是时序逻辑（有边沿触发）
            'clock_signals': list,       # 时钟信号列表
            'async_reset_signals': list, # 异步复位信号列表（候选）
            'edge_types': dict           # 信号名 -> 'posedge'/'negedge'
        }
        """
        result = {
            'is_sequential': False,
            'clock_signals': [],
            'async_reset_signals': [],
            'edge_types': {}
        }

        if not hasattr(always, 'sens_list') or not always.sens_list:
            return result

        sens_list = always.sens_list
        if not hasattr(sens_list, 'list') or not sens_list.list:
            return result

        edge_signals = []

        for sens in sens_list.list:
            if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                # 边沿触发
                result['is_sequential'] = True
                signal_name = None

                # 提取信号名
                if hasattr(sens, 'sig') and sens.sig:
                    if isinstance(sens.sig, Identifier):
                        signal_name = sens.sig.name
                    elif hasattr(sens.sig, 'name'):
                        signal_name = sens.sig.name

                if signal_name:
                    result['edge_types'][signal_name] = sens.type
                    edge_signals.append({
                        'name': signal_name,
                        'edge': sens.type
                    })

        # 根据内部结构判断时钟和复位
        if len(edge_signals) == 1:
            # 只有一个边沿信号，通常是时钟
            result['clock_signals'] = [edge_signals[0]['name']]
        elif len(edge_signals) >= 2:
            # 多个边沿信号，需要判断哪个是时钟哪个是复位
            # 先假设第一个是时钟，其余是异步复位候选
            # 后续会根据内部if语句进一步确认
            result['clock_signals'] = [edge_signals[0]['name']]
            result['async_reset_signals'] = [s['name'] for s in edge_signals[1:]]

        return result

    def _analyze_sequential_block(self, always: Always, sens_info: dict):
        """
        分析时序 always 块内部结构
        识别同步复位和确认异步复位
        """
        if not always.statement:
            return

        # 获取顶层语句
        top_stmt = always.statement
        if isinstance(top_stmt, Block) and top_stmt.statements:
            top_stmt = top_stmt.statements[0] if top_stmt.statements else None

        if not top_stmt:
            return

        clock_signals = sens_info['clock_signals']
        async_reset_signals = sens_info['async_reset_signals']

        # 检查顶层是否是 if 语句
        if isinstance(top_stmt, IfStatement):
            if_cond = top_stmt.cond

            # 提取 if 条件中的信号
            if_signals = self._extract_signals_from_expr(if_cond)

            # 规则2：检查是否是异步复位
            # if 条件信号在敏感列表中且是边沿触发
            for signal_name in if_signals:
                if signal_name in async_reset_signals:
                    # 检查 if 分支内是否有寄存器赋值
                    if self._has_register_assignment(top_stmt.true_statement):
                        # 确认是异步复位
                        self._mark_async_reset(signal_name, always.lineno)
                        # 标记主时钟
                        if clock_signals:
                            self._mark_clock(clock_signals[0], always.lineno)
                        return

            # 规则3：检查是否是同步复位
            # if 条件信号不在敏感列表的边沿信号中
            for signal_name in if_signals:
                if signal_name not in sens_info['edge_types']:
                    # 可能是同步复位
                    if self._has_register_assignment(top_stmt.true_statement):
                        self._mark_sync_reset(signal_name, always.lineno)
                        # 标记主时钟
                        if clock_signals:
                            self._mark_clock(clock_signals[0], always.lineno)
                        return

        # 如果没有识别到复位，标记时钟
        if clock_signals:
            self._mark_clock(clock_signals[0], always.lineno)

    def _extract_signals_from_expr(self, expr) -> list:
        """从表达式中提取所有信号名"""
        signals = []
        if expr is None:
            return signals

        if isinstance(expr, Identifier):
            signals.append(expr.name)
        elif isinstance(expr, Partselect):
            # 位选择如 r_addr[AW+1:2]
            if isinstance(expr.var, Identifier):
                signals.append(expr.var.name)
            # 递归处理位宽范围表达式
            if hasattr(expr, 'msb') and expr.msb:
                signals.extend(self._extract_signals_from_expr(expr.msb))
            if hasattr(expr, 'lsb') and expr.lsb:
                signals.extend(self._extract_signals_from_expr(expr.lsb))
        elif isinstance(expr, Concat):
            # 拼接操作 {a, b, c}
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    signals.extend(self._extract_signals_from_expr(item))
        elif isinstance(expr, Pointer):
            # 数组/向量指针如 arr[idx]
            if isinstance(expr.var, Identifier):
                signals.append(expr.var.name)
            if hasattr(expr, 'ptr') and expr.ptr:
                signals.extend(self._extract_signals_from_expr(expr.ptr))
        elif hasattr(expr, 'left'):
            signals.extend(self._extract_signals_from_expr(expr.left))
            if hasattr(expr, 'right'):
                signals.extend(self._extract_signals_from_expr(expr.right))

        return signals

    def _has_register_assignment(self, stmt) -> bool:
        """检查语句中是否包含寄存器赋值"""
        if stmt is None:
            return False

        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            return True

        # 递归检查子语句
        if hasattr(stmt, 'statements'):
            for s in stmt.statements:
                if self._has_register_assignment(s):
                    return True
        if hasattr(stmt, 'true_statement') and stmt.true_statement:
            if self._has_register_assignment(stmt.true_statement):
                return True
        if hasattr(stmt, 'false_statement') and stmt.false_statement:
            if self._has_register_assignment(stmt.false_statement):
                return True
        if hasattr(stmt, 'statement') and stmt.statement:
            if self._has_register_assignment(stmt.statement):
                return True

        return False

    def _mark_clock(self, signal_name: str, lineno: int):
        """标记信号为时钟"""
        symbol = self.current_scope.lookup_symbol(signal_name)
        if symbol:
            # 跳过未知实例中的符号
            if symbol.in_unknown_instance:
                return
            symbol.is_clock = True
            # print(f"[SymbolTable] Clock signal detected: '{signal_name}' at line {lineno}")

    def _mark_async_reset(self, signal_name: str, lineno: int):
        """标记信号为异步复位"""
        symbol = self.current_scope.lookup_symbol(signal_name)
        if symbol:
            # 跳过未知实例中的符号
            if symbol.in_unknown_instance:
                return
            symbol.is_async_reset = True
            # print(f"[SymbolTable] Async reset signal detected: '{signal_name}' at line {lineno}")

    def _mark_sync_reset(self, signal_name: str, lineno: int):
        """标记信号为同步复位"""
        symbol = self.current_scope.lookup_symbol(signal_name)
        if symbol:
            # 跳过未知实例中的符号
            if symbol.in_unknown_instance:
                return
            symbol.is_sync_reset = True
            # print(f"[SymbolTable] Sync reset signal detected: '{signal_name}' at line {lineno}")

    def _process_initial(self, initial: Initial):
        """处理 initial 块"""
        scope_name = f"initial_{initial.lineno}"
        initial_scope = self.current_scope.create_child_scope(scope_name, initial)
        initial_scope.level = self.current_scope.level + 1
        self.all_scopes.append(initial_scope)

        prev_scope = self.current_scope
        self.current_scope = initial_scope

        if initial.statement:
            self._process_statement(initial.statement)

        self.current_scope = prev_scope

    def _process_statement(self, stmt):
        """处理语句"""
        if isinstance(stmt, Block):
            self._process_block(stmt)
        elif isinstance(stmt, IfStatement):
            self._process_if_statement(stmt)
        elif isinstance(stmt, CaseStatement):
            self._process_case_statement(stmt)
        elif isinstance(stmt, ForStatement):
            self._process_for_statement(stmt)
        elif isinstance(stmt, WhileStatement):
            self._process_while_statement(stmt)
        elif isinstance(stmt, Substitution):
            self._process_substitution(stmt)
        elif isinstance(stmt, NonblockingSubstitution):
            self._process_nonblocking_substitution(stmt)
        elif isinstance(stmt, Decl):
            self._process_decl(stmt)
        elif isinstance(stmt, InstanceList):
            self._process_instance_list(stmt)

    def _process_block(self, block: Block):
        """处理语句块"""
        # 如果块有名字，创建新作用域
        if block.scope:
            block_scope = self.current_scope.create_child_scope(block.scope, block)
            block_scope.level = self.current_scope.level + 1
            self.all_scopes.append(block_scope)

            prev_scope = self.current_scope
            self.current_scope = block_scope

            for stmt in block.statements:
                self._process_statement(stmt)

            self.current_scope = prev_scope
        else:
            # 无名块，直接在当前作用域处理
            for stmt in block.statements:
                self._process_statement(stmt)

    def _process_if_statement(self, if_stmt: IfStatement):
        """处理 if 语句"""
        # 为 then 分支创建作用域
        then_scope = self.current_scope.create_child_scope(f"if_then_{if_stmt.lineno}", if_stmt)
        then_scope.level = self.current_scope.level + 1
        self.all_scopes.append(then_scope)

        prev_scope = self.current_scope
        self.current_scope = then_scope

        if if_stmt.true_statement:
            self._process_statement(if_stmt.true_statement)

        self.current_scope = prev_scope

        # 为 else 分支创建作用域
        if if_stmt.false_statement:
            else_scope = self.current_scope.create_child_scope(f"if_else_{if_stmt.lineno}", if_stmt)
            else_scope.level = self.current_scope.level + 1
            self.all_scopes.append(else_scope)

            self.current_scope = else_scope
            self._process_statement(if_stmt.false_statement)
            self.current_scope = prev_scope

    def _process_case_statement(self, case_stmt: CaseStatement):
        """处理 case 语句"""
        for case in case_stmt.caselist:
            case_scope = self.current_scope.create_child_scope(f"case_{case.lineno}", case)
            case_scope.level = self.current_scope.level + 1
            self.all_scopes.append(case_scope)

            prev_scope = self.current_scope
            self.current_scope = case_scope

            if case.statement:
                self._process_statement(case.statement)

            self.current_scope = prev_scope

    def _process_for_statement(self, for_stmt: ForStatement):
        """处理 for 语句"""
        for_scope = self.current_scope.create_child_scope(f"for_{for_stmt.lineno}", for_stmt)
        for_scope.level = self.current_scope.level + 1
        self.all_scopes.append(for_scope)

        prev_scope = self.current_scope
        self.current_scope = for_scope

        # 处理循环变量初始化
        if for_stmt.pre:
            self._process_statement(for_stmt.pre)

        if for_stmt.statement:
            self._process_statement(for_stmt.statement)

        self.current_scope = prev_scope

    def _process_while_statement(self, while_stmt: WhileStatement):
        """处理 while 语句"""
        while_scope = self.current_scope.create_child_scope(f"while_{while_stmt.lineno}", while_stmt)
        while_scope.level = self.current_scope.level + 1
        self.all_scopes.append(while_scope)

        prev_scope = self.current_scope
        self.current_scope = while_scope

        if while_stmt.statement:
            self._process_statement(while_stmt.statement)

        self.current_scope = prev_scope

    def _process_substitution(self, subst: Substitution):
        """处理阻塞赋值 (=)"""
        # 左值必须是可被赋值的符号
        lvalue = subst.left
        if isinstance(lvalue, Lvalue):
            lvalue = lvalue.var

        if isinstance(lvalue, Identifier):
            symbol = self.current_scope.lookup_symbol(lvalue.name)
            if symbol is None:
                # 未声明的标识符，创建 wire 符号并标记为 reg（因为被阻塞赋值）
                symbol = create_wire_symbol(
                    lvalue.name,
                    self.current_scope.level,
                    subst.lineno,
                    lvalue
                )
                self._add_symbol(symbol)

            symbol.mark_blocking_assigned()
            symbol.mark_initialized()

        elif isinstance(lvalue, Pointer):
            # 数组元素赋值
            if isinstance(lvalue.var, Identifier):
                symbol = self.current_scope.lookup_symbol(lvalue.var.name)
                if symbol:
                    symbol.mark_blocking_assigned()

    def _process_nonblocking_substitution(self, subst: NonblockingSubstitution):
        """处理非阻塞赋值 (<=)"""
        lvalue = subst.left
        if isinstance(lvalue, Lvalue):
            lvalue = lvalue.var

        if isinstance(lvalue, Identifier):
            symbol = self.current_scope.lookup_symbol(lvalue.name)
            if symbol is None:
                symbol = create_wire_symbol(
                    lvalue.name,
                    self.current_scope.level,
                    subst.lineno,
                    lvalue
                )
                self._add_symbol(symbol)

            symbol.mark_nonblocking_assigned()
            symbol.mark_initialized()

        elif isinstance(lvalue, Pointer):
            if isinstance(lvalue.var, Identifier):
                symbol = self.current_scope.lookup_symbol(lvalue.var.name)
                if symbol:
                    symbol.mark_nonblocking_assigned()

    def _process_assign(self, assign: Assign):
        """处理连续赋值"""
        lvalue = assign.left
        if isinstance(lvalue, Lvalue):
            lvalue = lvalue.var

        if isinstance(lvalue, Identifier):
            symbol = self.current_scope.lookup_symbol(lvalue.name)
            if symbol is None:
                symbol = create_wire_symbol(
                    lvalue.name,
                    self.current_scope.level,
                    assign.lineno,
                    lvalue
                )
                self._add_symbol(symbol)

            symbol.is_assigned = True
            symbol.mark_initialized()

        # 处理右侧表达式（标记使用的信号）
        if hasattr(assign, 'right') and assign.right:
            self._process_rvalue(assign.right)

    def _process_instance_list(self, instance_list: InstanceList):
        """处理实例列表"""
        # 检查模块是否已定义
        module_name = instance_list.module
        is_unknown_module = module_name not in self.modules

        for instance in instance_list.instances:
            self._process_instance(instance, is_unknown_module)

    def _process_instance(self, instance: Instance, is_unknown_module: bool = False):
        """处理单个实例"""
        symbol = create_instance_symbol(
            instance.name,
            instance.module,
            self.current_scope.level,
            instance.lineno,
            instance
        )

        # 处理实例参数
        if hasattr(instance, 'parameterlist') and instance.parameterlist:
            params = {}
            # parameterlist 可能是 tuple 或对象
            param_list = instance.parameterlist
            if isinstance(param_list, tuple):
                param_items = param_list
            elif hasattr(param_list, 'params'):
                param_items = param_list.params
            else:
                param_items = []

            for param in param_items:
                if hasattr(param, 'paramname') and hasattr(param, 'argname'):
                    params[param.paramname] = param.argname
            symbol.instance_params = params

        # 标记未知实例
        if is_unknown_module:
            self.unknown_instance_modules.add(instance.module)
            symbol.in_unknown_instance = True

        self._add_symbol(symbol)

        # 如果是未知实例，处理端口连接并标记引用的信号
        if is_unknown_module and hasattr(instance, 'portlist') and instance.portlist:
            self._mark_signals_in_unknown_instance(instance.portlist)

    def _mark_signals_in_unknown_instance(self, portlist):
        """
        标记连接到未知实例端口的信号
        这些信号不参与时钟/复位信号的讨论
        """
        if not portlist:
            return

        ports = []
        if isinstance(portlist, (list, tuple)):
            ports = portlist
        elif hasattr(portlist, 'ports'):
            ports = portlist.ports

        for port in ports:
            # 提取端口连接的信号
            if hasattr(port, 'argname'):
                arg = port.argname
                if isinstance(arg, Identifier):
                    self._mark_symbol_unknown_instance(arg.name)
                elif hasattr(arg, 'name'):
                    self._mark_symbol_unknown_instance(arg.name)
                # 递归处理表达式中的信号（如位选择、拼接等）
                self._extract_and_mark_signals(arg)

    def _extract_and_mark_signals(self, node):
        """递归提取并标记表达式中的所有信号"""
        if node is None:
            return

        if isinstance(node, Identifier):
            self._mark_symbol_unknown_instance(node.name)
            return

        # 递归处理子节点
        if hasattr(node, 'var') and node.var:
            self._extract_and_mark_signals(node.var)
        if hasattr(node, 'left') and node.left:
            self._extract_and_mark_signals(node.left)
        if hasattr(node, 'right') and node.right:
            self._extract_and_mark_signals(node.right)
        if hasattr(node, 'msb') and node.msb:
            self._extract_and_mark_signals(node.msb)
        if hasattr(node, 'lsb') and node.lsb:
            self._extract_and_mark_signals(node.lsb)
        if hasattr(node, 'list') and node.list:
            for item in node.list:
                self._extract_and_mark_signals(item)

    def _mark_symbol_unknown_instance(self, signal_name: str):
        """标记符号为未知实例中的信号"""
        symbol = self.current_scope.lookup_symbol(signal_name)
        if symbol:
            symbol.in_unknown_instance = True

    def _process_generate(self, gen_stmt: GenerateStatement):
        """处理 generate 块"""
        gen_scope = self.current_scope.create_child_scope(f"gen_{gen_stmt.lineno}", gen_stmt)
        gen_scope.level = self.current_scope.level + 1
        self.all_scopes.append(gen_scope)

        prev_scope = self.current_scope
        self.current_scope = gen_scope

        for item in gen_stmt.items:
            # Handle IfStatement in generate blocks
            if isinstance(item, IfStatement):
                self._process_if_statement(item)
            else:
                self._process_module_item(item)

        self.current_scope = prev_scope

    def _process_function(self, func: Function):
        """处理函数定义"""
        func_scope = self.current_scope.create_child_scope(func.name, func)
        func_scope.level = self.current_scope.level + 1
        self.all_scopes.append(func_scope)

        prev_scope = self.current_scope
        self.current_scope = func_scope

        # 处理函数声明
        if hasattr(func, 'statement') and func.statement:
            self._process_statement(func.statement)

        self.current_scope = prev_scope

    def _process_task(self, task: Task):
        """处理任务定义"""
        task_scope = self.current_scope.create_child_scope(task.name, task)
        task_scope.level = self.current_scope.level + 1
        self.all_scopes.append(task_scope)

        prev_scope = self.current_scope
        self.current_scope = task_scope

        if hasattr(task, 'statement') and task.statement:
            self._process_statement(task.statement)

        self.current_scope = prev_scope

    def _add_symbol(self, symbol: Symbol) -> bool:
        """
        添加符号到当前作用域
        如果符号已存在，记录错误
        """
        if not self.current_scope.add_symbol(symbol):
            existing = self.current_scope.get_symbol(symbol.name)
            self.errors.append(
                f"Symbol '{symbol.name}' already defined at line {existing.lineno} "
                f"when trying to define at line {symbol.lineno}"
            )
            return False
        return True

    def _extract_width(self, width_node) -> Tuple[Optional[int], Optional[int]]:
        """从 Width 节点提取位宽信息"""
        if width_node is None:
            return None, None

        try:
            if hasattr(width_node, 'msb') and hasattr(width_node, 'lsb'):
                msb = self._eval_const_expr(width_node.msb)
                lsb = self._eval_const_expr(width_node.lsb)
                return msb, lsb
        except:
            pass

        return None, None

    def _extract_dimensions(self, dimensions_node) -> List[Tuple[Optional[int], Optional[int]]]:
        """从 Dimensions 节点提取数组维度信息
        返回 [(msb, lsb), ...] 列表，表示每个维度的大小
        对于 [0:3]，返回 [(0, 3)]，表示4个元素（索引0,1,2,3）
        """
        if dimensions_node is None:
            return []

        result = []
        try:
            # Dimensions 节点有 lengths 属性，是 Length 对象的列表
            if hasattr(dimensions_node, 'lengths') and dimensions_node.lengths:
                for length in dimensions_node.lengths:
                    if hasattr(length, 'msb') and hasattr(length, 'lsb'):
                        msb = self._eval_const_expr(length.msb)
                        lsb = self._eval_const_expr(length.lsb)
                        result.append((msb, lsb))
        except Exception:
            pass

        return result

    def _eval_const_expr(self, expr) -> Optional[int]:
        """
        尝试计算常量表达式的值
        返回整数值或 None
        """
        # 解包 Rvalue
        if isinstance(expr, Rvalue):
            expr = expr.var

        if isinstance(expr, IntConst):
            try:
                value = expr.value
                # 如果 value 为 None，直接返回 None
                if value is None:
                    return None
                # 处理带位宽的格式，如 16'd10000, 8'hFF, 4'b1010
                import re
                # 匹配模式: <width>'<base><value>
                width_pattern = re.match(r"(\d+)'([bodhBODH])([0-9a-fA-F_]+)", value)
                if width_pattern:
                    base_char = width_pattern.group(2).lower()
                    num_str = width_pattern.group(3).replace('_', '')
                    if num_str is None:
                        return None
                    if base_char == 'b':
                        return int(num_str, 2)
                    elif base_char == 'o':
                        return int(num_str, 8)
                    elif base_char == 'd':
                        return int(num_str, 10)
                    elif base_char == 'h':
                        return int(num_str, 16)

                # 处理不带位宽的前缀格式
                if value.startswith("'b") or value.startswith("'B"):
                    return int(value[2:], 2)
                elif value.startswith("'o") or value.startswith("'O"):
                    return int(value[2:], 8)
                elif value.startswith("'h") or value.startswith("'H"):
                    return int(value[2:], 16)
                elif value.startswith("'d") or value.startswith("'D"):
                    return int(value[2:], 10)
                else:
                    # 纯数字（可能带下划线）
                    return int(value.replace('_', ''), 0)
            except:
                return None

        elif isinstance(expr, Identifier):
            # 查找是否是常量参数
            symbol = self.current_scope.lookup_symbol(expr.name) if self.current_scope else None
            if symbol:
                # 尝试获取已计算的常量值
                if symbol.is_const():
                    const_val = symbol.get_const_value()
                    if isinstance(const_val, int):
                        return const_val
                # 对于 PARAMETER/LOCALPARAM，尝试直接计算其值表达式
                elif symbol.is_param_type():
                    # 递归计算参数的值表达式
                    if hasattr(symbol.node, 'value') and symbol.node.value is not None:
                        param_val = self._eval_const_expr(symbol.node.value)
                        if param_val is not None:
                            return param_val

        elif isinstance(expr, Operator):
            # 尝试计算简单的常量表达式
            if hasattr(expr, 'left') and hasattr(expr, 'right'):
                left_val = self._eval_const_expr(expr.left)
                right_val = self._eval_const_expr(expr.right)
                if left_val is not None and right_val is not None:
                    if isinstance(expr, Plus):
                        return left_val + right_val
                    elif isinstance(expr, Minus):
                        return left_val - right_val
                    elif isinstance(expr, Times):
                        return left_val * right_val
                    elif isinstance(expr, Divide):
                        return left_val // right_val if right_val != 0 else None
                    elif isinstance(expr, Mod):
                        return left_val % right_val if right_val != 0 else None
                    elif isinstance(expr, Power):
                        return left_val ** right_val
                    elif isinstance(expr, Sll):
                        return left_val << right_val
                    elif isinstance(expr, Srl):
                        return left_val >> right_val
                    elif isinstance(expr, Sla):
                        return left_val << right_val
                    elif isinstance(expr, Sra):
                        return left_val >> right_val

        return None

    # ============ 查询接口 ============

    def lookup(self, name: str, scope: Optional[Scope] = None) -> Optional[Symbol]:
        """
        查找符号
        如果指定 scope，从该 scope 开始查找（包括所有子作用域）；否则从当前 scope 查找
        """
        if scope is None:
            scope = self.current_scope
        if scope is None:
            return None

        # 首先在当前作用域及其父作用域中查找
        result = scope.lookup_symbol(name)
        if result is not None:
            return result

        # 如果没有找到，递归查找所有子作用域
        return self._lookup_in_children(name, scope)

    def _lookup_in_children(self, name: str, scope: Scope) -> Optional[Symbol]:
        """递归查找子作用域"""
        for child in scope.children:
            # 先在子作用域中查找
            result = child.lookup_symbol(name)
            if result is not None:
                return result
            # 递归查找孙作用域
            result = self._lookup_in_children(name, child)
            if result is not None:
                return result
        return None

    def lookup_in_module(self, module_name: str, symbol_name: str) -> Optional[Symbol]:
        """在指定模块中查找符号"""
        module_scope = self.modules.get(module_name)
        if module_scope:
            return module_scope.lookup_symbol(symbol_name)
        return None

    def get_module_scope(self, module_name: str) -> Optional[Scope]:
        """获取模块作用域"""
        return self.modules.get(module_name)

    def get_all_modules(self) -> List[str]:
        """获取所有模块名"""
        return list(self.modules.keys())

    def get_undefined_symbols(self) -> List[Symbol]:
        """获取所有未定义但使用的符号"""
        undefined = []
        for scope in self.all_scopes:
            for symbol in scope.get_all_symbols():
                if not symbol.is_initialized and not symbol.is_port():
                    undefined.append(symbol)
        return undefined

    def get_unassigned_outputs(self) -> List[Symbol]:
        """获取未赋值的输出端口"""
        unassigned = []
        for scope in self.all_scopes:
            for symbol in scope.outputs.values():
                if not symbol.is_assigned:
                    unassigned.append(symbol)
        return unassigned

    def print_symbol_table(self, scope: Optional[Scope] = None, indent: int = 0):
        """打印符号表（用于调试）"""
        if scope is None:
            scope = self.root_scope

        prefix = "  " * indent
        print(f"{prefix}Scope: {scope.name} (level={scope.level})")

        for name, symbol in scope.symbols.items():
            print(f"{prefix}  {symbol}")

        for child in scope.children:
            self.print_symbol_table(child, indent + 1)
