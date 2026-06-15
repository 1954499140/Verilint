"""
Array Bound Checker - 数组越界检查器

检测:
1. array_index_out_of_bounds: 数组索引可能越界
2. bit_select_out_of_bounds: 位选择越界
3. vector_out_of_bounds: 向量访问越界

原理:
- 分析数组声明的维度
- 跟踪索引表达式的取值范围
- 检测常量索引是否越界
- 警告非常量索引可能的越界风险
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, Identifier, IntConst, Partselect, Pointer,
    NonblockingSubstitution, BlockingSubstitution, Assign,
    Lvalue, Rvalue, Always, AlwaysFF, AlwaysComb, AlwaysLatch,
    IfStatement, CaseStatement, ForStatement, Block,
    Decl, Input, Output, Inout, Reg, Wire, Integer,
    InstanceList, Instance, Plus, Minus, Times, Divide,
    Initial, Uminus
)

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbol_table_builder import SymbolTableBuilder
from symbol import Symbol, SymbolType


class ArrayBoundIssueType(Enum):
    """数组越界问题类型"""
    ARRAY_INDEX_OUT_OF_BOUNDS = "array_index_out_of_bounds"
    BIT_SELECT_OUT_OF_BOUNDS = "bit_select_out_of_bounds"
    VECTOR_OUT_OF_BOUNDS = "vector_out_of_bounds"


@dataclass
class ArrayBoundIssue:
    """数组越界问题报告"""
    issue_type: ArrayBoundIssueType
    signal_name: str
    lineno: int
    description: str
    declared_range: str
    accessed_range: str
    severity: str = "error"


class ArrayBoundChecker:
    """
    数组越界检查器

    检测数组、向量、位选择的越界访问
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False):
        self.ast = ast
        self.stb = stb
        self.issues: List[ArrayBoundIssue] = []
        self.debug = debug

        # 存储信号维度信息: signal_name -> {'msb': int, 'lsb': int, 'depth': int}
        self.signal_dimensions: Dict[str, Dict] = {}
        self._collect_signal_dimensions()

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[DEBUG] {msg}")

    def _collect_signal_dimensions(self):
        """收集所有信号的维度信息"""
        for scope in self.stb.all_scopes:
            for symbol_name, symbol in scope.symbols.items():
                dim_info = self._get_dimension_info(symbol)
                if dim_info:
                    self.signal_dimensions[symbol_name] = dim_info
                    self._dbg(f"Signal '{symbol_name}' dimensions: {dim_info}")

    def _get_dimension_info(self, symbol: Symbol) -> Optional[Dict]:
        """从符号中提取维度信息"""
        if not symbol:
            return None

        info = {
            'msb': None,
            'lsb': None,
            'width': None,
            'depth': None,  # 对于数组
            'is_array': False,
            'lineno': symbol.lineno
        }

        # 首先尝试从原始 AST 节点获取位宽（处理表达式如 AW+1:0）
        if hasattr(symbol, 'node') and symbol.node:
            node = symbol.node
            if hasattr(node, 'width') and node.width:
                width_info = self._extract_width_from_node(node.width)
                if width_info:
                    info['msb'], info['lsb'], info['width'] = width_info

        # 如果无法从 node 获取，尝试从 symbol 的 width 属性
        if info['width'] is None and hasattr(symbol, 'width') and symbol.width:
            # width 可能是整数或字符串
            if isinstance(symbol.width, int):
                info['width'] = symbol.width
                info['msb'] = symbol.width - 1
                info['lsb'] = 0
            elif isinstance(symbol.width, str):
                parsed = self._parse_range(symbol.width)
                if parsed:
                    info['msb'], info['lsb'] = parsed
                    info['width'] = info['msb'] - info['lsb'] + 1

        # 检查是否有数组维度 (array_dimensions 属性)
        if hasattr(symbol, 'array_dimensions') and symbol.array_dimensions:
            info['is_array'] = True
            # 计算总深度（元素个数）
            # array_dimensions 格式为 [(start, end), ...]，表示索引范围
            total_depth = 1
            for start, end in symbol.array_dimensions:
                if start is not None and end is not None:
                    total_depth *= abs(end - start) + 1
            info['depth'] = total_depth

        return info if info['width'] or info['depth'] else None

    def _extract_width_from_node(self, width_node) -> Optional[Tuple[int, int, int]]:
        """从 AST width 节点提取位宽 (msb, lsb, width)"""
        if not width_node:
            return None

        msb_val = None
        lsb_val = None

        # 处理 Width 节点
        if hasattr(width_node, 'msb') and hasattr(width_node, 'lsb'):
            msb_val = self._eval_expr(width_node.msb)
            lsb_val = self._eval_expr(width_node.lsb)

        if msb_val is not None and lsb_val is not None:
            width = msb_val - lsb_val + 1
            return (msb_val, lsb_val, width)

        return None

    def _eval_expr(self, expr) -> Optional[int]:
        """计算表达式值，支持 Parameter 引用"""
        if expr is None:
            return None

        # Rvalue 包装器 - 解包
        if hasattr(expr, 'var'):
            return self._eval_expr(expr.var)

        # 直接整数常量
        if isinstance(expr, IntConst):
            return self._parse_const_expr(expr.value)

        # 标识符（可能是 Parameter）
        if isinstance(expr, Identifier):
            return self._lookup_param_value(expr.name)

        # 二元运算（如 AW+1）
        if hasattr(expr, 'left') and hasattr(expr, 'right'):
            left_val = self._eval_expr(expr.left)
            right_val = self._eval_expr(expr.right)
            if left_val is not None and right_val is not None:
                # 根据操作符计算
                op = type(expr).__name__
                if op == 'Plus':
                    return left_val + right_val
                elif op == 'Minus':
                    return left_val - right_val
                elif op == 'Times':
                    return left_val * right_val
                elif op == 'Divide':
                    return left_val // right_val if right_val != 0 else None

        return None

    def _lookup_param_value(self, name: str) -> Optional[int]:
        """查找 Parameter 的值"""
        symbol = self._lookup_symbol(name)
        if not symbol:
            return None

        # 尝试获取常量值
        if hasattr(symbol, 'const_value') and symbol.const_value is not None:
            return symbol.const_value

        # 尝试从 initial_value 计算
        if hasattr(symbol, 'initial_value') and symbol.initial_value:
            val = self._eval_expr(symbol.initial_value)
            if val is not None:
                return val

        # 尝试从 param_value 获取
        if hasattr(symbol, 'param_value') and symbol.param_value:
            val = self._eval_expr(symbol.param_value)
            if val is not None:
                return val

        # 尝试从 node 直接获取（Parameter 的 AST 节点）
        if hasattr(symbol, 'node') and symbol.node:
            node = symbol.node
            # Parameter 节点可能有 value 属性
            if hasattr(node, 'value') and node.value:
                val = self._eval_expr(node.value)
                if val is not None:
                    return val

        return None

    def _parse_range(self, range_str: str) -> Optional[Tuple[int, int]]:
        """解析范围字符串 [msb:lsb]"""
        if not range_str:
            return None

        # 去掉方括号
        range_str = range_str.strip().strip('[]')

        # 尝试解析 msb:lsb
        if ':' in range_str:
            parts = range_str.split(':')
            if len(parts) == 2:
                try:
                    msb = self._parse_const_expr(parts[0].strip())
                    lsb = self._parse_const_expr(parts[1].strip())
                    if msb is not None and lsb is not None:
                        return (msb, lsb)
                except:
                    pass

        return None

    def _parse_const_expr(self, expr_str: str) -> Optional[int]:
        """解析常量表达式"""
        if not expr_str:
            return None

        expr_str = expr_str.strip()

        # 直接整数
        try:
            # 处理不同进制
            if expr_str.startswith('0x') or expr_str.startswith('0X'):
                return int(expr_str, 16)
            elif expr_str.startswith('0b') or expr_str.startswith('0B'):
                return int(expr_str, 2)
            elif expr_str.startswith('0') and len(expr_str) > 1:
                return int(expr_str, 8)
            else:
                return int(expr_str)
        except ValueError:
            pass

        # 尝试解析 WIDTH-1 这样的表达式
        if '-' in expr_str:
            parts = expr_str.split('-')
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                try:
                    left_val = self._parse_const_expr(left)
                    right_val = self._parse_const_expr(right)
                    if left_val is not None and right_val is not None:
                        return left_val - right_val
                except:
                    pass

        return None

    def check(self) -> List[ArrayBoundIssue]:
        """执行数组越界检查"""
        self.issues = []

        for module in self._get_modules():
            self._check_module(module)

        return self.issues

    def _check_module(self, module: ModuleDef):
        """检查单个模块"""
        for item in module.items:
            if isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
                self._check_always_block(item)
            elif isinstance(item, Assign):
                self._check_assign(item)
            elif isinstance(item, (InstanceList, Instance)):
                self._check_instance(item)
            elif isinstance(item, Initial):
                self._check_initial_block(item)

    def _check_always_block(self, always_node):
        """检查always块"""
        if hasattr(always_node, 'statement'):
            self._check_statement(always_node.statement)

    def _check_initial_block(self, initial_node):
        """检查initial块"""
        if hasattr(initial_node, 'statement'):
            self._check_statement(initial_node.statement)

    def _check_statement(self, stmt):
        """递归检查语句"""
        if stmt is None:
            return

        # 赋值语句
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            # 检查右侧表达式中的数组访问
            if hasattr(stmt, 'right'):
                self._check_expression(stmt.right, stmt.lineno)
            # 检查左侧表达式中的位选择
            if hasattr(stmt, 'left'):
                self._check_lvalue(stmt.left, stmt.lineno)

        # if语句
        elif isinstance(stmt, IfStatement):
            if hasattr(stmt, 'cond'):
                self._check_expression(stmt.cond, stmt.lineno)
            if hasattr(stmt, 'true_statement'):
                self._check_statement(stmt.true_statement)
            if hasattr(stmt, 'false_statement'):
                self._check_statement(stmt.false_statement)

        # case语句
        elif isinstance(stmt, CaseStatement):
            if hasattr(stmt, 'comp'):
                self._check_expression(stmt.comp, stmt.lineno)
            if hasattr(stmt, 'caselist'):
                for case in stmt.caselist:
                    if hasattr(case, 'cond') and case.cond:
                        self._check_expression(case.cond, stmt.lineno)
                    if case.statement:
                        self._check_statement(case.statement)

        # 块语句
        elif isinstance(stmt, Block):
            if hasattr(stmt, 'statements'):
                for s in stmt.statements:
                    self._check_statement(s)

        # for语句
        elif isinstance(stmt, ForStatement):
            if hasattr(stmt, 'statement'):
                self._check_statement(stmt.statement)

        # 递归处理其他可能的语句类型
        for attr_name in ['statement', 'true_statement', 'false_statement']:
            if hasattr(stmt, attr_name):
                attr_val = getattr(stmt, attr_name)
                if attr_val and not isinstance(attr_val, (NonblockingSubstitution, BlockingSubstitution)):
                    self._check_statement(attr_val)

    def _check_assign(self, assign_node):
        """检查assign语句"""
        # 检查右侧表达式
        if hasattr(assign_node, 'right'):
            self._check_expression(assign_node.right, assign_node.lineno)
        # 检查左侧
        if hasattr(assign_node, 'left'):
            self._check_lvalue(assign_node.left, assign_node.lineno)

    def _check_instance(self, instance):
        """检查instance端口连接"""
        if isinstance(instance, InstanceList):
            for inst in instance.instances:
                self._check_single_instance(inst)
        elif isinstance(instance, Instance):
            self._check_single_instance(instance)

    def _check_single_instance(self, instance):
        """检查单个instance"""
        if hasattr(instance, 'portlist') and instance.portlist:
            for port in instance.portlist:
                if hasattr(port, 'argname') and port.argname:
                    self._check_expression(port.argname, instance.lineno)

    def _check_expression(self, expr, lineno: int):
        """检查表达式中的数组访问和位选择"""
        if expr is None:
            return

        # 位选择或数组索引
        if isinstance(expr, (Partselect, Pointer)):
            self._check_partselect_or_pointer(expr, lineno)

        # 递归处理子表达式
        for attr_name in ['left', 'right', 'var', 'ptr', 'msb', 'lsb', 'next', 'args', 'cond', 'true_value', 'false_value']:
            if hasattr(expr, attr_name):
                attr_val = getattr(expr, attr_name)
                if attr_val is not None:
                    if isinstance(attr_val, (list, tuple)):
                        for item in attr_val:
                            self._check_expression(item, lineno)
                    else:
                        self._check_expression(attr_val, lineno)

    def _check_lvalue(self, lval, lineno: int):
        """检查左值中的位选择"""
        if lval is None:
            return

        # 解包 Lvalue
        if isinstance(lval, Lvalue):
            if hasattr(lval, 'var'):
                lval = lval.var

        # 检查位选择或数组索引
        if isinstance(lval, (Partselect, Pointer)):
            self._check_partselect_or_pointer(lval, lineno)

        # 递归处理
        for attr_name in ['var', 'ptr', 'msb', 'lsb']:
            if hasattr(lval, attr_name):
                attr_val = getattr(lval, attr_name)
                if attr_val:
                    self._check_lvalue(attr_val, lineno)

    def _check_partselect_or_pointer(self, expr, lineno: int):
        """检查位选择或数组索引的越界"""
        if not isinstance(expr, (Partselect, Pointer)):
            return

        # 获取基础信号名
        signal_name = None
        if hasattr(expr, 'var') and isinstance(expr.var, Identifier):
            signal_name = expr.var.name

        if not signal_name or signal_name not in self.signal_dimensions:
            # 递归检查子表达式
            if hasattr(expr, 'var'):
                self._check_expression(expr.var, lineno)
            return

        dim_info = self.signal_dimensions[signal_name]

        if isinstance(expr, Partselect):
            # 位选择 [msb:lsb]
            msb_val = None
            lsb_val = None

            if hasattr(expr, 'msb'):
                msb_val = self._get_const_value(expr.msb)
            if hasattr(expr, 'lsb'):
                lsb_val = self._get_const_value(expr.lsb)

            if msb_val is not None and lsb_val is not None and dim_info['msb'] is not None:
                # 检查是否越界
                if msb_val > dim_info['msb'] or lsb_val < dim_info['lsb']:
                    self._add_out_of_bounds_issue(
                        signal_name, lineno, dim_info,
                        f"[{msb_val}:{lsb_val}]",
                        ArrayBoundIssueType.BIT_SELECT_OUT_OF_BOUNDS
                    )

        elif isinstance(expr, Pointer):
            # 数组索引 [index]
            index_val = None
            if hasattr(expr, 'ptr'):
                index_val = self._get_const_value(expr.ptr)

            if index_val is not None and dim_info['depth'] is not None:
                # 检查数组索引是否越界
                if index_val < 0 or index_val >= dim_info['depth']:
                    self._add_out_of_bounds_issue(
                        signal_name, lineno, dim_info,
                        f"[{index_val}]",
                        ArrayBoundIssueType.ARRAY_INDEX_OUT_OF_BOUNDS
                    )

        # 递归检查子表达式
        for attr_name in ['var', 'msb', 'lsb', 'ptr']:
            if hasattr(expr, attr_name):
                attr_val = getattr(expr, attr_name)
                if attr_val:
                    self._check_expression(attr_val, lineno)

    def _get_const_value(self, expr) -> Optional[int]:
        """从表达式中提取常量值"""
        if expr is None:
            return None

        if isinstance(expr, IntConst):
            return self._parse_const_expr(expr.value)

        # 处理一元负号 (如 -1)
        if isinstance(expr, Uminus):
            if hasattr(expr, 'right') and expr.right:
                val = self._get_const_value(expr.right)
                if val is not None:
                    return -val

        # 尝试解析 Identifier 常量
        if isinstance(expr, Identifier):
            # 查找符号表中是否有该常量的值
            symbol = self._lookup_symbol(expr.name)
            if symbol and hasattr(symbol, 'initial_value') and symbol.initial_value:
                return self._get_const_value(symbol.initial_value)

        return None

    def _lookup_symbol(self, name: str) -> Optional[Symbol]:
        """查找符号"""
        return self.stb.lookup(name, self.stb.root_scope)

    def _add_out_of_bounds_issue(self, signal_name: str, lineno: int,
                                  dim_info: Dict, accessed_range: str,
                                  issue_type: ArrayBoundIssueType):
        """添加越界问题"""
        declared_range = ""
        if dim_info['width'] and dim_info['msb'] is not None:
            declared_range = f"[{dim_info['msb']}:{dim_info['lsb']}]"
        if dim_info['depth'] and dim_info['depth'] > 0:
            declared_range += f"[0:{dim_info['depth']-1}]"

        description = f"Signal '{signal_name}' out of bounds access: {accessed_range} vs declared {declared_range}"

        issue = ArrayBoundIssue(
            issue_type=issue_type,
            signal_name=signal_name,
            lineno=lineno,
            description=description,
            declared_range=declared_range,
            accessed_range=accessed_range,
            severity="error"
        )
        self.issues.append(issue)
        self._dbg(f"ISSUE: {description}")

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
        print("Array Bound Check Report")
        print("=" * 70)

        if not self.issues:
            print("No array bound issues found")
            return

        # 按类型分组
        array_issues = [i for i in self.issues
                       if i.issue_type == ArrayBoundIssueType.ARRAY_INDEX_OUT_OF_BOUNDS]
        bit_issues = [i for i in self.issues
                     if i.issue_type == ArrayBoundIssueType.BIT_SELECT_OUT_OF_BOUNDS]
        vector_issues = [i for i in self.issues
                        if i.issue_type == ArrayBoundIssueType.VECTOR_OUT_OF_BOUNDS]

        if array_issues:
            print(f"\n[!] Array Index Out of Bounds ({len(array_issues)}):")
            for issue in array_issues:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if bit_issues:
            print(f"\n[!] Bit Select Out of Bounds ({len(bit_issues)}):")
            for issue in bit_issues:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if vector_issues:
            print(f"\n[!] Vector Out of Bounds ({len(vector_issues)}):")
            for issue in vector_issues:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        print(f"\nTotal: {len(self.issues)} array bound issues")


def check_array_bound(ast, stb: SymbolTableBuilder, debug: bool = False) -> List[ArrayBoundIssue]:
    """便捷函数：直接检查数组越界问题"""
    checker = ArrayBoundChecker(ast, stb, debug=debug)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python array_bound_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    from pyverilog.vparser.parser import parse

    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Check array bounds
    checker = ArrayBoundChecker(ast, stb, debug=True)
    issues = checker.check()
    checker.print_report()
