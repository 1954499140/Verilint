
from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

from pyverilog.vparser.ast import (
    CaseStatement, Cond, ModuleDef, Always, Assign, Identifier, NonblockingSubstitution,
    BlockingSubstitution, Lvalue, Rvalue, AlwaysFF, AlwaysComb,
    AlwaysLatch, Partselect, Pointer, Concat, Repeat,
    InstanceList, Instance, PortArg, Uplus, Uminus, Ulnot, Unot,
    Uand, Unand, Uor, Unor, Uxor, Uxnor, And, Or, Xor,
    Xnor, Sll, Srl, Sla, Sra, LessThan, GreaterThan, LessEq,
    GreaterEq, Eq, NotEq, Eql, NotEql, Land, Lor, Plus, Minus,
    Times, Divide, Mod, Power, IntConst, Initial,
    Decl, Input, Output, Inout, GenerateStatement, ForStatement,
    Wire, Reg
)

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder
from dfg_builder import DFGBuilder
from symbol import Symbol, SymbolType


def is_integer_symbol(symbol: Optional[Symbol]) -> bool:
    """检查符号是否是 integer 类型

    integer 类型在 Verilog 中会被默认驱动（初始化为0），
    因此不需要报告 "used but never driven" 错误。
    """
    if symbol is None:
        return False
    return symbol.type == SymbolType.INTEGER


class RegisterIssueType(Enum):
    """寄存器问题类型"""
    USE_BEFORE_DRIVE = "use_before_drive"      # 先使用后驱动
    DRIVE_WITHOUT_USE = "drive_without_use"    # 驱动后未使用
    MULTI_DRIVE = "multi_drive"                # 多驱动


@dataclass
class RegisterIssue:
    """寄存器问题报告"""
    issue_type: RegisterIssueType
    register_name: str
    lineno: int
    description: str
    severity: str = "warning"  # error, warning, info
    # 额外的行号信息用于重新生成描述（预处理后的行号）
    use_lineno: int = 0      # use_before_drive 中的使用行号
    drive_lineno: int = 0    # use_before_drive/drive_without_use 中的驱动行号


@dataclass
class DriveInfo:
    """驱动信息"""
    register_name: str
    lineno: int
    stmt: Any                   # 驱动语句
    driver_type: str            # 'always', 'assign', 'initial'
    driver_name: str = ""       # 驱动块名称或标识
    is_blocking: bool = False   # 是否是阻塞赋值
    is_sequential: bool = False # 是否是时序逻辑驱动
    module_name: str = ""       # 驱动所在的模块名
    in_generate: bool = False   # 是否在generate块内部
    gen_branch: str = ""        # generate分支标识（用于区分不同if分支）
    ptr_index: str = ""         # 数组索引（如 "0", "i", "" 表示非数组）
    bit_msb: Optional[int] = None   # 位选择的高位（如 7 对于 reg[7:0]）
    bit_lsb: Optional[int] = None   # 位选择的低位（如 0 对于 reg[7:0]）


class RegisterChecker:
    """
    寄存器检查器

    检测三种常见问题:
    1. use_before_drive: 寄存器在被赋值前就被使用
    2. drive_without_use: 寄存器被赋值但从未被使用
    3. multi_drive: 寄存器被多个过程块或assign驱动
    """

    def __init__(self, ast, dfg_builder: DFGBuilder, debug: bool = False):
        self.ast = ast
        self.dfg_builder = dfg_builder
        self.stb = dfg_builder.stb
        self.issues: List[RegisterIssue] = []
        self.debug = debug

        # 存储驱动信息: register_name -> List[DriveInfo]
        self.drive_map: Dict[str, List[DriveInfo]] = defaultdict(list)

        # 存储使用信息: register_name -> List[lineno]
        self.use_map: Dict[str, List[int]] = defaultdict(list)

        # 存储instance端口连接中的使用: register_name -> List[lineno]
        # 这与普通使用分开，用于drive-without-use检测但不用于use-before-drive检测
        self.instance_use_map: Dict[str, List[int]] = defaultdict(list)

        # 存储时序逻辑中的使用: register_name -> List[lineno]
        # 用于区分状态机信号（在组合逻辑驱动，在时序逻辑使用）
        self.seq_use_map: Dict[str, List[int]] = defaultdict(list)

        # 存储子模块端口信息: module_name -> {port_name: port_direction}
        # port_direction: 'input', 'output', 'inout', 'unknown'
        self.module_ports: Dict[str, Dict[str, str]] = {}
        self._collect_module_port_info()

        # 存储 generate for 循环变量（这些不应被检查）
        self.generate_for_vars: Set[str] = set()
        self._collect_generate_for_vars()

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def _collect_generate_for_vars(self):
        """
        收集 generate for 循环中的循环变量
        这些变量不应被报告为 "used but never driven"
        """
        for module in self._get_modules():
            self._collect_generate_for_vars_in_module(module)

    def _collect_generate_for_vars_in_module(self, module: ModuleDef):
        """在模块中递归收集 generate for 循环变量"""
        for item in module.items:
            if isinstance(item, GenerateStatement):
                self._collect_generate_for_vars_in_items(item.items if hasattr(item, 'items') else [])
            elif isinstance(item, ForStatement):
                self._extract_for_loop_var(item)

    def _collect_generate_for_vars_in_items(self, items):
        """递归处理 generate 块中的 items"""
        if not items:
            return
        for item in items:
            if isinstance(item, ForStatement):
                self._extract_for_loop_var(item)
            elif isinstance(item, GenerateStatement):
                self._collect_generate_for_vars_in_items(item.items if hasattr(item, 'items') else [])

    def _extract_for_loop_var(self, for_stmt: ForStatement):
        """从 ForStatement 中提取循环变量名"""
        if not hasattr(for_stmt, 'pre') or not for_stmt.pre:
            return

        pre = for_stmt.pre
        if hasattr(pre, 'left'):
            left = pre.left
            if isinstance(left, Lvalue) and hasattr(left, 'var'):
                left = left.var
            if isinstance(left, Identifier):
                var_name = left.name
                self.generate_for_vars.add(var_name)
                self._dbg(f"Found generate for loop variable: {var_name}")

    def _collect_module_port_info(self):
        """
        收集所有子模块的端口信息
        从AST中提取每个模块的端口名称和方向
        """
        for module in self._get_modules():
            module_name = module.name
            self.module_ports[module_name] = {}

            # 从端口列表提取端口名称（按位置）
            port_list = []
            if hasattr(module, 'portlist') and module.portlist:
                for i, port in enumerate(module.portlist.ports):
                    port_name = None
                    if hasattr(port, 'name'):
                        port_name = port.name
                    elif hasattr(port, 'first') and hasattr(port.first, 'name'):
                        port_name = port.first.name
                    if port_name:
                        port_list.append((i, port_name))
                        self.module_ports[module_name][port_name] = 'unknown'

            # 从模块内部的声明提取端口方向
            for item in module.items:
                if isinstance(item, Decl):
                    for decl in item.list:
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
                            self.module_ports[module_name][port_name] = direction

            # 根据声明的顺序为位置端口分配方向
            for i, port_name in port_list:
                if port_name in self.module_ports[module_name]:
                    continue  # 已经有方向信息

    def _get_module_port_direction(self, module_name: str, port_index: int, port_name: str = None) -> str:
        """
        获取模块端口的方向

        Args:
            module_name: 模块名称
            port_index: 端口位置索引
            port_name: 端口名称（如果是命名端口连接）

        Returns:
            'input', 'output', 'inout', 'unknown'
        """
        if module_name not in self.module_ports:
            return 'unknown'

        ports = self.module_ports[module_name]

        # 如果有端口名称，直接查找
        if port_name and port_name in ports:
            return ports[port_name]

        # 根据位置查找（需要知道端口列表顺序）
        # 这个信息在_collect_module_port_info中收集
        return 'unknown'

    def check(self, instance_driven_signals: List[str] = None,
              instance_used_signals: List[str] = None) -> List[RegisterIssue]:
        """
        执行所有寄存器检查

        Args:
            instance_driven_signals: 被 instance output 驱动的信号列表
            instance_used_signals: 被 instance input 使用的信号列表
        """
        self.issues = []
        self.drive_map.clear()
        self.use_map.clear()
        self.seq_use_map.clear()

        # 收集所有驱动和使用信息
        self._collect_drive_info()
        self._collect_use_info()

        # 添加 instance 端口连接中的驱动和使用信息
        if instance_driven_signals:
            for signal_name in instance_driven_signals:
                # 将被 instance 驱动的信号标记为已驱动
                self._dbg(f"Marking '{signal_name}' as driven by instance")
                # 使用一个特殊的 DriveInfo 来标记 instance 驱动
                drive_info = DriveInfo(
                    register_name=signal_name,
                    lineno=0,  # instance 没有具体行号
                    stmt=None,
                    driver_type='instance_external',  # 外部 instance 驱动
                    driver_name='instance',
                    module_name='external'
                )
                self.drive_map[signal_name].append(drive_info)

        if instance_used_signals:
            for signal_name in instance_used_signals:
                # 将被 instance 使用的信号标记为已使用
                self._dbg(f"Marking '{signal_name}' as used by instance")
                self.use_map[signal_name].append(0)  # 0 表示来自 instance

        # 检查四种问题
        self._check_never_driven()
        # self._check_use_before_drive()
        self._check_drive_without_use()
        self._check_multi_drive()

        return self.issues

    def _collect_drive_info(self):
        """收集所有寄存器驱动信息"""
        # 从AST中遍历所有模块
        for module in self._get_modules():
            self._collect_module_drive_info(module)

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

    def _collect_module_drive_info(self, module: ModuleDef):
        """收集模块内的驱动信息"""
        # 给每个always块分配一个唯一标识
        always_counter = 0
        initial_counter = 0
        instance_counter = 0
        module_name = module.name

        for item in module.items:
            if isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
                always_name = f"always_{always_counter}"
                self._collect_always_drive_info(item, always_name, module_name, in_generate=False, gen_branch="")
                always_counter += 1
            elif isinstance(item, Assign):
                self._collect_assign_drive_info(item, module_name, in_generate=False)
            elif isinstance(item, Initial):
                initial_name = f"initial_{initial_counter}"
                self._collect_initial_drive_info(item, initial_name, module_name)
                initial_counter += 1
            elif isinstance(item, (InstanceList, Instance)):
                instance_name = f"instance_{instance_counter}"
                self._collect_instance_drive_info(item, instance_name, module_name)
                instance_counter += 1
            elif isinstance(item, Decl):
                # 处理声明时带初始化的 wire，如: wire x = expr;
                self._collect_decl_drive_info(item, module_name)
            elif isinstance(item, GenerateStatement):
                # generate 块中的驱动
                if hasattr(item, 'items') and item.items:
                    for gen_item in item.items:
                        self._collect_generate_item_drive_info(gen_item, module_name)

    def _collect_always_drive_info(self, always_node, always_name: str, module_name: str = "",
                                   in_generate: bool = False, gen_branch: str = ""):
        """收集always块内的驱动信息"""
        driver_type = 'always'
        is_sequential = False
        if isinstance(always_node, AlwaysFF):
            driver_type = 'always_ff'
            is_sequential = True
        elif isinstance(always_node, AlwaysComb):
            driver_type = 'always_comb'
        elif isinstance(always_node, AlwaysLatch):
            driver_type = 'always_latch'
        else:
            # 检查是否是时序逻辑（通过检查敏感列表是否有posedge/negedge）
            is_sequential = self._is_sequential_always(always_node)
            if is_sequential:
                driver_type = 'always_seq'

        # 递归查找所有赋值语句
        assignments = self._find_assignments(always_node.statement)

        for stmt, is_blocking in assignments:
            # 使用带索引的目标提取
            targets_with_index = self._get_assignment_targets_with_index(stmt)
            for reg_name, ptr_index, bit_msb, bit_lsb in targets_with_index:
                drive_info = DriveInfo(
                    register_name=reg_name,
                    lineno=stmt.lineno,
                    stmt=stmt,
                    driver_type=driver_type,
                    driver_name=always_name,
                    is_blocking=is_blocking,
                    is_sequential=is_sequential,
                    module_name=module_name,
                    in_generate=in_generate,
                    gen_branch=gen_branch,
                    ptr_index=ptr_index,
                    bit_msb=bit_msb,
                    bit_lsb=bit_lsb
                )
                self.drive_map[reg_name].append(drive_info)
                self._dbg(f"Found drive: {reg_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {stmt.lineno} in {always_name} (seq={is_sequential}, gen={in_generate}, branch={gen_branch})")

    def _collect_generate_for_drive_info(self, for_stmt: ForStatement, module_name: str = "",
                                         gen_branch: str = ""):
        """
        尝试展开 generate for 循环来收集驱动信息
        对于形如: for (i = 1; i < NUMBER_OF_STAGES-1; i = i + 1) 的循环
        尝试计算每次迭代的位范围
        """
        # 提取循环变量信息
        loop_var = self._extract_loop_variable(for_stmt)
        if not loop_var:
            # 无法提取循环变量，回退到普通处理
            if hasattr(for_stmt, 'statement') and for_stmt.statement:
                self._collect_generate_item_drive_info(for_stmt.statement, module_name,
                                                      in_generate=True, gen_branch=gen_branch)
            return

        # 尝试计算循环范围
        loop_range = self._compute_loop_range(for_stmt, loop_var)
        if not loop_range:
            # 无法计算循环范围，回退到普通处理
            if hasattr(for_stmt, 'statement') and for_stmt.statement:
                self._collect_generate_item_drive_info(for_stmt.statement, module_name,
                                                      in_generate=True, gen_branch=gen_branch)
            return

        start_val, end_val, step = loop_range
        self._dbg(f"Generate for loop: {loop_var} from {start_val} to {end_val} step {step}")

        # 对每次迭代，用具体值替换循环变量，然后收集驱动
        for i in range(start_val, end_val, step):
            # 创建分支标识，包含迭代值
            iter_branch = f"{gen_branch}:for_{loop_var}={i}" if gen_branch else f"for_{loop_var}={i}"
            # 收集这次迭代的驱动，传入循环变量值用于位范围计算
            self._collect_generate_for_iteration(for_stmt.statement, module_name, iter_branch, loop_var, i)

    def _extract_loop_variable(self, for_stmt: ForStatement) -> Optional[str]:
        """从 for 语句中提取循环变量名"""
        if not hasattr(for_stmt, 'pre') or not for_stmt.pre:
            return None

        pre = for_stmt.pre
        # pre 可能是 BlockingSubstitution (i = 0)
        if isinstance(pre, BlockingSubstitution):
            lval = pre.left
            if isinstance(lval, Lvalue) and hasattr(lval, 'var'):
                lval = lval.var
            if isinstance(lval, Identifier):
                return lval.name
        # 也可能是直接的 Lvalue
        elif isinstance(pre, Lvalue) and hasattr(pre, 'var'):
            if isinstance(pre.var, Identifier):
                return pre.var.name
        # 或者通过 left 属性访问
        elif hasattr(pre, 'left'):
            left = pre.left
            if isinstance(left, Lvalue) and hasattr(left, 'var'):
                left = left.var
            if isinstance(left, Identifier):
                return left.name
        return None

    def _compute_loop_range(self, for_stmt: ForStatement, loop_var: str) -> Optional[Tuple[int, int, int]]:
        """
        尝试计算 generate for 循环的范围
        返回: (start, end, step) 或 None
        """
        try:
            # 提取初始值: i = start
            start_val = None
            if hasattr(for_stmt, 'pre') and for_stmt.pre:
                pre = for_stmt.pre
                # pre 是 BlockingSubstitution，right 是 Rvalue
                if isinstance(pre, BlockingSubstitution):
                    if hasattr(pre, 'right'):
                        rval = pre.right
                        if isinstance(rval, Rvalue) and hasattr(rval, 'var'):
                            start_val = self._eval_const_expr(rval.var)
                        else:
                            start_val = self._eval_const_expr(rval)
                elif hasattr(pre, 'right'):
                    start_val = self._eval_const_expr(pre.right)

            # 提取结束条件: i < end 或 i <= end
            end_val = None
            if hasattr(for_stmt, 'cond') and for_stmt.cond:
                cond = for_stmt.cond
                # 处理 i < end 或 end > i
                if isinstance(cond, LessThan):
                    if hasattr(cond, 'left') and isinstance(cond.left, Identifier) and cond.left.name == loop_var:
                        end_val = self._eval_const_expr(cond.right)
                    elif hasattr(cond, 'right') and isinstance(cond.right, Identifier) and cond.right.name == loop_var:
                        end_val = self._eval_const_expr(cond.left)
                elif isinstance(cond, LessEq):
                    if hasattr(cond, 'left') and isinstance(cond.left, Identifier) and cond.left.name == loop_var:
                        end_val = self._eval_const_expr(cond.right)
                        if end_val is not None:
                            end_val += 1  # <= 需要 +1
                    elif hasattr(cond, 'right') and isinstance(cond.right, Identifier) and cond.right.name == loop_var:
                        end_val = self._eval_const_expr(cond.left)
                        if end_val is not None:
                            end_val += 1

            # 提取步进: i = i + step
            step_val = 1
            if hasattr(for_stmt, 'post') and for_stmt.post:
                post = for_stmt.post
                # post 是 BlockingSubstitution，需要获取 right 中的表达式
                post_expr = None
                if isinstance(post, BlockingSubstitution) and hasattr(post, 'right'):
                    rval = post.right
                    if isinstance(rval, Rvalue) and hasattr(rval, 'var'):
                        post_expr = rval.var
                    else:
                        post_expr = rval
                elif hasattr(post, 'right'):
                    post_expr = post.right

                # 处理 i = i + 1 或 i = i - 1
                if isinstance(post_expr, Plus):
                    if hasattr(post_expr, 'right'):
                        step = self._eval_const_expr(post_expr.right)
                        if step is not None:
                            step_val = step
                elif isinstance(post_expr, Minus):
                    if hasattr(post_expr, 'right'):
                        step = self._eval_const_expr(post_expr.right)
                        if step is not None:
                            step_val = -step

            if start_val is not None and end_val is not None:
                return (start_val, end_val, step_val)

        except Exception as e:
            self._dbg(f"Error computing loop range: {e}")

        return None

    def _collect_generate_for_iteration(self, stmt, module_name: str, gen_branch: str,
                                        loop_var: str, loop_value: int):
        """
        收集 generate for 单次迭代的驱动信息
        用 loop_value 替换 loop_var 来计算位范围
        """
        if stmt is None:
            return

        if isinstance(stmt, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
            always_name = f"always_gen_{stmt.lineno}_{loop_var}={loop_value}"
            self._collect_always_drive_info_gen(stmt, always_name, module_name,
                                               in_generate=True, gen_branch=gen_branch,
                                               loop_var=loop_var, loop_value=loop_value)
        elif isinstance(stmt, Assign):
            self._collect_assign_drive_info_gen(stmt, module_name, gen_branch, loop_var, loop_value)
        elif hasattr(stmt, 'statements'):
            for s in stmt.statements:
                self._collect_generate_for_iteration(s, module_name, gen_branch, loop_var, loop_value)
        elif hasattr(stmt, 'true_statement') or hasattr(stmt, 'false_statement'):
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                self._collect_generate_for_iteration(stmt.true_statement, module_name, gen_branch, loop_var, loop_value)
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                self._collect_generate_for_iteration(stmt.false_statement, module_name, gen_branch, loop_var, loop_value)

    def _collect_always_drive_info_gen(self, always_node, always_name: str, module_name: str = "",
                                       in_generate: bool = False, gen_branch: str = "",
                                       loop_var: str = "", loop_value: int = 0):
        """收集 generate for 中的 always 块驱动信息，支持循环变量替换"""
        driver_type = 'always'
        is_sequential = False
        if isinstance(always_node, AlwaysFF):
            driver_type = 'always_ff'
            is_sequential = True
        elif isinstance(always_node, AlwaysComb):
            driver_type = 'always_comb'
        elif isinstance(always_node, AlwaysLatch):
            driver_type = 'always_latch'
        else:
            is_sequential = self._is_sequential_always(always_node)
            if is_sequential:
                driver_type = 'always_seq'

        assignments = self._find_assignments(always_node.statement)

        for stmt, is_blocking in assignments:
            # 在提取目标时替换循环变量
            targets = self._get_assignment_targets_with_index_gen(stmt, loop_var, loop_value)
            for reg_name, ptr_index, bit_msb, bit_lsb in targets:
                drive_info = DriveInfo(
                    register_name=reg_name,
                    lineno=stmt.lineno,
                    stmt=stmt,
                    driver_type=driver_type,
                    driver_name=always_name,
                    is_blocking=is_blocking,
                    is_sequential=is_sequential,
                    module_name=module_name,
                    in_generate=in_generate,
                    gen_branch=gen_branch,
                    ptr_index=ptr_index,
                    bit_msb=bit_msb,
                    bit_lsb=bit_lsb
                )
                self.drive_map[reg_name].append(drive_info)
                self._dbg(f"Found gen-for drive: {reg_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {stmt.lineno} ({loop_var}={loop_value})")

    def _collect_assign_drive_info_gen(self, assign_node: Assign, module_name: str,
                                       gen_branch: str, loop_var: str, loop_value: int):
        """收集 generate for 中的 assign 驱动信息，支持循环变量替换"""
        targets = self._get_assignment_targets_with_index_gen(assign_node, loop_var, loop_value)
        for reg_name, ptr_index, bit_msb, bit_lsb in targets:
            drive_info = DriveInfo(
                register_name=reg_name,
                lineno=assign_node.lineno,
                stmt=assign_node,
                driver_type='assign',
                driver_name='continuous',
                is_blocking=False,
                module_name=module_name,
                in_generate=True,
                gen_branch=gen_branch,
                ptr_index=ptr_index,
                bit_msb=bit_msb,
                bit_lsb=bit_lsb
            )
            self.drive_map[reg_name].append(drive_info)

    def _get_assignment_targets_with_index_gen(self, stmt, loop_var: str, loop_value: int):
        """
        获取赋值语句的目标，支持循环变量替换
        例如: pipe[BIT_WIDTH*(i+1)-1:BIT_WIDTH*i] 当 i=1 时变成 pipe[BIT_WIDTH*2-1:BIT_WIDTH]
        """
        targets = []

        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_lvals_with_index_gen(lval, loop_var, loop_value))

        elif isinstance(stmt, Assign):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_lvals_with_index_gen(lval, loop_var, loop_value))

        return targets

    def _extract_lvals_with_index_gen(self, lval, loop_var: str, loop_value: int):
        """提取左值目标，支持循环变量替换"""
        results = []

        if isinstance(lval, Identifier):
            results.append((lval.name, "", None, None))

        elif isinstance(lval, (Partselect, Pointer)):
            var_name, ptr_index, bit_msb, bit_lsb = self._extract_lval_with_index_gen(lval, loop_var, loop_value)
            if var_name:
                results.append((var_name, ptr_index, bit_msb, bit_lsb))

        elif isinstance(lval, Concat):
            if hasattr(lval, 'list') and lval.list:
                for item in lval.list:
                    results.extend(self._extract_lvals_with_index_gen(item, loop_var, loop_value))

        return results

    def _extract_lval_with_index_gen(self, lval, loop_var: str, loop_value: int):
        """提取左值信息，支持循环变量替换"""
        if lval is None:
            return None, "", None, None

        if isinstance(lval, Identifier):
            return lval.name, "", None, None

        elif isinstance(lval, Pointer):
            var_name = ""
            ptr_str = ""
            bit_msb = None
            bit_lsb = None
            if isinstance(lval.var, Identifier):
                var_name = lval.var.name
            elif isinstance(lval.var, (Partselect, Pointer)):
                var_name, _, bit_msb, bit_lsb = self._extract_lval_with_index_gen(lval.var, loop_var, loop_value)

            if lval.ptr:
                # 替换循环变量后计算索引
                ptr_str = self._expr_to_string_gen(lval.ptr, loop_var, loop_value)
            return var_name, ptr_str, bit_msb, bit_lsb

        elif isinstance(lval, Partselect):
            # 替换循环变量后计算位范围
            msb_val = self._eval_const_expr_gen(lval.msb, loop_var, loop_value)
            lsb_val = self._eval_const_expr_gen(lval.lsb, loop_var, loop_value)

            if isinstance(lval.var, Identifier):
                return lval.var.name, "", msb_val, lsb_val
            elif isinstance(lval.var, Pointer):
                var_name, ptr_str, _, _ = self._extract_lval_with_index_gen(lval.var, loop_var, loop_value)
                return var_name, ptr_str, msb_val, lsb_val
            else:
                var_name, ptr_str, _, _ = self._extract_lval_with_index_gen(lval.var, loop_var, loop_value)
                return var_name, ptr_str, msb_val, lsb_val

        elif isinstance(lval, Concat):
            if hasattr(lval, 'list') and lval.list:
                return self._extract_lval_with_index_gen(lval.list[0], loop_var, loop_value)
            return None, "", None, None

        return None, "", None, None

    def _eval_const_expr_gen(self, expr, loop_var: str, loop_value: int) -> Optional[int]:
        """计算表达式值，支持循环变量替换"""
        if expr is None:
            return None

        # 解包 Rvalue
        if isinstance(expr, Rvalue):
            expr = expr.var

        # 替换循环变量
        if isinstance(expr, Identifier) and expr.name == loop_var:
            return loop_value

        if isinstance(expr, IntConst):
            val_str = str(expr.value).strip()
            try:
                if "'d" in val_str.lower():
                    return int(val_str.split("'d")[-1], 10)
                elif "'h" in val_str.lower():
                    return int(val_str.split("'h")[-1], 16)
                elif "'b" in val_str.lower():
                    return int(val_str.split("'b")[-1], 2)
                elif "'o" in val_str.lower():
                    return int(val_str.split("'o")[-1], 8)
                else:
                    return int(val_str, 10)
            except ValueError:
                return None

        elif isinstance(expr, Plus):
            left = self._eval_const_expr_gen(expr.left, loop_var, loop_value)
            right = self._eval_const_expr_gen(expr.right, loop_var, loop_value)
            if left is not None and right is not None:
                return left + right

        elif isinstance(expr, Minus):
            left = self._eval_const_expr_gen(expr.left, loop_var, loop_value)
            right = self._eval_const_expr_gen(expr.right, loop_var, loop_value)
            if left is not None and right is not None:
                return left - right

        elif isinstance(expr, Times):
            left = self._eval_const_expr_gen(expr.left, loop_var, loop_value)
            right = self._eval_const_expr_gen(expr.right, loop_var, loop_value)
            if left is not None and right is not None:
                return left * right

        elif isinstance(expr, Divide):
            left = self._eval_const_expr_gen(expr.left, loop_var, loop_value)
            right = self._eval_const_expr_gen(expr.right, loop_var, loop_value)
            if left is not None and right is not None and right != 0:
                return left // right

        elif isinstance(expr, Mod):
            left = self._eval_const_expr_gen(expr.left, loop_var, loop_value)
            right = self._eval_const_expr_gen(expr.right, loop_var, loop_value)
            if left is not None and right is not None and right != 0:
                return left % right

        elif isinstance(expr, Identifier):
            # 首先检查是否是循环变量
            if expr.name == loop_var:
                return loop_value
            # 然后查找符号表
            symbol = self._lookup_symbol(expr.name)
            if symbol and symbol.is_param_type():
                if hasattr(symbol, 'param_value') and symbol.param_value is not None:
                    return self._eval_const_expr_gen(symbol.param_value, loop_var, loop_value)
            if symbol and hasattr(symbol, 'value') and symbol.value is not None:
                return self._eval_const_expr_gen(symbol.value, loop_var, loop_value)

        return None

    def _expr_to_string_gen(self, expr, loop_var: str, loop_value: int) -> str:
        """将表达式转换为字符串，支持循环变量替换"""
        if expr is None:
            return ""

        if isinstance(expr, Identifier):
            if expr.name == loop_var:
                return str(loop_value)
            return expr.name

        elif isinstance(expr, IntConst):
            return str(expr.value)

        elif isinstance(expr, Pointer):
            base = self._expr_to_string_gen(expr.var, loop_var, loop_value)
            idx = self._expr_to_string_gen(expr.ptr, loop_var, loop_value)
            return f"{base}[{idx}]"

        elif isinstance(expr, Partselect):
            base = self._expr_to_string_gen(expr.var, loop_var, loop_value)
            msb = self._eval_const_expr_gen(expr.msb, loop_var, loop_value)
            lsb = self._eval_const_expr_gen(expr.lsb, loop_var, loop_value)
            if msb is not None and lsb is not None:
                return f"{base}[{msb}:{lsb}]"
            msb_str = self._expr_to_string_gen(expr.msb, loop_var, loop_value)
            lsb_str = self._expr_to_string_gen(expr.lsb, loop_var, loop_value)
            return f"{base}[{msb_str}:{lsb_str}]"

        elif isinstance(expr, Plus):
            left = self._expr_to_string_gen(expr.left, loop_var, loop_value)
            right = self._expr_to_string_gen(expr.right, loop_var, loop_value)
            return f"{left}+{right}"

        elif isinstance(expr, Minus):
            left = self._expr_to_string_gen(expr.left, loop_var, loop_value)
            right = self._expr_to_string_gen(expr.right, loop_var, loop_value)
            return f"{left}-{right}"

        elif isinstance(expr, Times):
            left = self._expr_to_string_gen(expr.left, loop_var, loop_value)
            right = self._expr_to_string_gen(expr.right, loop_var, loop_value)
            return f"{left}*{right}"

        else:
            if hasattr(expr, 'name'):
                return expr.name
            if hasattr(expr, 'value'):
                return str(expr.value)
            return str(expr)

    def _collect_generate_item_drive_info(self, item, module_name: str = "",
                                          in_generate: bool = True, gen_branch: str = ""):
        """递归收集generate块中的驱动信息"""
        if item is None:
            return

        if isinstance(item, Assign):
            self._collect_assign_drive_info(item, module_name, in_generate=True, gen_branch=gen_branch)
        elif isinstance(item, Decl):
            # 处理generate块中的声明（如 wire x = expr;）
            self._collect_decl_drive_info(item, module_name)
        elif isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
            # Always块在generate中也需要收集驱动
            always_name = f"always_gen_{item.lineno}"
            self._collect_always_drive_info(item, always_name, module_name,
                                           in_generate=True, gen_branch=gen_branch)
        elif isinstance(item, Initial):
            initial_name = f"initial_gen_{item.lineno}"
            self._collect_initial_drive_info(item, initial_name, module_name)
        elif isinstance(item, (InstanceList, Instance)):
            self._collect_instance_drive_info(item, f"instance_gen_{item.lineno}", module_name)
        elif isinstance(item, ForStatement):
            # 尝试展开 generate for 循环来计算位范围
            self._collect_generate_for_drive_info(item, module_name, gen_branch)
        elif isinstance(item, GenerateStatement):
            if hasattr(item, 'items') and item.items:
                for gen_item in item.items:
                    self._collect_generate_item_drive_info(gen_item, module_name,
                                                          in_generate=True, gen_branch=gen_branch)
        elif hasattr(item, 'true_statement') or hasattr(item, 'false_statement'):
            # generate if 语句 - 为true/false分支创建不同的标识
            branch_id = f"if_{item.lineno}"
            if hasattr(item, 'true_statement') and item.true_statement:
                true_branch = f"{gen_branch}:{branch_id}_true" if gen_branch else f"{branch_id}_true"
                self._collect_generate_item_drive_info(item.true_statement, module_name,
                                                      in_generate=True, gen_branch=true_branch)
            if hasattr(item, 'false_statement') and item.false_statement:
                false_branch = f"{gen_branch}:{branch_id}_false" if gen_branch else f"{branch_id}_false"
                self._collect_generate_item_drive_info(item.false_statement, module_name,
                                                      in_generate=True, gen_branch=false_branch)
        elif isinstance(item, CaseStatement):
            # generate case 语句
            for case_item in item.caselist:
                if case_item.statement:
                    branch_id = f"case_{item.lineno}_{case_item.lineno}"
                    case_branch = f"{gen_branch}:{branch_id}" if gen_branch else branch_id
                    self._collect_generate_item_drive_info(case_item.statement, module_name,
                                                          in_generate=True, gen_branch=case_branch)
        elif hasattr(item, 'statements'):
            # 块语句
            for stmt in item.statements:
                self._collect_generate_item_drive_info(stmt, module_name,
                                                      in_generate=True, gen_branch=gen_branch)
        elif hasattr(item, 'items'):
            # 某些块类型使用items而不是statements
            for sub_item in item.items:
                self._collect_generate_item_drive_info(sub_item, module_name,
                                                      in_generate=True, gen_branch=gen_branch)

    def _is_sequential_always(self, always_node) -> bool:
        """检查always块是否是时序逻辑（有posedge/negedge）"""
        if not hasattr(always_node, 'sens_list') or not always_node.sens_list:
            return False

        sens_list = always_node.sens_list
        if hasattr(sens_list, 'list'):
            for sens in sens_list.list:
                if hasattr(sens, 'type'):
                    # type可以是'posedge'或'negedge'
                    if sens.type in ('posedge', 'negedge'):
                        return True
        return False

    def _collect_assign_drive_info(self, assign_node: Assign, module_name: str = "",
                                   in_generate: bool = False, gen_branch: str = ""):
        """收集assign语句的驱动信息"""
        targets = self._get_assignment_targets_with_index(assign_node)
        for reg_name, ptr_index, bit_msb, bit_lsb in targets:
            drive_info = DriveInfo(
                register_name=reg_name,
                lineno=assign_node.lineno,
                stmt=assign_node,
                driver_type='assign',
                driver_name='continuous',
                is_blocking=False,
                module_name=module_name,
                in_generate=in_generate,
                gen_branch=gen_branch,
                ptr_index=ptr_index,
                bit_msb=bit_msb,
                bit_lsb=bit_lsb
            )
            self.drive_map[reg_name].append(drive_info)
            self._dbg(f"Found assign drive: {reg_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {assign_node.lineno} (in_generate={in_generate}, branch={gen_branch!r})")

    def _collect_decl_drive_info(self, decl_node: Decl, module_name: str = ""):
        """
        收集声明语句中的驱动信息
        处理 wire 声明时带初始化的情况，如: wire x = expr;
        Pyverilog 将这种语法解析为 Decl 中包含 Wire 和 Assign 两个节点
        """
        if not hasattr(decl_node, 'list') or not decl_node.list:
            return

        # 首先收集所有 wire 名称（用于过滤）
        wire_names = set()
        for decl_item in decl_node.list:
            if isinstance(decl_item, Wire):
                wire_names.add(decl_item.name)

        # 然后处理 Decl 中的 Assign 节点（这些是 wire 声明时的赋值）
        for decl_item in decl_node.list:
            if isinstance(decl_item, Assign):
                # 这是 wire 声明时的连续赋值
                targets = self._get_assignment_targets_with_index(decl_item)
                for reg_name, ptr_index, bit_msb, bit_lsb in targets:
                    drive_info = DriveInfo(
                        register_name=reg_name,
                        lineno=decl_item.lineno,
                        stmt=decl_item,
                        driver_type='assign',  # 声明时赋值等效于 assign
                        driver_name='decl_init',
                        is_blocking=False,
                        module_name=module_name,
                        ptr_index=ptr_index,
                        bit_msb=bit_msb,
                        bit_lsb=bit_lsb
                    )
                    self.drive_map[reg_name].append(drive_info)
                    self._dbg(f"Found decl drive: {reg_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {decl_item.lineno}")

    def _collect_initial_drive_info(self, initial_node: Initial, initial_name: str, module_name: str = ""):
        """收集initial块内的驱动信息"""
        # 递归查找所有赋值语句
        assignments = self._find_assignments(initial_node.statement)

        for stmt, is_blocking in assignments:
            targets = self._get_assignment_targets_with_index(stmt)
            for reg_name, ptr_index, bit_msb, bit_lsb in targets:
                drive_info = DriveInfo(
                    register_name=reg_name,
                    lineno=stmt.lineno,
                    stmt=stmt,
                    driver_type='initial',
                    driver_name=initial_name,
                    is_blocking=is_blocking,
                    module_name=module_name,
                    ptr_index=ptr_index,
                    bit_msb=bit_msb,
                    bit_lsb=bit_lsb
                )
                self.drive_map[reg_name].append(drive_info)
                self._dbg(f"Found drive: {reg_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {stmt.lineno} in {initial_name}")

    def _collect_instance_drive_info(self, instance, instance_name: str, module_name: str = ""):
        """
        收集模块实例化中的驱动信息
        实例的输出端口会驱动连接的信号
        """
        if isinstance(instance, InstanceList):
            for inst in instance.instances:
                self._collect_single_instance_drive_info(inst, instance_name, module_name)
        elif isinstance(instance, Instance):
            self._collect_single_instance_drive_info(instance, instance_name, module_name)

    def _collect_single_instance_drive_info(self, instance: Instance, instance_name: str, module_name: str = ""):
        """
        收集单个实例的输出端口驱动信息

        对于寄存器检查：只要信号连接到实例化端口作为被驱动目标（即作为output连接），
        就视为已被驱动。如果子模块定义可用，会根据端口方向精确判断；
        如果不可用，会尝试根据端口位置或名称推断，或保守地将所有端口连接都视为驱动。
        """
        module_name = instance.module
        port_info = self.module_ports.get(module_name, {})

        # 处理端口连接
        if hasattr(instance, 'portlist') and instance.portlist:
            for port in instance.portlist:
                if isinstance(port, PortArg) and port.argname:
                    port_name = port.portname
                    should_mark_as_driven = False

                    # 确定是否应该标记为被驱动
                    if port_name and port_name in port_info:
                        # 有子模块定义，根据端口方向判断
                        direction = port_info[port_name]
                        if direction in ('output', 'inout'):
                            should_mark_as_driven = True
                    else:
                        # 没有子模块定义，尝试推断
                        # 策略1: 端口名包含 output/out 等关键字
                        if port_name and any(keyword in port_name.lower() for keyword in ['out', 'dout', 'q', 'data_out', 'result']):
                            should_mark_as_driven = True
                        # 策略2: 如果连接的是简单标识符或数组元素（而非表达式），
                        # 且该信号在模块内没有被其他方式驱动，则可能是 output
                        # 这里我们保守处理：标记所有端口连接为潜在驱动
                        # 这是为了避免漏报 use_before_drive 错误
                        else:
                            # 检查连接的是否是可写的信号（Identifier 或 Pointer）
                            arg = port.argname
                            if isinstance(arg, (Identifier, Pointer, Partselect)):
                                should_mark_as_driven = True
                            elif isinstance(arg, Concat):
                                # 拼接中的所有元素都可能是被驱动的
                                should_mark_as_driven = True

                    if should_mark_as_driven:
                        # 从表达式中提取被驱动的信号（带索引）
                        targets_with_index = self._extract_lvals_with_index(port.argname)
                        for target_name, ptr_index, bit_msb, bit_lsb in targets_with_index:
                            drive_info = DriveInfo(
                                register_name=target_name,
                                lineno=instance.lineno,
                                stmt=instance,
                                driver_type='instance',
                                driver_name=instance_name,
                                is_blocking=False,
                                module_name=module_name,
                                ptr_index=ptr_index,
                                bit_msb=bit_msb,
                                bit_lsb=bit_lsb
                            )
                            self.drive_map[target_name].append(drive_info)
                            self._dbg(f"Found instance drive: {target_name}[{ptr_index}][{bit_msb}:{bit_lsb}] at line {instance.lineno} from {instance.module}.{port_name}")

    def _extract_identifiers_from_expr(self, expr) -> List[str]:
        """从表达式中提取所有标识符名"""
        identifiers = []
        if expr is None:
            return identifiers

        if isinstance(expr, Identifier):
            identifiers.append(expr.name)
        elif isinstance(expr, (Partselect, Pointer)):
            if isinstance(expr.var, Identifier):
                identifiers.append(expr.var.name)
        elif isinstance(expr, Concat):
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    identifiers.extend(self._extract_identifiers_from_expr(item))

        elif isinstance(expr, Cond):
            # 处理三元条件运算符: cond ? true_value : false_value
            if hasattr(expr, 'cond') and expr.cond:
                identifiers.extend(self._extract_identifiers_from_expr(expr.cond))
            if hasattr(expr, 'true_value') and expr.true_value:
                identifiers.extend(self._extract_identifiers_from_expr(expr.true_value))
            if hasattr(expr, 'false_value') and expr.false_value:
                identifiers.extend(self._extract_identifiers_from_expr(expr.false_value))

        elif isinstance(expr, Repeat):
            # 处理重复操作: {4{a}}
            if hasattr(expr, 'value') and expr.value:
                identifiers.extend(self._extract_identifiers_from_expr(expr.value))
            if hasattr(expr, 'times') and expr.times:
                identifiers.extend(self._extract_identifiers_from_expr(expr.times))

        else:
            # 通用递归处理
            for attr_name in ['var', 'left', 'right', 'ptr', 'msb', 'lsb']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                identifiers.extend(self._extract_identifiers_from_expr(item))
                        elif hasattr(attr_val, '__dict__'):
                            identifiers.extend(self._extract_identifiers_from_expr(attr_val))
        return identifiers

    def _find_assignments(self, stmt) -> List[Tuple[Any, bool]]:
        """
        递归查找语句中的所有赋值
        返回: [(stmt, is_blocking), ...]
        """
        assignments = []

        if stmt is None:
            return assignments

        # 阻塞赋值 (=)
        if isinstance(stmt, BlockingSubstitution):
            assignments.append((stmt, True))

        # 非阻塞赋值 (<=)
        elif isinstance(stmt, NonblockingSubstitution):
            assignments.append((stmt, False))

        # 块语句 - 递归处理
        elif hasattr(stmt, 'statements'):
            for s in stmt.statements:
                assignments.extend(self._find_assignments(s))

        # if语句 - 递归处理两个分支
        elif hasattr(stmt, 'true_statement') or hasattr(stmt, 'false_statement'):
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                assignments.extend(self._find_assignments(stmt.true_statement))
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                assignments.extend(self._find_assignments(stmt.false_statement))

        # case语句 - 递归处理所有分支
        elif hasattr(stmt, 'caselist'):
            for case in stmt.caselist:
                if case.statement:
                    assignments.extend(self._find_assignments(case.statement))

        return assignments

    def _get_assignment_targets(self, stmt) -> List[str]:
        """
        获取赋值语句的所有目标变量名
        支持多个目标，如: {a, b} = 2'b00;
        """
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

    def _get_assignment_targets_with_index(self, stmt) -> List[Tuple[str, str, Optional[int], Optional[int]]]:
        """
        获取赋值语句的所有目标变量名、数组索引和位范围
        支持多个目标，如: {a, b} = 2'b00;
        返回: [(变量名, 索引字符串, 位高位, 位低位), ...]
        """
        targets = []

        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_lvals_with_index(lval))

        elif isinstance(stmt, Assign):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            targets.extend(self._extract_lvals_with_index(lval))

        return targets

    def _extract_lvals_with_index(self, lval) -> List[Tuple[str, str, Optional[int], Optional[int]]]:
        """
        从左值表达式中提取所有变量名、数组索引和位范围
        支持拼接: {a, b, c} = value;
        返回: [(变量名, 索引字符串, 位高位, 位低位), ...]
        """
        results = []

        if isinstance(lval, Identifier):
            results.append((lval.name, "", None, None))

        elif isinstance(lval, (Partselect, Pointer)):
            var_name, ptr_index, bit_msb, bit_lsb = self._extract_lval_with_index(lval)
            if var_name:
                results.append((var_name, ptr_index, bit_msb, bit_lsb))

        elif isinstance(lval, Concat):
            # 处理拼接赋值: {a, b, c} = value;
            if hasattr(lval, 'list') and lval.list:
                for item in lval.list:
                    results.extend(self._extract_lvals_with_index(item))

        return results

    def _extract_identifiers_from_lval(self, lval) -> List[str]:
        """从左值表达式中提取所有标识符名"""
        identifiers = []

        if isinstance(lval, Identifier):
            identifiers.append(lval.name)
        elif isinstance(lval, (Partselect, Pointer)):
            if isinstance(lval.var, Identifier):
                identifiers.append(lval.var.name)
        elif isinstance(lval, Concat):
            # 处理拼接赋值: {a, b, c} = value;
            if hasattr(lval, 'list') and lval.list:
                for item in lval.list:
                    identifiers.extend(self._extract_identifiers_from_lval(item))

        return identifiers

    def _get_assignment_target(self, stmt) -> Optional[str]:
        """获取赋值语句的目标变量名（向后兼容，只返回第一个）"""
        targets = self._get_assignment_targets(stmt)
        return targets[0] if targets else None

    def _get_assignment_target_with_index(self, stmt) -> Tuple[Optional[str], str, Optional[int], Optional[int]]:
        """
        获取赋值语句的目标变量名、数组索引和位范围
        返回: (变量名, 索引字符串, 位高位, 位低位)
        索引字符串: 如果是数组索引，返回索引的字符串表示；如果不是数组，返回 ""
        """
        if stmt is None:
            return None, "", None, None

        lval = None
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
        elif isinstance(stmt, Assign):
            lval = stmt.left

        if lval is None:
            return None, "", None, None

        if isinstance(lval, Lvalue):
            lval = lval.var

        return self._extract_lval_with_index(lval)

    def _extract_lval_with_index(self, lval) -> Tuple[Optional[str], str, Optional[int], Optional[int]]:
        """
        从左值表达式中提取变量名、数组索引和位选择范围
        返回: (变量名, 索引字符串, 位高位, 位低位)
        位选择示例: reg[7:0] -> ("reg", "", 7, 0)
        """
        if lval is None:
            return None, "", None, None

        if isinstance(lval, Identifier):
            return lval.name, "", None, None

        elif isinstance(lval, Pointer):
            # 数组索引: array[idx]
            var_name = ""
            ptr_str = ""
            bit_msb = None
            bit_lsb = None
            if isinstance(lval.var, Identifier):
                var_name = lval.var.name
            elif isinstance(lval.var, (Partselect, Pointer)):
                var_name, _, bit_msb, bit_lsb = self._extract_lval_with_index(lval.var)

            # 提取索引的字符串表示
            if lval.ptr:
                ptr_str = self._expr_to_string(lval.ptr)
            return var_name, ptr_str, bit_msb, bit_lsb

        elif isinstance(lval, Partselect):
            # 位选择: reg[7:0]
            # 尝试计算位范围的数值
            msb_val = self._eval_const_expr(lval.msb)
            lsb_val = self._eval_const_expr(lval.lsb)

            if isinstance(lval.var, Identifier):
                return lval.var.name, "", msb_val, lsb_val
            elif isinstance(lval.var, Pointer):
                var_name, ptr_str, _, _ = self._extract_lval_with_index(lval.var)
                return var_name, ptr_str, msb_val, lsb_val
            else:
                var_name, ptr_str, _, _ = self._extract_lval_with_index(lval.var)
                return var_name, ptr_str, msb_val, lsb_val

        elif isinstance(lval, Concat):
            # 拼接: 返回第一个元素的索引（简化处理）
            if hasattr(lval, 'list') and lval.list:
                return self._extract_lval_with_index(lval.list[0])
            return None, "", None, None

        return None, "", None, None

    def _eval_const_expr(self, expr) -> Optional[int]:
        """
        尝试计算常量表达式的值
        支持: IntConst, Rvalue, 简单的算术表达式 (+, -, *, /)
        返回: 整数值 或 None（如果无法计算）
        """
        if expr is None:
            return None

        # 解包 Rvalue
        if isinstance(expr, Rvalue):
            expr = expr.var

        if isinstance(expr, IntConst):
            # 处理 'd32, 'h1F, 32 等格式
            val_str = str(expr.value).strip()
            try:
                # 处理 'd32 格式
                if "'d" in val_str.lower():
                    return int(val_str.split("'d")[-1], 10)
                # 处理 'h1F 格式
                elif "'h" in val_str.lower():
                    return int(val_str.split("'h")[-1], 16)
                # 处理 'b101 格式
                elif "'b" in val_str.lower():
                    return int(val_str.split("'b")[-1], 2)
                # 处理 'o77 格式
                elif "'o" in val_str.lower():
                    return int(val_str.split("'o")[-1], 8)
                # 普通十进制
                else:
                    return int(val_str, 10)
            except ValueError:
                return None

        elif isinstance(expr, Plus):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is not None and right is not None:
                return left + right

        elif isinstance(expr, Minus):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is not None and right is not None:
                return left - right

        elif isinstance(expr, Times):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is not None and right is not None:
                return left * right

        elif isinstance(expr, Divide):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is not None and right is not None and right != 0:
                return left // right

        elif isinstance(expr, Mod):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is not None and right is not None and right != 0:
                return left % right

        elif isinstance(expr, Identifier):
            # 尝试查找参数值
            symbol = self._lookup_symbol(expr.name)
            if symbol and symbol.is_param_type():
                # 参数值保存在 param_value 中
                if hasattr(symbol, 'param_value') and symbol.param_value is not None:
                    return self._eval_const_expr(symbol.param_value)
            if symbol and hasattr(symbol, 'value') and symbol.value is not None:
                return self._eval_const_expr(symbol.value)

        return None

    def _expr_to_string(self, expr) -> str:
        """
        将表达式转换为字符串表示（用于索引比较）
        """
        if expr is None:
            return ""

        if isinstance(expr, Identifier):
            return expr.name

        elif isinstance(expr, IntConst):
            return str(expr.value)

        elif isinstance(expr, Pointer):
            # 多维数组: array[i][j]
            base = self._expr_to_string(expr.var)
            idx = self._expr_to_string(expr.ptr)
            return f"{base}[{idx}]"

        elif isinstance(expr, Partselect):
            # 位选择: reg[7:0]
            base = self._expr_to_string(expr.var)
            msb = self._expr_to_string(expr.msb)
            lsb = self._expr_to_string(expr.lsb)
            return f"{base}[{msb}:{lsb}]"

        elif isinstance(expr, (Plus, Minus, Times, Divide)):
            # 简单算术表达式
            left = self._expr_to_string(expr.left)
            right = self._expr_to_string(expr.right)
            op = '+' if isinstance(expr, Plus) else '-' if isinstance(expr, Minus) else '*' if isinstance(expr, Times) else '/'
            return f"{left}{op}{right}"

        else:
            # 尝试从其他属性提取
            if hasattr(expr, 'name'):
                return expr.name
            if hasattr(expr, 'value'):
                return str(expr.value)
            return str(expr)

    def _collect_use_info(self):
        """收集所有寄存器的使用信息"""
        # 从 AST 中直接收集使用信息
        for module in self._get_modules():
            self._collect_module_use_info(module)

    def _collect_module_use_info(self, module: ModuleDef):
        """收集模块内的使用信息"""
        for item in module.items:
            if isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
                # 检查时序逻辑
                is_sequential = self._is_sequential_always(item)
                if is_sequential:
                    # 时序逻辑中的使用记录到 seq_use_map
                    self._collect_seq_always_use_info(item)
                else:
                    # 组合逻辑中的使用记录到 use_map
                    self._collect_stmt_use_info(item.statement)
            elif isinstance(item, Assign):
                # assign 语句的右侧是使用
                self._collect_expr_use_info(item.right, item.lineno)
            elif isinstance(item, (InstanceList, Instance)):
                # 模块实例化中的端口连接
                self._collect_instance_use_info(item)
            elif isinstance(item, Initial):
                # initial 块中的使用
                self._collect_stmt_use_info(item.statement)
            elif isinstance(item, Decl):
                # 处理声明时带初始化的 wire，收集 RHS 的使用信息
                self._collect_decl_use_info(item)
            elif isinstance(item, GenerateStatement):
                # generate 块中的语句
                if hasattr(item, 'items') and item.items:
                    for gen_item in item.items:
                        self._collect_generate_item_use_info(gen_item)

    def _collect_seq_always_use_info(self, always_node):
        """收集时序逻辑always块中的使用信息（记录到seq_use_map）"""
        def collect_seq_stmt(stmt):
            if stmt is None:
                return

            # 赋值语句：收集右侧的使用（非阻塞赋值的RHS）
            if isinstance(stmt, NonblockingSubstitution):
                self._collect_seq_expr_use_info(stmt.right, stmt.lineno)
            elif isinstance(stmt, BlockingSubstitution):
                # 时序逻辑中的阻塞赋值也记录
                self._collect_seq_expr_use_info(stmt.right, stmt.lineno)

            # 块语句
            elif hasattr(stmt, 'statements'):
                for s in stmt.statements:
                    collect_seq_stmt(s)

            # if语句
            elif hasattr(stmt, 'cond'):
                # if条件是一个表达式，需要用_expr_use_info处理
                self._collect_seq_expr_use_info(stmt.cond, stmt.lineno)
                if hasattr(stmt, 'true_statement') and stmt.true_statement:
                    collect_seq_stmt(stmt.true_statement)
                if hasattr(stmt, 'false_statement') and stmt.false_statement:
                    collect_seq_stmt(stmt.false_statement)

            # case语句
            elif hasattr(stmt, 'comp') and hasattr(stmt, 'caselist'):
                # case条件是一个表达式，需要用_expr_use_info处理
                self._collect_seq_expr_use_info(stmt.comp, stmt.lineno)
                for case in stmt.caselist:
                    if case.statement:
                        collect_seq_stmt(case.statement)
            elif isinstance(stmt,Identifier):
                self._collect_seq_expr_use_info(stmt,stmt.lineno)

        if hasattr(always_node, 'statement'):
            collect_seq_stmt(always_node.statement)

    def _collect_seq_expr_use_info(self, expr, lineno: int):
        """收集时序逻辑中的表达式使用（记录到seq_use_map）"""
        if expr is None:
            return

        if isinstance(expr, Identifier):
            if expr.name not in self.generate_for_vars:
                self.seq_use_map[expr.name].append(lineno)
                self._dbg(f"Found seq use: {expr.name} at line {lineno}")

        # 递归处理其他类型
        elif isinstance(expr, (Partselect, Pointer)):
            if hasattr(expr, 'var'):
                self._collect_seq_expr_use_info(expr.var, lineno)
            if hasattr(expr, 'ptr'):
                self._collect_seq_expr_use_info(expr.ptr, lineno)
            if hasattr(expr, 'msb'):
                self._collect_seq_expr_use_info(expr.msb, lineno)
            if hasattr(expr, 'lsb'):
                self._collect_seq_expr_use_info(expr.lsb, lineno)

        elif isinstance(expr, Concat):
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    self._collect_seq_expr_use_info(item, lineno)

        elif isinstance(expr, Cond):
            # 处理三元条件运算符: cond ? true_value : false_value
            if hasattr(expr, 'cond') and expr.cond:
                self._collect_seq_expr_use_info(expr.cond, lineno)
            if hasattr(expr, 'true_value') and expr.true_value:
                self._collect_seq_expr_use_info(expr.true_value, lineno)
            if hasattr(expr, 'false_value') and expr.false_value:
                self._collect_seq_expr_use_info(expr.false_value, lineno)

        elif isinstance(expr, Repeat):
            # 处理重复操作: {4{a}}
            if hasattr(expr, 'value') and expr.value:
                self._collect_seq_expr_use_info(expr.value, lineno)
            if hasattr(expr, 'times') and expr.times:
                self._collect_seq_expr_use_info(expr.times, lineno)

        elif hasattr(expr, 'left') and hasattr(expr, 'right'):
            self._collect_seq_expr_use_info(expr.left, lineno)
            self._collect_seq_expr_use_info(expr.right, lineno)

        elif hasattr(expr, 'var'):
            self._collect_seq_expr_use_info(expr.var, lineno)

    def _collect_generate_item_use_info(self, item):
        """递归收集generate块中的使用信息"""
        if item is None:
            return

        if isinstance(item, Assign):
            self._collect_expr_use_info(item.right, item.lineno)
        elif isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
            self._collect_stmt_use_info(item.statement)
        elif isinstance(item, Initial):
            self._collect_stmt_use_info(item.statement)
        elif isinstance(item, (InstanceList, Instance)):
            self._collect_instance_use_info(item)
        elif isinstance(item, Decl):
            # generate 块中的声明初始化
            self._collect_decl_use_info(item)
        elif isinstance(item, ForStatement):
            # generate for 语句
            if hasattr(item, 'pre') and item.pre:
                self._collect_generate_item_use_info(item.pre)
            if hasattr(item, 'cond') and item.cond:
                self._collect_expr_use_info(item.cond, item.lineno)
            if hasattr(item, 'post') and item.post:
                self._collect_generate_item_use_info(item.post)
            if hasattr(item, 'statement') and item.statement:
                self._collect_generate_item_use_info(item.statement)
        elif isinstance(item, GenerateStatement):
            if hasattr(item, 'items') and item.items:
                for gen_item in item.items:
                    self._collect_generate_item_use_info(gen_item)
        elif hasattr(item, 'true_statement') or hasattr(item, 'false_statement'):
            # generate if 语句
            if hasattr(item, 'cond') and item.cond:
                self._collect_expr_use_info(item.cond, item.lineno)
            if hasattr(item, 'true_statement') and item.true_statement:
                self._collect_generate_item_use_info(item.true_statement)
            if hasattr(item, 'false_statement') and item.false_statement:
                self._collect_generate_item_use_info(item.false_statement)
        elif isinstance(item, CaseStatement):
            # generate case 语句
            print(item)
            if item.case:
                self._collect_expr_use_info(item.case, item.lineno)
            for case_item in item.caselist:
                self._collect_generate_item_use_info(case_item)
        elif hasattr(item, 'statements'):
            # 块语句
            for stmt in item.statements:
                self._collect_generate_item_use_info(stmt)

    def _collect_decl_use_info(self, decl_node: Decl):
        """
        收集声明语句中的使用信息
        处理 wire 声明时带初始化的情况，收集等号右侧的表达式使用
        Pyverilog 将这种语法解析为 Decl 中包含 Wire 和 Assign 两个节点
        """
        if not hasattr(decl_node, 'list') or not decl_node.list:
            return

        # 处理 Decl 中的 Assign 节点（这些是 wire 声明时的赋值，RHS 包含使用）
        for decl_item in decl_node.list:
            if isinstance(decl_item, Assign):
                # 收集赋值右侧表达式中的使用
                self._collect_expr_use_info(decl_item.right, decl_item.lineno)
                self._dbg(f"Found decl use in wire initialization at line {decl_item.lineno}")

    def _collect_instance_use_info(self, instance):
        """收集模块实例化中的端口连接使用信息"""
        if isinstance(instance, InstanceList):
            for inst in instance.instances:
                self._collect_single_instance_use_info(inst)
        elif isinstance(instance, Instance):
            self._collect_single_instance_use_info(instance)

    def _collect_single_instance_use_info(self, instance: Instance):
        """收集单个实例的端口连接使用信息"""
        # 标记instance端口连接中的信号为被使用
        # 无论模块是否定义，端口连接都意味着信号被使用
        if hasattr(instance, 'portlist') and instance.portlist:
            for port in instance.portlist:
                if isinstance(port, PortArg) and port.argname:
                    # 收集instance端口连接中的信号使用
                    self._collect_instance_port_use(port.argname, instance.lineno)
                    # 同时标记为普通使用（用于drive-without-use检测）
                    self._collect_expr_use_info(port.argname, instance.lineno)

    def _collect_instance_port_use(self, expr, lineno: int):
        """收集instance端口连接中的信号使用"""
        if expr is None:
            return

        if isinstance(expr, Identifier):
            # 记录instance端口连接中的使用
            self.instance_use_map[expr.name].append(lineno)
            self._dbg(f"Found instance use: {expr.name} at line {lineno}")

        # 递归处理复合表达式
        elif isinstance(expr, (Partselect, Pointer)):
            if hasattr(expr, 'var'):
                self._collect_instance_port_use(expr.var, lineno)
            if hasattr(expr, 'ptr') and expr.ptr:
                self._collect_instance_port_use(expr.ptr, lineno)

        elif isinstance(expr, Concat):
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    self._collect_instance_port_use(item, lineno)

        elif isinstance(expr, Cond):
            # 处理三元条件运算符: cond ? true_value : false_value
            if hasattr(expr, 'cond') and expr.cond:
                self._collect_instance_port_use(expr.cond, lineno)
            if hasattr(expr, 'true_value') and expr.true_value:
                self._collect_instance_port_use(expr.true_value, lineno)
            if hasattr(expr, 'false_value') and expr.false_value:
                self._collect_instance_port_use(expr.false_value, lineno)

        elif isinstance(expr, Repeat):
            # 处理重复操作: {4{a}}
            if hasattr(expr, 'value') and expr.value:
                self._collect_instance_port_use(expr.value, lineno)
            if hasattr(expr, 'times') and expr.times:
                self._collect_instance_port_use(expr.times, lineno)

        else:
            # 通用递归处理
            for attr_name in ['left', 'right', 'var', 'next', 'args', 'value', 'times', 'ptr', 'msb', 'lsb', 'cond', 'true_value', 'false_value']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                self._collect_instance_port_use(item, lineno)
                        elif hasattr(attr_val, '__dict__'):
                            self._collect_instance_port_use(attr_val, lineno)

    def _collect_stmt_use_info(self, stmt):
        """递归收集语句中的使用信息"""
        if stmt is None:
            return

        # 赋值语句：收集右侧的使用
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            self._collect_expr_use_info(stmt.right, stmt.lineno)

        # 块语句
        elif hasattr(stmt, 'statements'):
            for s in stmt.statements:
                self._collect_stmt_use_info(s)

        # if语句
        elif hasattr(stmt, 'cond'):
            # 收集条件中的使用
            self._collect_expr_use_info(stmt.cond, stmt.lineno)
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                self._collect_stmt_use_info(stmt.true_statement)
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
            
                self._collect_stmt_use_info(stmt.false_statement)

        # case语句
        elif hasattr(stmt, 'comp') and hasattr(stmt, 'caselist'):
            # case条件
            self._collect_expr_use_info(stmt.comp, stmt.lineno)
            for case in stmt.caselist:
                if case.statement:
                    self._collect_stmt_use_info(case.statement)

    def _collect_expr_use_info(self, expr, lineno: int):
        """
        递归收集表达式中的标识符使用
        处理各种表达式类型：Identifier, Concat, Repeat, Partselect, Pointer,
        运算符表达式，以及实例化中的端口连接等
        """
        if expr is None:
            return
        if isinstance(expr,Cond):
            # 三元运算符: cond ? true_value : false_value
            # 只收集条件和两个分支的使用
            self._collect_expr_use_info(expr.cond, expr.lineno)
            if hasattr(expr, 'true_value') and expr.true_value:
                self._collect_expr_use_info(expr.true_value, expr.lineno)
            if hasattr(expr, 'false_value') and expr.false_value:
                self._collect_expr_use_info(expr.false_value, expr.lineno)
        # 处理不同的表达式类型
        if isinstance(expr, Identifier):
            # 普通标识符
            # 跳过 generate for 循环变量
            if expr.name in self.generate_for_vars:
                self._dbg(f"Skipping generate for variable: {expr.name}")
                return
            self.use_map[expr.name].append(lineno)
            self._dbg(f"Found use: {expr.name} at line {lineno}")

        elif isinstance(expr, (Partselect, Pointer)):
            # 位选择或数组索引: reg[7:0], array[idx]
            # var 是被访问的变量本身
            if hasattr(expr, 'var'):
                self._collect_expr_use_info(expr.var, lineno)
            # 索引可能是表达式，需要递归处理
            if hasattr(expr, 'ptr') and expr.ptr:
                self._collect_expr_use_info(expr.ptr, lineno)
            # Partselect 有 msb/lsb 属性
            if hasattr(expr, 'msb') and expr.msb:
                self._collect_expr_use_info(expr.msb, lineno)
            if hasattr(expr, 'lsb') and expr.lsb:
                self._collect_expr_use_info(expr.lsb, lineno)

        elif isinstance(expr, Concat):
            # 拼接操作: {a, b, c}
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    self._collect_expr_use_info(item, lineno)

        elif isinstance(expr, Repeat):
            # 重复操作: {4{a}}
            if hasattr(expr, 'value') and expr.value:
                self._collect_expr_use_info(expr.value, lineno)
            if hasattr(expr, 'times') and expr.times:
                self._collect_expr_use_info(expr.times, lineno)

        elif isinstance(expr, (Uplus, Uminus, Ulnot, Unot, Uand, Unand, Uor, Unor, Uxor, Uxnor)):
            # 一元运算符: +a, -a, !a, ~a, &a, ~&a, |a, ~|a, ^a, ~^a
            # 这些运算符都有 'right' 属性
            if hasattr(expr, 'right') and expr.right:
                self._collect_expr_use_info(expr.right, lineno)

        elif hasattr(expr, 'cond') and hasattr(expr, 'true_value') and hasattr(expr, 'false_value'):
            # 三元条件运算符 (Cond): cond ? true_value : false_value
            self._collect_expr_use_info(expr.cond, lineno)
            if expr.true_value:
                self._collect_expr_use_info(expr.true_value, lineno)
            if expr.false_value:
                self._collect_expr_use_info(expr.false_value, lineno)

        elif isinstance(expr, (And, Or, Xor, Xnor, Sll, Srl, Sla, Sra,
                              LessThan, GreaterThan, LessEq, GreaterEq,
                              Eq, NotEq, Eql, NotEql, Land, Lor,
                              Plus, Minus, Times, Divide, Mod, Power)):
            # 二元运算符
            if hasattr(expr, 'left') and expr.left:
                self._collect_expr_use_info(expr.left, lineno)
            if hasattr(expr, 'right') and expr.right:
                self._collect_expr_use_info(expr.right, lineno)

        elif isinstance(expr, Rvalue):
            # 右值包装器
            if hasattr(expr, 'var') and expr.var:
                self._collect_expr_use_info(expr.var, lineno)

        elif isinstance(expr, Lvalue):
            # 左值（在赋值右侧使用时）
            if hasattr(expr, 'var') and expr.var:
                self._collect_expr_use_info(expr.var, lineno)

        elif isinstance(expr, IntConst):
            # 整数常量，忽略
            pass

        else:
            # 通用递归处理：尝试遍历所有可能的子属性
            for attr_name in ['left', 'right', 'var', 'next', 'args', 'value', 'times', 'ptr', 'msb', 'lsb']:
                if hasattr(expr, attr_name):
                    attr_val = getattr(expr, attr_name)
                    if attr_val is not None:
                        if isinstance(attr_val, (list, tuple)):
                            for item in attr_val:
                                self._collect_expr_use_info(item, lineno)
                        elif hasattr(attr_val, '__dict__'):
                            self._collect_expr_use_info(attr_val, lineno)

    def _check_never_driven(self):
        """
        检查使用了从未被驱动的寄存器

        这种情况通常发生在寄存器被声明、被使用，但从未在任何过程块或assign中被赋值

        注意：
        - input端口不需要被驱动（它们被外部驱动）
        - output端口默认被外部使用
        - 连接到instance端口的信号不报告，因为可能是instance的输出驱动
        """
        # 合并 use_map 和 seq_use_map 中的使用信息
        all_uses = defaultdict(list)
        for reg_name, uses in self.use_map.items():
            all_uses[reg_name].extend(uses)
        for reg_name, uses in self.seq_use_map.items():
            all_uses[reg_name].extend(uses)

        for reg_name, uses in all_uses.items():
            if not uses:
                continue

            # 检查是否有驱动
            if reg_name not in self.drive_map or not self.drive_map[reg_name]:
                symbol = self._lookup_symbol(reg_name)

                # input端口不需要被驱动（它们被外部驱动）
                if symbol and symbol.type == SymbolType.INPUT:
                    continue

                # wire类型不是寄存器，跳过检查
                if symbol and symbol.type == SymbolType.WIRE:
                    continue

                # 跳过参数和本地参数 - 它们是常量，不需要被驱动
                if symbol and symbol.is_param_type():
                    continue

                # 跳过常量
                if symbol and symbol.is_constant:
                    continue

                # 跳过 integer 类型 - integer 会被默认驱动（初始化为0）
                if is_integer_symbol(symbol):
                    self._dbg(f"Skipping integer symbol: {reg_name}")
                    continue

                # 报告第一个使用位置
                first_use_lineno = min(uses)
                self.issues.append(RegisterIssue(
                    issue_type=RegisterIssueType.USE_BEFORE_DRIVE,
                    register_name=reg_name,
                    lineno=first_use_lineno,
                    description=f"Register '{reg_name}' is used but never driven",
                    severity="error"
                ))
                self._dbg(f"USE_NEVER_DRIVEN: {reg_name} used at {first_use_lineno} but never driven")

    def _check_use_before_drive(self):
        """
        检查先使用后驱动问题

        简化逻辑：只要有任何驱动（assign/always/initial），就不报 use_before_drive
        只报完全没有驱动的情况（这已经在 _check_never_driven 中处理了）

        原因：
        - 时序逻辑：非阻塞赋值在同一个时钟沿执行
        - 组合逻辑：并行执行，不考虑代码顺序
        - assign：连续赋值，随时都有驱动
        - initial：在t=0执行

        这个方法现在实际上是一个空检查，因为所有情况都被跳过了
        """
        # 简化：只要有驱动就不报 use_before_drive
        # 因为所有 Verilog 驱动方式（assign/always/initial）都是并行或立即执行的
        pass

    def _check_drive_without_use(self):
        """
        检查驱动后未使用问题

        对于每个寄存器，检查是否有驱动后没有任何使用
        区分处理普通使用和instance端口连接中的使用

        关键区别：
        - assign中的使用是"连续"的，会随时读取信号值，不按行号顺序
        - always块中的使用是"时序"的，需要按行号顺序检查

        特殊处理：
        - 模块的输出端口如果被驱动，默认假设被外部使用（因为可能在未解析的instance中使用）
        """
        for reg_name, drive_infos in self.drive_map.items():
            if not drive_infos:
                continue

            # 检查符号类型 - wire不是寄存器，跳过检查
            symbol = self._lookup_symbol(reg_name)
            if symbol and symbol.type == SymbolType.WIRE:
                continue

            regular_uses = self.use_map.get(reg_name, [])
            regular_seq_uses = self.seq_use_map.get(reg_name,[])
            instance_uses = self.instance_use_map.get(reg_name, [])
            # 检查是否是模块输出端口 - 输出端口默认被外部使用
            is_output_port = symbol and symbol.type == SymbolType.OUTPUT

            # 如果完全没有使用（包括instance端口连接），但如果是输出端口则跳过
            if not regular_uses and not instance_uses and not regular_seq_uses:
                if is_output_port:
                    self._dbg(f"Skipping drive-without-use for {reg_name} - output port assumed used externally")
                else:
                    for drive_info in drive_infos:
                        self.issues.append(RegisterIssue(
                            issue_type=RegisterIssueType.DRIVE_WITHOUT_USE,
                            register_name=reg_name,
                            lineno=drive_info.lineno,
                            description=f"Register '{reg_name}' is driven but never used",
                            severity="warning"
                        ))
                        self._dbg(f"DRIVE_WITHOUT_USE: {reg_name} at line {drive_info.lineno}")
                continue

            # 如果有instance端口连接中的使用，无论行号顺序如何，都不报告为驱动后未使用
            if instance_uses:
                self._dbg(f"Skipping drive-without-use for {reg_name} - used in instance port connection")
                continue

            # 检查是否有assign中的使用 - assign是连续赋值，不按行号顺序
            # 需要查找哪些使用是在assign中（通过检查行号对应的语句类型）
            has_assign_use = self._has_assign_use(reg_name, regular_uses)
            if has_assign_use:
                self._dbg(f"Skipping drive-without-use for {reg_name} - used in assign statement")
                continue

            # 如果信号在时序逻辑中被使用（作为RHS），则跳过检查
            # 这是状态机的标准写法：组合逻辑计算next_state，时序逻辑更新current_state
            seq_uses = self.seq_use_map.get(reg_name, [])
            if seq_uses:
                self._dbg(f"Skipping drive-without-use check for {reg_name} - used in sequential always block (state machine pattern)")
                continue

            # 对于时序逻辑（sequential always），所有驱动都是并行的，不应该按行号检查
            # 只要信号有被使用，就不应该报告"驱动后未使用"
            has_sequential_drive = any(d.is_sequential for d in drive_infos)
            if has_sequential_drive:
                self._dbg(f"Skipping drive-without-use check for {reg_name} - has sequential drive (parallel execution)")
                continue

            # 只有组合逻辑驱动时，才需要检查行号顺序
            comb_drives = [d for d in drive_infos if not d.is_sequential and d.driver_type != 'initial']
            if not comb_drives:
                continue

            # 只有always块中的使用时，检查最后一次驱动后是否有使用
            if regular_uses:
                last_drive_lineno = max(d.lineno for d in comb_drives)
                last_use_lineno = max(regular_uses)

                if last_drive_lineno > last_use_lineno:
                    # 输出端口不报告驱动后未使用
                    if is_output_port:
                        self._dbg(f"Skipping drive-without-use for {reg_name} - output port")
                        continue
                    # 最后一次驱动后没有使用
                    for drive_info in comb_drives:
                        if drive_info.lineno == last_drive_lineno:
                            self.issues.append(RegisterIssue(
                                issue_type=RegisterIssueType.DRIVE_WITHOUT_USE,
                                register_name=reg_name,
                                lineno=drive_info.lineno,
                                description=f"Register '{reg_name}' is driven but not used afterwards",
                                severity="warning",
                                drive_lineno=drive_info.lineno,
                                use_lineno=last_use_lineno
                            ))
                            self._dbg(f"DRIVE_WITHOUT_USE: {reg_name} last drive at {drive_info.lineno}, last use at {last_use_lineno}")
                            break

    def _has_assign_use(self, reg_name: str, use_linenos: List[int]) -> bool:
        """检查信号是否在assign语句中被使用"""
        for module in self._get_modules():
            for item in module.items:
                if isinstance(item, Assign):
                    # 检查这个assign是否使用了该信号
                    uses_in_assign = []
                    self._collect_expr_use_info_impl(item.right, item.lineno, uses_in_assign)
                    for name, _ in uses_in_assign:
                        if name == reg_name:
                            return True
        return False

    def _collect_expr_use_info_impl(self, expr, lineno: int, results: List[Tuple[str, int]]):
        """内部实现：收集表达式中的标识符使用"""
        if expr is None:
            return

        if isinstance(expr, Identifier):
            results.append((expr.name, lineno))

        # 递归处理其他类型...
        if hasattr(expr, 'left'):
            self._collect_expr_use_info_impl(expr.left, lineno, results)
        if hasattr(expr, 'right'):
            self._collect_expr_use_info_impl(expr.right, lineno, results)
        if hasattr(expr, 'var'):
            self._collect_expr_use_info_impl(expr.var, lineno, results)
        if hasattr(expr, 'ptr'):
            self._collect_expr_use_info_impl(expr.ptr, lineno, results)
        # 处理三元条件运算符 (Cond): cond ? true_value : false_value
        if hasattr(expr, 'cond') and expr.cond:
            self._collect_expr_use_info_impl(expr.cond, lineno, results)
        if hasattr(expr, 'true_value') and expr.true_value:
            self._collect_expr_use_info_impl(expr.true_value, lineno, results)
        if hasattr(expr, 'false_value') and expr.false_value:
            self._collect_expr_use_info_impl(expr.false_value, lineno, results)

    def _check_multi_drive(self):
        """
        检查多驱动问题

        多驱动定义：当且仅当一个寄存器在同一个模块、同一个并行层级中被多个不同的过程块驱动时，才算多驱动。
        跨模块的同名寄存器不算多驱动。

        并行层级（从高到低）：
        1. initial: 在t=0执行一次
        2. sequential (时序always): always @(posedge clk) - 并行执行
        3. combinational (组合always): always @(*) - 并行执行，但在时序逻辑之下
        4. assign: 独立的过程块，与always @(*)同级但每个assign独立

        多驱动判定：
        - 多个sequential块驱动同一信号 = 多驱动 ✓
        - 多个assign驱动同一信号 = 多驱动 ✓
        - 多个组合always块驱动同一信号 = 多驱动 ✓
        - assign + 组合always块 = 不算多驱动（不同层级）✗
        - sequential + 任何其他 = 不算多驱动（层级不同）✗
        - initial + 任何其他 = 不算多驱动（initial是独立的）✗
        - instance驱动：由于无法确定端口方向，暂不参与多驱动检查
        """
        # 按模块名分组驱动信息: module_name -> {reg_name -> [DriveInfo]}
        module_drives: Dict[str, Dict[str, List[DriveInfo]]] = defaultdict(lambda: defaultdict(list))

        for reg_name, drive_infos in self.drive_map.items():
            for d in drive_infos:
                module_name = d.module_name if d.module_name else "unknown"
                module_drives[module_name][reg_name].append(d)

        # 对每个模块分别检查多驱动
        for module_name, reg_drives in module_drives.items():
            for reg_name, drive_infos in reg_drives.items():
                if len(drive_infos) <= 1:
                    continue

                # wire类型不是寄存器，跳过多驱动检查
                symbol = self._lookup_symbol(reg_name)
                if symbol and symbol.type == SymbolType.WIRE:
                    continue

                # 排除instance驱动（无法确定端口方向，可能是input也可能是output）
                non_instance_drives = [d for d in drive_infos if d.driver_type != 'instance']
                if len(non_instance_drives) <= 1:
                    continue

                # 按并行层级分组
                assign_drives = [d for d in non_instance_drives if d.driver_type == 'assign']
                seq_drives = [d for d in non_instance_drives if d.is_sequential and d.driver_type != 'initial']
                comb_drives = [d for d in non_instance_drives if not d.is_sequential and d.driver_type not in ('assign', 'initial')]
                initial_drives = [d for d in non_instance_drives if d.driver_type == 'initial']

                # 检查多驱动：同一并行层级中有多个不同的驱动源
                # 关键：如果两个驱动针对同一个数组的不同索引（ptr_index不同），不算多驱动
                # 同样，如果两个驱动针对同一个寄存器的不同位范围（bit_range不重叠），也不算多驱动
                multi_drive_sources = []

                def bit_ranges_overlap(d1: DriveInfo, d2: DriveInfo) -> bool:
                    """检查两个驱动的位范围是否重叠"""
                    # 如果都没有位范围信息，视为重叠（整个寄存器被驱动）
                    if d1.bit_msb is None and d2.bit_msb is None:
                        return True
                    # 如果只有一个有位范围，视为重叠（保守处理）
                    if d1.bit_msb is None or d2.bit_msb is None:
                        return True
                    # 检查位范围是否重叠: [msb1:lsb1] 和 [msb2:lsb2]
                    # 重叠条件：不是 (msb1 < lsb2 或 msb2 < lsb1)
                    max_lsb = max(d1.bit_lsb, d2.bit_lsb)
                    min_msb = min(d1.bit_msb, d2.bit_msb)
                    return max_lsb <= min_msb

                def filter_same_index_drives(drives: List[DriveInfo]) -> List[DriveInfo]:
                    """过滤掉针对不同索引或不重叠位范围的驱动，只返回真正多驱动的源"""
                    if len(drives) <= 1:
                        return drives

                    # 按 (driver_name, ptr_index, 位范围) 分组
                    # 如果同一个driver_name驱动了不同的索引或位范围，这是允许的
                    # 如果不同的driver_name驱动了相同的索引且有重叠位范围，这才是多驱动
                    index_groups: Dict[str, List[DriveInfo]] = defaultdict(list)
                    for d in drives:
                        index_key = d.ptr_index if d.ptr_index else "__no_index__"
                        index_groups[index_key].append(d)

                    result = []
                    for index_key, index_drives in index_groups.items():
                        # 对于每个索引，按位范围分组检查
                        # 需要检查所有驱动对，看是否有重叠位范围的多驱动
                        unique_drivers_by_range: Dict[Tuple, DriveInfo] = {}

                        for d in index_drives:
                            driver_key = (d.driver_name, d.gen_branch)
                            bit_range_key = (d.bit_msb, d.bit_lsb)

                            # 检查是否与已有的驱动有位范围重叠
                            has_overlap = False
                            for existing_drive in unique_drivers_by_range.values():
                                existing_driver_key = (existing_drive.driver_name, existing_drive.gen_branch)
                                # 如果是同一个驱动源，检查位范围是否不同
                                if driver_key == existing_driver_key:
                                    # 同一个驱动源，不同位范围是允许的
                                    continue
                                # 不同驱动源，检查位范围是否重叠
                                if bit_ranges_overlap(d, existing_drive):
                                    has_overlap = True
                                    # 添加两个驱动到结果
                                    if existing_drive not in result:
                                        result.append(existing_drive)
                                    if d not in result:
                                        result.append(d)

                            if not has_overlap and driver_key not in [(unique_drivers_by_range[k].driver_name,
                                                                        unique_drivers_by_range[k].gen_branch)
                                                                       for k in unique_drivers_by_range]:
                                unique_drivers_by_range[bit_range_key] = d

                    return result

                # assign驱动：检查是否跨越generate边界和分支
                # generate内部的assign按分支分组，不同分支之间不算多驱动
                # generate外部的assign之间如果有多个，算多驱动
                if len(assign_drives) > 1:
                    # 分组：generate内部和外部的assign
                    assign_outside_gen = [d for d in assign_drives if not d.in_generate]
                    assign_inside_gen = [d for d in assign_drives if d.in_generate]

                    # 外部的assign：按索引过滤后检查
                    filtered_outside = filter_same_index_drives(assign_outside_gen)
                    if len(filtered_outside) > 1:
                        multi_drive_sources.extend(filtered_outside)

                    # generate内部的assign：按分支分组，再按索引检查
                    if assign_inside_gen:
                        # 按分支分组 - 使用完整分支路径以区分嵌套if
                        assign_branches = {}
                        for d in assign_inside_gen:
                            # 使用完整分支路径作为分组键
                            branch = d.gen_branch if d.gen_branch else 'default'
                            if branch not in assign_branches:
                                assign_branches[branch] = []
                            assign_branches[branch].append(d)

                        # 只有当同一分支内、同一索引有多个assign时才报告
                        for branch, drives in assign_branches.items():
                            filtered_branch = filter_same_index_drives(drives)
                            if len(filtered_branch) > 1:
                                multi_drive_sources.extend(filtered_branch)

                    # 只有当内外都有assign且针对同一索引、重叠位范围时才报告跨边界多驱动
                    if assign_outside_gen and assign_inside_gen:
                        # 检查是否有重叠的位范围
                        for outside_drive in assign_outside_gen:
                            for inside_drive in assign_inside_gen:
                                if (outside_drive.ptr_index == inside_drive.ptr_index and
                                    bit_ranges_overlap(outside_drive, inside_drive)):
                                    multi_drive_sources.extend(assign_outside_gen)
                                    multi_drive_sources.extend(assign_inside_gen)
                                    break
                            else:
                                continue
                            break

                # 时序逻辑驱动：多个不同的时序always块
                # 同一always块中的多次赋值不算多驱动
                # generate中不同if分支的always也不算多驱动
                # 不同索引的数组元素也不算多驱动
                seq_outside_gen = [d for d in seq_drives if not d.in_generate]
                seq_inside_gen = [d for d in seq_drives if d.in_generate]

                # 检查外部的时序驱动（考虑数组索引）
                filtered_seq_outside = filter_same_index_drives(seq_outside_gen)
                if len(filtered_seq_outside) > 1:
                    multi_drive_sources.extend(filtered_seq_outside)

                # 检查generate内部的时序驱动 - 按分支分组，再按索引检查
                seq_branches = {}
                for d in seq_inside_gen:
                    # 使用完整分支路径作为分组键
                    branch = d.gen_branch if d.gen_branch else 'default'
                    if branch not in seq_branches:
                        seq_branches[branch] = []
                    seq_branches[branch].append(d)

                # 只有当同一分支内、同一索引有多个always时才算多驱动
                for branch, drives in seq_branches.items():
                    filtered_branch = filter_same_index_drives(drives)
                    if len(filtered_branch) > 1:
                        multi_drive_sources.extend(filtered_branch)

                # 只有当内外都有时序驱动且针对同一索引、重叠位范围时才报告跨边界多驱动
                if seq_outside_gen and seq_inside_gen:
                    # 检查是否有重叠的位范围
                    for outside_drive in seq_outside_gen:
                        for inside_drive in seq_inside_gen:
                            if (outside_drive.ptr_index == inside_drive.ptr_index and
                                bit_ranges_overlap(outside_drive, inside_drive)):
                                multi_drive_sources.extend(seq_outside_gen)
                                multi_drive_sources.extend(seq_inside_gen)
                                break
                        else:
                            continue
                        break

                # 组合逻辑always块驱动：多个不同的组合always块
                # 同样处理generate分支和数组索引
                comb_outside_gen = [d for d in comb_drives if not d.in_generate]
                comb_inside_gen = [d for d in comb_drives if d.in_generate]

                # 检查外部的组合驱动（考虑数组索引）
                filtered_comb_outside = filter_same_index_drives(comb_outside_gen)
                if len(filtered_comb_outside) > 1:
                    multi_drive_sources.extend(filtered_comb_outside)

                # 检查generate内部的组合驱动 - 按分支分组，再按索引检查
                comb_branches = {}
                for d in comb_inside_gen:
                    # 使用完整分支路径作为分组键
                    branch = d.gen_branch if d.gen_branch else 'default'
                    if branch not in comb_branches:
                        comb_branches[branch] = []
                    comb_branches[branch].append(d)

                for branch, drives in comb_branches.items():
                    filtered_branch = filter_same_index_drives(drives)
                    if len(filtered_branch) > 1:
                        multi_drive_sources.extend(filtered_branch)

                # 只有当内外都有组合驱动且针对同一索引、重叠位范围时才报告跨边界多驱动
                if comb_outside_gen and comb_inside_gen:
                    # 检查是否有重叠的位范围
                    for outside_drive in comb_outside_gen:
                        for inside_drive in comb_inside_gen:
                            if (outside_drive.ptr_index == inside_drive.ptr_index and
                                bit_ranges_overlap(outside_drive, inside_drive)):
                                multi_drive_sources.extend(comb_outside_gen)
                                multi_drive_sources.extend(comb_inside_gen)
                                break
                        else:
                            continue
                        break

                # 注意：assign + 组合always块 = 不算多驱动（assign是独立层级，always @(*)是另一层级）
                # 如果既有assign又有组合always驱动，不算多驱动

                # initial是独立的，与其他层级都不冲突

                # 如果检测到多驱动，生成报告
                if multi_drive_sources:
                    driver_descs = []
                    for drive_info in multi_drive_sources:
                        idx_info = f"[{drive_info.ptr_index}]" if drive_info.ptr_index else ""
                        bit_info = f"[{drive_info.bit_msb}:{drive_info.bit_lsb}]" if drive_info.bit_msb is not None else ""
                        driver_descs.append(f"{drive_info.driver_type}{idx_info}{bit_info} (line {drive_info.lineno})")

                    first_drive = multi_drive_sources[0]
                    idx_info = f"[{first_drive.ptr_index}]" if first_drive.ptr_index else ""
                    bit_info = f"[{first_drive.bit_msb}:{first_drive.bit_lsb}]" if first_drive.bit_msb is not None else ""
                    self.issues.append(RegisterIssue(
                        issue_type=RegisterIssueType.MULTI_DRIVE,
                        register_name=reg_name,
                        lineno=first_drive.lineno,
                        description=f"Register '{reg_name}{idx_info}{bit_info}' has multiple drivers: {', '.join(driver_descs)}",
                        severity="error"
                    ))
                    self._dbg(f"MULTI_DRIVE: {reg_name}{idx_info}{bit_info} in module {module_name} has multiple parallel drivers")

    def _lookup_symbol(self, name: str) -> Optional[Symbol]:
        """查找符号"""
        return self.stb.lookup(name, self.stb.root_scope)

    def print_report(self):
        """打印检查报告"""
        print("\n" + "=" * 70)
        print("Register Check Report")
        print("=" * 70)

        if not self.issues:
            print("No register issues found")
            return

        # 按类型分组
        use_before_drive = [i for i in self.issues
                           if i.issue_type == RegisterIssueType.USE_BEFORE_DRIVE]
        drive_without_use = [i for i in self.issues
                            if i.issue_type == RegisterIssueType.DRIVE_WITHOUT_USE]
        multi_drive = [i for i in self.issues
                      if i.issue_type == RegisterIssueType.MULTI_DRIVE]

        if use_before_drive:
            print(f"\n[!] Use Before Drive ({len(use_before_drive)}):")
            for issue in use_before_drive:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if drive_without_use:
            print(f"\n[!] Drive Without Use ({len(drive_without_use)}):")
            for issue in drive_without_use:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if multi_drive:
            print(f"\n[!] Multi-Drive ({len(multi_drive)}):")
            for issue in multi_drive:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        print(f"\nTotal: {len(self.issues)} register issues")


def check_register(ast, dfg_builder: DFGBuilder, debug: bool = False) -> List[RegisterIssue]:
    """便捷函数：直接检查寄存器问题"""
    checker = RegisterChecker(ast, dfg_builder, debug=debug)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python register_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    from pyverilog.vparser.parser import parse
    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Build DFG
    dfg_builder = DFGBuilder(stb)
    dfg_builder.build(ast)

    # Check registers
    checker = RegisterChecker(ast, dfg_builder, debug=True)
    issues = checker.check()
    checker.print_report()
