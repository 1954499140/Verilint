"""
Width Checker - Bit Width Mismatch Detector
Detects width mismatches in assignments and operations
"""

from typing import List, Set, Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyverilog.vparser.ast import (
    IfStatement, NonblockingSubstitution, BlockingSubstitution,
    Lvalue, Identifier, IntConst, Partselect, Concat, Repeat, UnaryOperator,
    Operator, Cond, Assign, Instance, InstanceList, ModuleDef,
    Plus, Minus, Times, Divide, Mod, Land, Lor, Eq, NotEq, Eql, NotEql, LessThan, GreaterThan,
    LessEq, GreaterEq, Xor, And, Or, Sll, Srl, Sra, Rvalue,
    GenerateStatement, ForStatement, Block, Pointer,
    Uand, Unand, Uor, Unor, Uxor, Uxnor, Ulnot
)
from symbol_table_builder import SymbolTableBuilder
from dfg_builder import DFGBuilder
from symbol import Symbol


class WidthIssueType(Enum):
    """Width issue types"""
    MISMATCH = "mismatch"           # Width mismatch in assignment
    TRUNCATION = "truncation"       # Potential truncation
    EXTENSION = "extension"         # Unnecessary extension
    OPERAND_MISMATCH = "operand_mismatch"  # Operand width mismatch
    PORT_MISMATCH = "port_mismatch" # Instance port width mismatch
    PARTSELECT_BOUNDS = "partselect_bounds"  # Partselect bounds error (msb < lsb)
    PARTSELECT_OVERFLOW = "partselect_overflow"  # Partselect exceeds signal width
    CONCAT_DUPLICATE = "concat_duplicate"  # Duplicate signals in concat
    CONCAT_WIDTH_MISMATCH = "concat_width_mismatch"  # Concat width doesn't match LHS


@dataclass
class WidthIssue:
    """Width issue report"""
    symbol_name: str
    issue_type: WidthIssueType
    lineno: int
    lhs_width: int
    rhs_width: int
    description: str


class BitAssignmentTracker:
    """
    位精确的部分赋值跟踪器
    跟踪向量信号的哪些位已被赋值
    """

    def __init__(self):
        # signal_name -> {lineno: (bit_indices, is_complete)}
        # bit_indices 可以是:
        #   - 单个整数 (Pointer): 0
        #   - 范围元组 (Partselect): (msb, lsb)
        #   - None (完整赋值)
        self.partial_assignments: Dict[str, List[Tuple[int, Optional[Tuple[int, int]], bool]]] = {}

    def record_assignment(self, signal_name: str, lineno: int, bit_range: Optional[Tuple[int, int]] = None, is_complete: bool = False):
        """
        记录一次赋值操作

        Args:
            signal_name: 信号名
            lineno: 赋值行号
            bit_range: 位范围，None 表示完整赋值，(msb, lsb) 表示部分赋值
            is_complete: 是否是完整赋值（Identifier 左值）
        """
        if signal_name not in self.partial_assignments:
            self.partial_assignments[signal_name] = []

        self.partial_assignments[signal_name].append((lineno, bit_range, is_complete))

    def get_assigned_bits(self, signal_name: str, signal_width: int) -> Set[int]:
        """
        获取信号已被赋值的位集合

        Args:
            signal_name: 信号名
            signal_width: 信号总位宽

        Returns:
            已被赋值的位索引集合
        """
        if signal_name not in self.partial_assignments:
            return set()

        assigned_bits = set()

        for lineno, bit_range, is_complete in self.partial_assignments[signal_name]:
            if is_complete or bit_range is None:
                # 完整赋值，所有位都被覆盖
                return set(range(signal_width))
            else:
                msb, lsb = bit_range
                # 确保 msb >= lsb
                if msb >= lsb:
                    assigned_bits.update(range(lsb, msb + 1))
                else:
                    assigned_bits.update(range(msb, lsb + 1))

        return assigned_bits

    def get_unassigned_bits(self, signal_name: str, signal_width: int) -> Set[int]:
        """获取信号未被赋值的位集合"""
        all_bits = set(range(signal_width))
        assigned_bits = self.get_assigned_bits(signal_name, signal_width)
        return all_bits - assigned_bits

    def has_partial_assignment(self, signal_name: str) -> bool:
        """检查信号是否有部分赋值记录"""
        return signal_name in self.partial_assignments and len(self.partial_assignments[signal_name]) > 0

    def check_partial_assignment_width(self, signal_name: str, signal_width: int,
                                       lhs_msb: int, lhs_lsb: int,
                                       rhs_width: int, lineno: int) -> Optional[WidthIssue]:
        """
        检查部分赋值时的位宽匹配

        Args:
            signal_name: 信号名
            signal_width: 信号总位宽
            lhs_msb: 左值 msb
            lhs_lsb: 左值 lsb
            rhs_width: 右值位宽
            lineno: 行号

        Returns:
            如果发现问题，返回 WidthIssue，否则返回 None
        """
        lhs_width = abs(lhs_msb - lhs_lsb) + 1

        if rhs_width > lhs_width:
            # RHS 比 LHS 部分选择的位宽更宽，可能截断
            return WidthIssue(
                symbol_name=signal_name,
                issue_type=WidthIssueType.TRUNCATION,
                lineno=lineno,
                lhs_width=lhs_width,
                rhs_width=rhs_width,
                description=f"Partial assignment truncation: {lhs_width}-bit slice [{lhs_msb}:{lhs_lsb}] assigned {rhs_width}-bit RHS"
            )

        return None


class WidthChecker:
    """
    Width Checker

    Detects:
    1. Assignment width mismatches (LHS vs RHS)
    2. Truncation risks (RHS wider than LHS)
    3. Operand width mismatches in expressions
    4. Parameter-dependent width issues
    """

    def __init__(self, dfg_builder: DFGBuilder):
        self.dfg_builder = dfg_builder
        self.stb: SymbolTableBuilder = dfg_builder.stb
        self.issues: List[WidthIssue] = []
        self.bit_tracker = BitAssignmentTracker()  # 位精确跟踪器

    def check(self) -> List[WidthIssue]:
        """Run all width checks"""
        self.issues = []
        self.bit_tracker = BitAssignmentTracker()  # 重置位跟踪器

        # Check all modules
        for module_name, dfg in self.dfg_builder.dfgs.items():
            self._check_module(module_name, dfg)

        return self.issues

    def _check_module(self, module_name: str, dfg):
        """Check a single module"""
        # Get module scope
        module_scope = self.stb.get_module_scope(module_name)
        if not module_scope:
            return

        # Check assign statements
        if dfg.assign_graph:
            for node in dfg.assign_graph.nodes:
                if isinstance(node.stmt, Assign):
                    self._check_assignment(node.stmt, module_scope)

        # Check always block assignments
        for subgraph_name, subgraph in dfg.subgraphs.items():
            for node in subgraph.nodes:
                stmt = node.stmt
                if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
                    self._check_substitution(stmt, module_scope)

        # Check instance port connections
        self._check_instances(module_name, module_scope)

    def _get_symbol_width(self, name: str, scope) -> Optional[int]:
        """Get symbol width, return None if unknown"""
        symbol = scope.lookup_symbol(name)
        if symbol:
            return symbol.width
        return None

    def _check_assignment(self, assign: Assign, scope):
        """Check continuous assignment width"""
        # Get LHS width
        lhs_width = self._get_expr_width(assign.left, scope)

        # Get RHS width
        rhs_width = self._get_expr_width(assign.right, scope)

        # Extract LHS name and track partial assignments
        lval = assign.left
        if isinstance(lval, Lvalue):
            lval = lval.var
        if isinstance(lval, Identifier):
            lhs_name = lval.name
            # 记录完整赋值
            self.bit_tracker.record_assignment(lhs_name, assign.lineno, None, True)
        elif hasattr(lval, 'name'):
            lhs_name = lval.name
        else:
            lhs_name = str(lval)

        # 处理部分赋值（Pointer 和 Partselect）
        if isinstance(lval, Pointer):
            # Pointer 可能是:
            # 1. arr[0] - 数组索引（访问数组元素）
            # 2. vector[0] - 位选择（选择向量的一位）
            if isinstance(lval.var, Identifier):
                signal_name = lval.var.name
                # 检查变量是否是数组类型
                symbol = scope.lookup_symbol(signal_name)
                is_array = symbol and symbol.array_dimensions

                if is_array:
                    # 数组索引: arr[0] = expr - 这是对整个数组元素的赋值
                    # 不视为 bit-select，跳过部分赋值检查
                    pass
                else:
                    # 位选择: vector[0] = expr - 这是对单个位的赋值
                    bit_idx = self._eval_const_expr(lval.ptr, scope)
                    if bit_idx is not None:
                        self.bit_tracker.record_assignment(signal_name, assign.lineno, (bit_idx, bit_idx), False)
                        # 检查部分赋值的位宽
                        if rhs_width is not None and rhs_width > 1:
                            issue = WidthIssue(
                                symbol_name=signal_name,
                                issue_type=WidthIssueType.TRUNCATION,
                                lineno=assign.lineno,
                                lhs_width=1,
                                rhs_width=rhs_width,
                                description=f"Bit-select assignment truncation: 1-bit LHS [{bit_idx}] assigned {rhs_width}-bit RHS"
                            )
                            self.issues.append(issue)
        elif isinstance(lval, Partselect):
            # a[7:4] = expr - 部分选择
            if isinstance(lval.var, Identifier):
                signal_name = lval.var.name
                msb = self._eval_const_expr(lval.msb, scope)
                lsb = self._eval_const_expr(lval.lsb, scope)
                if msb is not None and lsb is not None:
                    self.bit_tracker.record_assignment(signal_name, assign.lineno, (msb, lsb), False)
                    # 检查部分赋值的位宽
                    lhs_slice_width = abs(msb - lsb) + 1
                    if rhs_width is not None and rhs_width > lhs_slice_width:
                        issue = WidthIssue(
                            symbol_name=signal_name,
                            issue_type=WidthIssueType.TRUNCATION,
                            lineno=assign.lineno,
                            lhs_width=lhs_slice_width,
                            rhs_width=rhs_width,
                            description=f"Part-select assignment truncation: {lhs_slice_width}-bit slice [{msb}:{lsb}] assigned {rhs_width}-bit RHS"
                        )
                        self.issues.append(issue)

        # Check Concat and Partselect on RHS
        self._check_concat_width(assign.right, lhs_width, lhs_name, assign.lineno, scope)
        self._check_concat_issues(assign.right, lhs_width, lhs_name, assign.lineno, scope)
        self._check_partselect_issues(assign.right, lhs_name, assign.lineno, scope)
        self._check_pointer_bounds(assign.right, lhs_name, assign.lineno, scope)

        if lhs_width is not None and rhs_width is not None:
            rhs_expr = assign.right
            if isinstance(rhs_expr, Rvalue):
                rhs_expr = rhs_expr.var
            is_arith = isinstance(rhs_expr, (Plus, Minus)) or self._expr_contains_arithmetic(rhs_expr)
            rhs_has_param = self._expr_contains_parameter(rhs_expr, scope)
            lhs_has_param = self._is_parametrized_signal(assign.left, scope)
            # If either side has parameters, don't report width issues
            # (designer is responsible for parameter consistency)
            has_param = rhs_has_param or lhs_has_param
            self._compare_widths(lhs_width, rhs_width, assign.lineno, lhs_name, is_arith, has_param)

    def _check_substitution(self, subst, scope):
        """Check blocking/nonblocking substitution width"""
        # Get LHS
        lval = subst.left
        if isinstance(lval, Lvalue):
            lval = lval.var

        lhs_width = self._get_expr_width(lval, scope)
        rhs_width = self._get_expr_width(subst.right, scope)

        # Extract LHS name properly and track partial assignments
        if isinstance(lval, Identifier):
            lhs_name = lval.name
            # 记录完整赋值
            self.bit_tracker.record_assignment(lhs_name, subst.lineno, None, True)
        elif hasattr(lval, 'name'):
            lhs_name = lval.name
        else:
            lhs_name = str(lval)

        # 处理部分赋值（Pointer 和 Partselect）
        if isinstance(lval, Pointer):
            # Pointer 可能是:
            # 1. arr[0] - 数组索引（访问数组元素）
            # 2. vector[0] - 位选择（选择向量的一位）
            if isinstance(lval.var, Identifier):
                signal_name = lval.var.name
                # 检查变量是否是数组类型
                symbol = scope.lookup_symbol(signal_name)
                is_array = symbol and symbol.array_dimensions

                if is_array:
                    # 数组索引: arr[0] <= expr - 这是对整个数组元素的赋值
                    # 不视为 bit-select，跳过部分赋值检查
                    pass
                else:
                    # 位选择: vector[0] <= expr - 这是对单个位的赋值
                    bit_idx = self._eval_const_expr(lval.ptr, scope)
                    if bit_idx is not None:
                        self.bit_tracker.record_assignment(signal_name, subst.lineno, (bit_idx, bit_idx), False)
                        # 检查部分赋值的位宽
                        if rhs_width is not None and rhs_width > 1:
                            issue = WidthIssue(
                                symbol_name=signal_name,
                                issue_type=WidthIssueType.TRUNCATION,
                                lineno=subst.lineno,
                                lhs_width=1,
                                rhs_width=rhs_width,
                                description=f"Bit-select assignment truncation: 1-bit LHS [{bit_idx}] assigned {rhs_width}-bit RHS"
                            )
                            self.issues.append(issue)
        elif isinstance(lval, Partselect):
            # a[7:4] <= expr - 部分选择
            if isinstance(lval.var, Identifier):
                signal_name = lval.var.name
                msb = self._eval_const_expr(lval.msb, scope)
                lsb = self._eval_const_expr(lval.lsb, scope)
                if msb is not None and lsb is not None:
                    self.bit_tracker.record_assignment(signal_name, subst.lineno, (msb, lsb), False)
                    # 检查部分赋值的位宽
                    lhs_slice_width = abs(msb - lsb) + 1
                    if rhs_width is not None and rhs_width > lhs_slice_width:
                        issue = WidthIssue(
                            symbol_name=signal_name,
                            issue_type=WidthIssueType.TRUNCATION,
                            lineno=subst.lineno,
                            lhs_width=lhs_slice_width,
                            rhs_width=rhs_width,
                            description=f"Part-select assignment truncation: {lhs_slice_width}-bit slice [{msb}:{lsb}] assigned {rhs_width}-bit RHS"
                        )
                        self.issues.append(issue)

        # Check Concat and Partselect on RHS
        self._check_concat_width(subst.right, lhs_width, lhs_name, subst.lineno, scope)
        self._check_concat_issues(subst.right, lhs_width, lhs_name, subst.lineno, scope)
        self._check_partselect_issues(subst.right, lhs_name, subst.lineno, scope)
        self._check_pointer_bounds(subst.right, lhs_name, subst.lineno, scope)

        if lhs_width is not None and rhs_width is not None:
            rhs_expr = subst.right
            if isinstance(rhs_expr, Rvalue):
                rhs_expr = rhs_expr.var
            is_arith = isinstance(rhs_expr, (Plus, Minus)) or self._expr_contains_arithmetic(rhs_expr)
            rhs_has_param = self._expr_contains_parameter(rhs_expr, scope)
            lhs_has_param = self._is_parametrized_signal(subst.left, scope)
            # If either side has parameters, don't report width issues
            # (designer is responsible for parameter consistency)
            has_param = rhs_has_param or lhs_has_param
            self._compare_widths(lhs_width, rhs_width, subst.lineno, lhs_name, is_arith, has_param)

    def _get_expr_width(self, expr, scope) -> Optional[int]:
        """Calculate expression width"""
        if expr is None:
            return None

        # Identifier (symbol)
        if isinstance(expr, Identifier):
            return self._get_symbol_width(expr.name, scope)

        # Lvalue/Rvalue wrapper
        if isinstance(expr, (Lvalue, Rvalue)):
            return self._get_expr_width(expr.var, scope)

        # Integer constant
        if isinstance(expr, IntConst):
            return self._parse_int_width(expr)

        # Part-select (bit slice)
        if isinstance(expr, Partselect):
            return self._calc_partselect_width(expr, scope)

        # Concatenation
        if isinstance(expr, Concat):
            total_width = 0
            if expr.list:
                for item in expr.list:
                    item_width = self._get_expr_width(item, scope)
                    if item_width is None:
                        return None
                    total_width += item_width
            return total_width

        # Repeat {n{expr}}
        if isinstance(expr, Repeat):
            # Get repeat count from 'times' attribute
            repeat_count = self._eval_const_expr(expr.times, scope)
            if repeat_count is not None:
                # The expression to repeat is in 'value' attribute (which is a Concat)
                inner_width = self._get_expr_width(expr.value, scope)
                if inner_width is not None:
                    return repeat_count * inner_width
            return None

        # Unary operator
        if isinstance(expr, UnaryOperator):
            # Reduction operators (Uand, Unand, Uor, Unor, Uxor, Uxnor) always return 1 bit
            if isinstance(expr, (Uand, Unand, Uor, Unor, Uxor, Uxnor)):
                return 1
            # Logical not (Ulnot) also returns 1 bit
            if isinstance(expr, Ulnot):
                return 1
            # Other unary operators (Uplus, Uminus, Unot) have same width as operand
            return self._get_expr_width(expr.right, scope)

        # Conditional operator (must check before Operator, as Cond may be a subclass)
        if isinstance(expr, Cond):
            true_width = self._get_expr_width(expr.true_value, scope)
            false_width = self._get_expr_width(expr.false_value, scope)
            if true_width and false_width:
                return max(true_width, false_width)
            return true_width or false_width

        # Binary operators
        if isinstance(expr, Operator):
            return self._calc_operator_width(expr, scope)

        # Pointer (vector bit-select like a[0] or array access like mem[i])
        if isinstance(expr, Pointer):
            # Check if the variable is an array or a vector
            if isinstance(expr.var, Identifier):
                symbol = scope.lookup_symbol(expr.var.name)
                if symbol:
                    # If it's an array, return element width
                    if symbol.array_dimensions:
                        return symbol.width
                    # If it's a vector (no array dimensions), bit-select returns 1-bit
                    return 1
            # For nested pointers or other cases, fall back to operand width
            return self._get_expr_width(expr.var, scope)

        return None

    def _parse_int_width(self, int_const: IntConst) -> Optional[int]:
        """Parse width from integer constant"""
        try:
            value = str(int_const.value)

            # Check for sized constant like "2'b10", "8'hFF", "16'd255"
            # Format: <size>'<base><value>
            if "'" in value:
                parts = value.split("'")
                if len(parts) >= 2:
                    size_str = parts[0]
                    # size_str could be empty (e.g., 'b10) or a number (e.g., 2'b10)
                    if size_str and size_str.isdigit():
                        return int(size_str)
                    # If no size prefix, check if it's an unsized literal starting with '
                    elif not size_str:
                        # Unsized literal like 'b10, 'hFF - estimate from value
                        val_part = parts[1]
                        if val_part:
                            base = val_part[0].lower()
                            val_str = val_part[1:]
                            if base == 'b':
                                return len(val_str)
                            elif base == 'h':
                                return len(val_str) * 4
                            elif base == 'd':
                                return int(val_str).bit_length()

            # Unsized constant - estimate bits needed
            val = int(value.replace("'b", "").replace("'h", "").replace("'d", ""), 0)
            if val == 0:
                return 1
            return val.bit_length()
        except (ValueError, TypeError):
            return None

    def _calc_partselect_width(self, partselect: Partselect, scope=None) -> Optional[int]:
        """Calculate width of part-select [msb:lsb]"""
        try:
            msb = self._eval_const_expr(partselect.msb, scope)
            lsb = self._eval_const_expr(partselect.lsb, scope)
            if msb is not None and lsb is not None:
                return msb - lsb + 1
        except:
            pass
        return None

    def _expr_contains_arithmetic(self, expr) -> bool:
        """Check if expression contains Plus or Minus operation (arithmetic)"""
        if expr is None:
            return False

        # Direct Plus/Minus
        if isinstance(expr, (Plus, Minus)):
            return True

        # Check inside Lvalue/Rvalue
        if isinstance(expr, (Lvalue, Rvalue)):
            return self._expr_contains_arithmetic(expr.var)

        # Check inside Cond (both branches)
        if isinstance(expr, Cond):
            return (self._expr_contains_arithmetic(expr.true_value) or
                    self._expr_contains_arithmetic(expr.false_value))

        # Check inside binary operators
        if isinstance(expr, Operator):
            if hasattr(expr, 'left') and self._expr_contains_arithmetic(expr.left):
                return True
            if hasattr(expr, 'right') and self._expr_contains_arithmetic(expr.right):
                return True

        return False

    def _expr_contains_parameter(self, expr, scope) -> bool:
        """Check if expression contains parameter reference"""
        if expr is None:
            return False

        if isinstance(expr, Identifier) and scope is not None:
            symbol = scope.lookup_symbol(expr.name)
            if symbol and symbol.type.name == "PARAMETER":
                return True
            return False

        if isinstance(expr, (Lvalue, Rvalue)):
            return self._expr_contains_parameter(expr.var, scope)

        # Handle binary operators
        if isinstance(expr, Operator):
            if hasattr(expr, 'left') and hasattr(expr, 'right'):
                return (self._expr_contains_parameter(expr.left, scope) or
                        self._expr_contains_parameter(expr.right, scope))
            # Handle unary operators (Uor, Uand, etc.)
            elif hasattr(expr, 'right'):
                return self._expr_contains_parameter(expr.right, scope)
            return False

        if isinstance(expr, Partselect):
            # Check the partselect range expressions
            result = self._expr_contains_parameter(expr.var, scope)
            if hasattr(expr, 'msb') and self._expr_contains_parameter(expr.msb, scope):
                return True
            if hasattr(expr, 'lsb') and self._expr_contains_parameter(expr.lsb, scope):
                return True
            return result

        return False

    def _is_parametrized_signal(self, expr, scope) -> bool:
        """Check if the signal declaration uses parameters (for LHS)"""
        if expr is None or scope is None:
            return False

        # Extract identifier from Lvalue/Rvalue
        if isinstance(expr, (Lvalue, Rvalue)):
            expr = expr.var

        if isinstance(expr, Identifier):
            symbol = scope.lookup_symbol(expr.name)
            if symbol and hasattr(symbol, 'node') and symbol.node:
                # Check if the declaration width contains parameters
                node = symbol.node

                # Handle Ioport (first=Input/Output, second=Wire/Reg with width)
                if hasattr(node, 'second') and node.second and hasattr(node.second, 'width'):
                    node = node.second

                if hasattr(node, 'width') and node.width:
                    if hasattr(node.width, 'msb') and self._expr_contains_parameter(node.width.msb, scope):
                        return True
                    if hasattr(node.width, 'lsb') and self._expr_contains_parameter(node.width.lsb, scope):
                        return True
            return False

        if isinstance(expr, Partselect):
            return self._is_parametrized_signal(expr.var, scope)

        return False

    def _get_param_actual_width(self, symbol, scope) -> Optional[int]:
        """Get actual bit width of a parameter from its value"""
        if symbol is None:
            return None

        # Try to get the value from symbol
        value = None

        # Try param_value (may be wrapped in Rvalue)
        if hasattr(symbol, 'param_value') and symbol.param_value is not None:
            val = symbol.param_value
            if isinstance(val, Rvalue):
                val = val.var
            value = self._eval_const_expr(val, scope)

        # Try const_value
        if value is None and hasattr(symbol, 'const_value') and symbol.const_value is not None:
            value = symbol.const_value

        if value is not None:
            # Calculate bit width needed to represent this value
            if value == 0:
                return 1
            # For positive numbers, calculate bits needed
            if value > 0:
                return value.bit_length()
            # For negative numbers, handle sign bit
            return (value + 1).bit_length() + 1

        return None

    def _calc_operator_width(self, expr: Operator, scope) -> Optional[int]:
        """Calculate result width of binary operator"""
        left_width = self._get_expr_width(expr.left, scope)
        right_width = self._get_expr_width(expr.right, scope)

        if left_width is None or right_width is None:
            return None

        # Different operators have different width rules
        if isinstance(expr, (Plus, Minus, Times)):
            # Arithmetic: return max width (no carry bit)
            # Designer is responsible for overflow handling
            return max(left_width, right_width)

        if isinstance(expr, (Land, Lor, Eq, NotEq, Eql, NotEql, LessThan, GreaterThan, LessEq, GreaterEq)): # pyright: ignore[reportUndefinedVariable]
            # Comparison/logical: result is 1 bit
            return 1

        if isinstance(expr, (Land, Lor, Xor)):
            # Bitwise: max of operands
            return max(left_width, right_width)

        if isinstance(expr, (Sll, Srl, Sra)):
            # Shift: left operand width
            return left_width

        return max(left_width, right_width)

    def _eval_const_expr(self, expr, scope=None) -> Optional[int]:
        """Evaluate constant expression, with optional scope for parameter lookup"""
        if expr is None:
            return None

        if isinstance(expr, IntConst):
            try:
                val = str(expr.value)
                if val.startswith("'"):
                    # Sized literal
                    parts = val.split("'")
                    if len(parts) >= 2:
                        val = parts[1][1:]  # Remove base specifier
                        base = 10
                        if parts[1][0].lower() == 'b':
                            base = 2
                        elif parts[1][0].lower() == 'h':
                            base = 16
                        elif parts[1][0].lower() == 'd':
                            base = 10
                        return int(val, base)
                return int(val, 0)
            except (ValueError, TypeError):
                return None

        # Handle Identifier (parameter reference)
        if isinstance(expr, Identifier) and scope is not None:
            symbol = scope.lookup_symbol(expr.name)
            if symbol and symbol.type.name == "PARAMETER":
                # Try to get parameter value
                if hasattr(symbol, 'param_value') and symbol.param_value is not None:
                    # param_value might be wrapped in Rvalue
                    val = symbol.param_value
                    if isinstance(val, Rvalue):
                        val = val.var
                    return self._eval_const_expr(val, scope)
                if hasattr(symbol, 'const_value') and symbol.const_value is not None:
                    return symbol.const_value
            return None

        # Handle binary operators
        if isinstance(expr, Plus):
            left = self._eval_const_expr(expr.left, scope)
            right = self._eval_const_expr(expr.right, scope)
            if left is not None and right is not None:
                return left + right
            return None

        if isinstance(expr, Minus):
            left = self._eval_const_expr(expr.left, scope)
            right = self._eval_const_expr(expr.right, scope)
            if left is not None and right is not None:
                return left - right
            return None

        if isinstance(expr, Times):
            left = self._eval_const_expr(expr.left, scope)
            right = self._eval_const_expr(expr.right, scope)
            if left is not None and right is not None:
                return left * right
            return None

        if isinstance(expr, Sll):
            left = self._eval_const_expr(expr.left, scope)
            right = self._eval_const_expr(expr.right, scope)
            if left is not None and right is not None:
                return left << right
            return None

        if isinstance(expr, (Srl, Sra)):
            left = self._eval_const_expr(expr.left, scope)
            right = self._eval_const_expr(expr.right, scope)
            if left is not None and right is not None:
                return left >> right
            return None

        # Handle unary minus (e.g., -1)
        if hasattr(expr, "__class__") and expr.__class__.__name__ == "Uminus":
            if hasattr(expr, "right"):
                inner_val = self._eval_const_expr(expr.right, scope)
                if inner_val is not None:
                    return -inner_val

        return None

    def _check_concat_issues(self, expr, lhs_width: Optional[int], lhs_name: str, lineno: int, scope):
        """Check for issues in Concat expression (recursively)"""
        if expr is None:
            return

        # Recursively check nested expressions
        if isinstance(expr, (Lvalue, Rvalue)):
            self._check_concat_issues(expr.var, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Operator):
            if hasattr(expr, 'left'):
                self._check_concat_issues(expr.left, lhs_width, lhs_name, lineno, scope)
            if hasattr(expr, 'right'):
                self._check_concat_issues(expr.right, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Cond):
            self._check_concat_issues(expr.true_value, lhs_width, lhs_name, lineno, scope)
            self._check_concat_issues(expr.false_value, lhs_width, lhs_name, lineno, scope)
            return

        if not isinstance(expr, Concat):
            return

        # Check for duplicate signals in concat
        seen_signals = set()
        duplicates = set()

        def collect_identifiers(e):
            """Recursively collect identifiers from concat"""
            if isinstance(e, Identifier):
                return {e.name}
            elif isinstance(e, Partselect):
                if isinstance(e.var, Identifier):
                    return {e.var.name}
            elif isinstance(e, Pointer):
                if isinstance(e.var, Identifier):
                    return {e.var.name}
            elif isinstance(e, Concat) and e.list:
                ids = set()
                for item in e.list:
                    ids.update(collect_identifiers(item))
                return ids
            return set()

        if expr.list:
            for item in expr.list:
                ids = collect_identifiers(item)
                for sig in ids:
                    if sig in seen_signals:
                        duplicates.add(sig)
                    seen_signals.add(sig)

        for dup in duplicates:
            issue = WidthIssue(
                symbol_name=lhs_name,
                issue_type=WidthIssueType.CONCAT_DUPLICATE,
                lineno=lineno,
                lhs_width=lhs_width or 0,
                rhs_width=0,
                description=f"Signal '{dup}' appears multiple times in concatenation"
            )
            self.issues.append(issue)

    def _check_partselect_issues(self, expr, lhs_name: str, lineno: int, scope):
        """Check for issues in Partselect expression (recursively)"""
        if expr is None:
            return

        # Recursively check nested expressions
        if isinstance(expr, (Lvalue, Rvalue)):
            self._check_partselect_issues(expr.var, lhs_name, lineno, scope)
            return

        if isinstance(expr, Operator):
            if hasattr(expr, 'left'):
                self._check_partselect_issues(expr.left, lhs_name, lineno, scope)
            if hasattr(expr, 'right'):
                self._check_partselect_issues(expr.right, lhs_name, lineno, scope)
            return

        if isinstance(expr, Cond):
            self._check_partselect_issues(expr.true_value, lhs_name, lineno, scope)
            self._check_partselect_issues(expr.false_value, lhs_name, lineno, scope)
            return

        if isinstance(expr, Concat) and expr.list:
            for item in expr.list:
                self._check_partselect_issues(item, lhs_name, lineno, scope)
            return

        if not isinstance(expr, Partselect):
            return

        # Check msb >= lsb
        msb = self._eval_const_expr(expr.msb, scope)
        lsb = self._eval_const_expr(expr.lsb, scope)

        if msb is not None and lsb is not None:
            if msb < lsb:
                issue = WidthIssue(
                    symbol_name=lhs_name,
                    issue_type=WidthIssueType.PARTSELECT_BOUNDS,
                    lineno=lineno,
                    lhs_width=0,
                    rhs_width=0,
                    description=f"Part-select [{msb}:{lsb}] has msb < lsb"
                )
                self.issues.append(issue)

        # Check if partselect exceeds signal's actual bit range
        if isinstance(expr.var, Identifier):
            sig_name = expr.var.name
            symbol = scope.lookup_symbol(sig_name)
            if symbol and msb is not None:
                # Get the signal's declared bit range
                sig_msb = symbol.width_msb
                sig_lsb = symbol.width_lsb

                if sig_msb is not None and sig_lsb is not None:
                    # Calculate valid bit range (handles both [31:0] and [0:31] declarations)
                    sig_min = min(sig_msb, sig_lsb)
                    sig_max = max(sig_msb, sig_lsb)

                    # Check if accessed bit is within the valid range
                    if msb < sig_min or msb > sig_max:
                        issue = WidthIssue(
                            symbol_name=lhs_name,
                            issue_type=WidthIssueType.PARTSELECT_OVERFLOW,
                            lineno=lineno,
                            lhs_width=symbol.width,
                            rhs_width=msb,
                            description=f"Part-select [{msb}:{lsb}] exceeds signal '{sig_name}' valid range [{sig_msb}:{sig_lsb}]"
                        )
                        self.issues.append(issue)
                    # Also check lsb if it's a range part-select
                    if lsb is not None and (lsb < sig_min or lsb > sig_max):
                        issue = WidthIssue(
                            symbol_name=lhs_name,
                            issue_type=WidthIssueType.PARTSELECT_OVERFLOW,
                            lineno=lineno,
                            lhs_width=symbol.width,
                            rhs_width=lsb,
                            description=f"Part-select [{msb}:{lsb}] exceeds signal '{sig_name}' valid range [{sig_msb}:{sig_lsb}]"
                        )
                        self.issues.append(issue)

    def _check_concat_width(self, expr, lhs_width: Optional[int], lhs_name: str, lineno: int, scope):
        """Check if Concat width exceeds LHS width"""
        if expr is None or lhs_width is None:
            return

        # Recursively check nested expressions
        if isinstance(expr, (Lvalue, Rvalue)):
            self._check_concat_width(expr.var, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Operator):
            # For reduction operators (Uand, Uor, etc.), the result is always 1-bit
            # Don't check the width of their operands against LHS width
            if isinstance(expr, (Uand, Unand, Uor, Unor, Uxor, Uxnor, Ulnot)):
                return
            # For comparison operators (Eq, NotEq, LessThan, etc.), the result is always 1-bit
            # Don't check the width of their operands against LHS width
            if isinstance(expr, (Eq, NotEq, Eql, NotEql, LessThan, GreaterThan, LessEq, GreaterEq)):
                return
            if hasattr(expr, 'left'):
                self._check_concat_width(expr.left, lhs_width, lhs_name, lineno, scope)
            if hasattr(expr, 'right'):
                self._check_concat_width(expr.right, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Cond):
            self._check_concat_width(expr.true_value, lhs_width, lhs_name, lineno, scope)
            self._check_concat_width(expr.false_value, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Concat):
            # Check if this Concat is inside a reduction operator (Uand/Uor/etc.)
            # Reduction operators always return 1-bit, so we should skip the width check
            # for Concats that are direct children of reduction operators
            concat_width = self._get_expr_width(expr, scope)
            if concat_width is not None and concat_width > lhs_width:
                issue = WidthIssue(
                    symbol_name=lhs_name,
                    issue_type=WidthIssueType.CONCAT_WIDTH_MISMATCH,
                    lineno=lineno,
                    lhs_width=lhs_width,
                    rhs_width=concat_width,
                    description=f"Concat width ({concat_width}-bit) exceeds LHS width ({lhs_width}-bit)"
                )
                self.issues.append(issue)
            # Also check nested concats
            if expr.list:
                for item in expr.list:
                    self._check_concat_width(item, lhs_width, lhs_name, lineno, scope)
            return

        if isinstance(expr, Repeat):
            # Check repeat width
            repeat_width = self._get_expr_width(expr, scope)
            if repeat_width is not None and repeat_width > lhs_width:
                issue = WidthIssue(
                    symbol_name=lhs_name,
                    issue_type=WidthIssueType.CONCAT_WIDTH_MISMATCH,
                    lineno=lineno,
                    lhs_width=lhs_width,
                    rhs_width=repeat_width,
                    description=f"Repeat width ({repeat_width}-bit) exceeds LHS width ({lhs_width}-bit)"
                )
                self.issues.append(issue)
            # Check nested - Repeat uses 'value' for the repeated expression (as Concat)
            if hasattr(expr, 'value') and expr.value:
                self._check_concat_width(expr.value, lhs_width, lhs_name, lineno, scope)
            return

    def _check_pointer_bounds(self, expr, lhs_name: str, lineno: int, scope):
        """Check if Pointer (array access) is out of bounds"""
        if expr is None:
            return

        # Recursively check nested expressions
        if isinstance(expr, (Lvalue, Rvalue)):
            self._check_pointer_bounds(expr.var, lhs_name, lineno, scope)
            return

        if isinstance(expr, Operator):
            if hasattr(expr, 'left'):
                self._check_pointer_bounds(expr.left, lhs_name, lineno, scope)
            if hasattr(expr, 'right'):
                self._check_pointer_bounds(expr.right, lhs_name, lineno, scope)
            return

        if isinstance(expr, Cond):
            self._check_pointer_bounds(expr.true_value, lhs_name, lineno, scope)
            self._check_pointer_bounds(expr.false_value, lhs_name, lineno, scope)
            return

        if isinstance(expr, Concat) and expr.list:
            for item in expr.list:
                self._check_pointer_bounds(item, lhs_name, lineno, scope)
            return

        if isinstance(expr, Repeat):
            if hasattr(expr, 'value') and expr.value:
                self._check_pointer_bounds(expr.value, lhs_name, lineno, scope)
            return

        if not isinstance(expr, Pointer):
            return

        # Check if pointer (array access or bit-select) is out of bounds
        if isinstance(expr.var, Identifier):
            var_name = expr.var.name
            symbol = scope.lookup_symbol(var_name)
            if not symbol:
                return

            # Get the index value
            index_val = self._eval_const_expr(expr.ptr, scope)
            if index_val is None:
                return

            # Case 1: Array index bounds check
            if symbol.array_dimensions:
                # Assume 1D array for now
                # array_dimensions is [(msb, lsb), ...], e.g., [(0, 3)] means indices 0,1,2,3
                dim_msb, dim_lsb = symbol.array_dimensions[0]
                # Calculate actual bounds
                if dim_msb is not None and dim_lsb is not None:
                    min_idx = min(dim_msb, dim_lsb)
                    max_idx = max(dim_msb, dim_lsb)
                    if index_val < min_idx or index_val > max_idx:
                        issue = WidthIssue(
                            symbol_name=lhs_name,
                            issue_type=WidthIssueType.PARTSELECT_OVERFLOW,
                            lineno=lineno,
                            lhs_width=max_idx - min_idx + 1,
                            rhs_width=index_val,
                            description=f"Array index [{index_val}] out of bounds for '{var_name}' (valid range: {min_idx}-{max_idx})"
                        )
                        self.issues.append(issue)
                return

            # Case 2: Vector bit-select bounds check
            # For signals like [31:16], accessing bit 15 should be an error
            sig_msb = symbol.width_msb
            sig_lsb = symbol.width_lsb
            if sig_msb is not None and sig_lsb is not None:
                sig_min = min(sig_msb, sig_lsb)
                sig_max = max(sig_msb, sig_lsb)
                if index_val < sig_min or index_val > sig_max:
                    issue = WidthIssue(
                        symbol_name=lhs_name,
                        issue_type=WidthIssueType.PARTSELECT_OVERFLOW,
                        lineno=lineno,
                        lhs_width=symbol.width,
                        rhs_width=index_val,
                        description=f"Bit-select [{index_val}] out of bounds for '{var_name}' (valid range: {sig_min}-{sig_max})"
                    )
                    self.issues.append(issue)

    def _compare_widths(self, lhs_width: int, rhs_width: int, lineno: int, name: str,
                        is_arithmetic_result: bool = False, is_param_expression: bool = False):
        """Compare LHS and RHS widths and record issues

        Args:
            is_arithmetic_result: True if RHS is result of arithmetic operation (+-*/)
                                  In this case, width+1 for carry is expected and not an error
            is_param_expression: True if RHS contains parameter - for param expressions
                                 we don't report overflow/width issues
        """
        # For parameter expressions: only check if constant is too large
        if is_param_expression:
            # Don't report truncation or overflow for parameter expressions
            return

        if rhs_width > lhs_width:
            # For arithmetic operations, width+1 for carry is expected
            # Only report as truncation if RHS is significantly wider
            if is_arithmetic_result and rhs_width <= lhs_width + 1:
                # Report as info only - potential overflow from arithmetic carry
                issue = WidthIssue(
                    symbol_name=name,
                    issue_type=WidthIssueType.EXTENSION,  # Use EXTENSION as info level
                    lineno=lineno,
                    lhs_width=lhs_width,
                    rhs_width=rhs_width,
                    description=f"Potential overflow: {lhs_width}-bit LHS assigned arithmetic result with carry ({rhs_width}-bit)"
                )
                self.issues.append(issue)
                return

            # Truncation risk
            issue = WidthIssue(
                symbol_name=name,
                issue_type=WidthIssueType.TRUNCATION,
                lineno=lineno,
                lhs_width=lhs_width,
                rhs_width=rhs_width,
                description=f"Width mismatch: {lhs_width}-bit LHS assigned {rhs_width}-bit RHS (truncation)"
            )
            self.issues.append(issue)
        elif rhs_width < lhs_width:
            # Extension (informational) - but not for parameter expressions
            issue = WidthIssue(
                symbol_name=name,
                issue_type=WidthIssueType.EXTENSION,
                lineno=lineno,
                lhs_width=lhs_width,
                rhs_width=rhs_width,
                description=f"Width mismatch: {lhs_width}-bit LHS assigned {rhs_width}-bit RHS (zero-extended)"
            )
            self.issues.append(issue)

    def _check_instances(self, module_name: str, scope):
        """Check instance port connection widths"""
        # Get module AST
        module_scope = self.stb.get_module_scope(module_name)
        if not module_scope or not module_scope.node:
            return

        module_node = module_scope.node
        if not isinstance(module_node, ModuleDef):
            return

        # Recursively find all instances (including in generate blocks)
        self._collect_instances(module_node, scope)

    def _collect_instances(self, node, scope):
        """Recursively collect and check instances from node"""
        if node is None:
            return

        # Check if this is an InstanceList
        if isinstance(node, InstanceList):
            for instance in node.instances:
                self._check_instance_ports(instance, scope)
            return

        # Handle Block (begin/end blocks)
        if isinstance(node, Block):
            if hasattr(node, 'statements') and node.statements:
                for stmt in node.statements:
                    self._collect_instances(stmt, scope)
            return

        # Handle GenerateStatement (generate blocks)
        if isinstance(node, GenerateStatement):
            if hasattr(node, 'items') and node.items:
                for item in node.items:
                    self._collect_instances(item, scope)
            return

        # Handle ForStatement (generate for loops)
        if isinstance(node, ForStatement):
            # Check the loop body
            if hasattr(node, 'statement') and node.statement:
                self._collect_instances(node.statement, scope)
            return

        # For generate blocks and other compound statements, recursively check children
        if hasattr(node, 'items') and node.items:
            for item in node.items:
                self._collect_instances(item, scope)

        # For generate statements with 'statement' attribute
        if hasattr(node, 'statement') and node.statement:
            self._collect_instances(node.statement, scope)

        # For if/generate statements with true/false branches
        if hasattr(node, 'true_statement') and node.true_statement:
            self._collect_instances(node.true_statement, scope)
        if hasattr(node, 'false_statement') and node.false_statement:
            self._collect_instances(node.false_statement, scope)

        # For case statements
        if hasattr(node, 'caselist') and node.caselist:
            for case in node.caselist:
                if hasattr(case, 'statement') and case.statement:
                    self._collect_instances(case.statement, scope)

    def _check_instance_ports(self, instance: Instance, scope):
        """Check a single instance's port connections"""
        instance_name = instance.name
        module_name = instance.module

        # Get the instantiated module's scope (if available)
        target_scope = self.stb.get_module_scope(module_name)
        if not target_scope:
            # Cannot check if target module not in symbol table
            return

        # Get parameter overrides from the instance symbol (already evaluated by symbol table builder)
        # First try the current scope, then recursively search in child scopes
        instance_symbol = scope.lookup_symbol(instance_name)
        if not instance_symbol:
            # Instance might be in a child scope (e.g., generate block)
            # Pass the line number to find the correct instance when there are multiple
            # instances with the same name in different branches
            instance_symbol = self._find_instance_in_children(scope, instance_name, instance.lineno)

        param_overrides = {}
        if instance_symbol and hasattr(instance_symbol, 'instance_params'):
            param_overrides = instance_symbol.instance_params

        # Check each port connection
        if hasattr(instance, 'portlist') and instance.portlist:
            # portlist could be a tuple or a Portlist object
            ports = instance.portlist
            if hasattr(ports, 'ports'):
                ports = ports.ports
            for port in ports:
                if hasattr(port, 'portname') and hasattr(port, 'argname'):
                    port_name = port.portname
                    arg_expr = port.argname

                    # Get port width from target module, considering parameter overrides
                    port_width = self._get_port_width_with_params(target_scope, port_name, param_overrides)

                    # Get argument width from current scope
                    arg_width = self._get_expr_width(arg_expr, scope)

                    if port_width is not None and arg_width is not None:
                        if arg_width != port_width:
                            issue = WidthIssue(
                                symbol_name=f"{instance_name}.{port_name}",
                                issue_type=WidthIssueType.PORT_MISMATCH,
                                lineno=instance.lineno,
                                lhs_width=port_width,
                                rhs_width=arg_width,
                                description=f"Instance '{instance_name}' port '{port_name}': "
                                           f"expected {port_width}-bit, got {arg_width}-bit"
                            )
                            self.issues.append(issue)

    def _find_instance_in_children(self, scope, instance_name: str, lineno: int = None):
        """
        Recursively search for an instance symbol in child scopes.
        This is needed for instances inside generate blocks.

        Args:
            scope: The scope to search in
            instance_name: Name of the instance to find
            lineno: Optional line number to match the correct instance
                   (needed when multiple instances have the same name in different branches)
        """
        # Check direct children
        for child in scope.children:
            symbol = child.get_symbol(instance_name)
            if symbol and symbol.type.name == 'INSTANCE':
                # If lineno is provided, check if this is the right instance
                if lineno is not None and symbol.node and hasattr(symbol.node, 'lineno'):
                    if symbol.node.lineno == lineno:
                        return symbol
                elif lineno is None:
                    return symbol
            # Recursively search in grand-children
            result = self._find_instance_in_children(child, instance_name, lineno)
            if result:
                return result
        return None

    def _get_port_width_with_params(self, scope, port_name: str, param_overrides: Dict[str, int]) -> Optional[int]:
        """
        Get width of a port from module scope, considering parameter overrides

        Args:
            scope: Target module's scope
            port_name: Name of the port
            param_overrides: Dictionary of parameter name -> override value

        Returns:
            Port width considering parameter overrides, or None if unknown
        """
        # Get the port symbol
        port_symbol = None
        if port_name in scope.inputs:
            port_symbol = scope.inputs[port_name]
        elif port_name in scope.outputs:
            port_symbol = scope.outputs[port_name]
        else:
            port_symbol = scope.lookup_symbol(port_name)

        if not port_symbol:
            return None

        # If no parameter overrides, return the default width
        if not param_overrides:
            return port_symbol.width

        # Check if the port's width depends on parameters
        # by looking at the declaration node
        if hasattr(port_symbol, 'node') and port_symbol.node:
            node = port_symbol.node

            # Handle Ioport (first=Input/Output, second=Wire/Reg with width)
            # For Ioport, the width is typically in node.first (the Input/Output)
            if hasattr(node, 'second') and node.second and hasattr(node.second, 'width'):
                node = node.second
            elif hasattr(node, 'first') and node.first and hasattr(node.first, 'width'):
                node = node.first

            if hasattr(node, 'width') and node.width:
                width_node = node.width
                # Check if width contains parameter references
                if hasattr(width_node, 'msb') and hasattr(width_node, 'lsb'):
                    # Try to evaluate msb and lsb with parameter overrides
                    msb_val = self._eval_expr_with_params(width_node.msb, scope, param_overrides)
                    lsb_val = self._eval_expr_with_params(width_node.lsb, scope, param_overrides)

                    if msb_val is not None and lsb_val is not None:
                        return abs(msb_val - lsb_val) + 1

        # Fall back to default width
        return port_symbol.width

    def _eval_expr_with_params(self, expr, scope, param_overrides: Dict[str, int]) -> Optional[int]:
        """
        Evaluate an expression, substituting parameter values from overrides

        Args:
            expr: Expression to evaluate
            scope: Current scope
            param_overrides: Dictionary of parameter name -> override value

        Returns:
            Evaluated integer value or None if cannot evaluate
        """
        if expr is None:
            return None

        # Integer constant
        if isinstance(expr, IntConst):
            return self._eval_const_expr(expr, scope)

        # Identifier (parameter reference)
        if isinstance(expr, Identifier):
            # First check if this parameter has an override
            if expr.name in param_overrides:
                override_val = param_overrides[expr.name]
                # If the override value is an AST node, evaluate it recursively
                if isinstance(override_val, (IntConst, Identifier, Plus, Minus, Times, Divide, Mod,
                                              And, Or, Xor, Land, Lor, Sll, Srl, Sra)):
                    return self._eval_expr_with_params(override_val, scope, param_overrides)
                elif isinstance(override_val, int):
                    return override_val
                return None
            # Otherwise, evaluate normally
            return self._eval_const_expr(expr, scope)

        # Binary operators
        if isinstance(expr, Plus):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left + right
            return None

        if isinstance(expr, Minus):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left - right
            return None

        if isinstance(expr, Times):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left * right
            return None

        if isinstance(expr, Divide):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None and right != 0:
                return left // right
            return None

        if isinstance(expr, Mod):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None and right != 0:
                return left % right
            return None

        if isinstance(expr, And):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left & right
            return None

        if isinstance(expr, Or):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left | right
            return None

        if isinstance(expr, Sll):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left << right
            return None

        if isinstance(expr, (Srl, Sra)):
            left = self._eval_expr_with_params(expr.left, scope, param_overrides)
            right = self._eval_expr_with_params(expr.right, scope, param_overrides)
            if left is not None and right is not None:
                return left >> right
            return None

        if isinstance(expr, Cond):
            # Conditional expression: cond ? true_value : false_value
            cond_val = self._eval_expr_with_params(expr.cond, scope, param_overrides)
            true_val = self._eval_expr_with_params(expr.true_value, scope, param_overrides)
            false_val = self._eval_expr_with_params(expr.false_value, scope, param_overrides)
            # For parameter expressions, we evaluate both branches and return the matching one
            # If condition evaluates to truthy, return true_val, else false_val
            if cond_val is not None:
                return true_val if cond_val else false_val
            # If we can't evaluate condition, try to return a value if both branches are equal
            if true_val is not None and false_val is not None and true_val == false_val:
                return true_val
            return None

        # Fallback to standard evaluation
        return self._eval_const_expr(expr, scope)

    def _get_port_width(self, scope, port_name: str) -> Optional[int]:
        """Get width of a port from module scope"""
        # Check inputs
        if port_name in scope.inputs:
            return scope.inputs[port_name].width
        # Check outputs
        if port_name in scope.outputs:
            return scope.outputs[port_name].width
        # Check all symbols
        symbol = scope.lookup_symbol(port_name)
        if symbol:
            return symbol.width
        return None

    def print_report(self):
        """Print width check report"""
        print("\n" + "=" * 70)
        print("Width Check Report")
        print("=" * 70)

        if not self.issues:
            print("No width issues found")
            return

        # Group by type
        truncations = [i for i in self.issues if i.issue_type == WidthIssueType.TRUNCATION]
        extensions = [i for i in self.issues if i.issue_type == WidthIssueType.EXTENSION]
        port_mismatches = [i for i in self.issues if i.issue_type == WidthIssueType.PORT_MISMATCH]
        partselect_bounds = [i for i in self.issues if i.issue_type == WidthIssueType.PARTSELECT_BOUNDS]
        partselect_overflow = [i for i in self.issues if i.issue_type == WidthIssueType.PARTSELECT_OVERFLOW]
        concat_duplicates = [i for i in self.issues if i.issue_type == WidthIssueType.CONCAT_DUPLICATE]
        concat_width_mismatch = [i for i in self.issues if i.issue_type == WidthIssueType.CONCAT_WIDTH_MISMATCH]

        if truncations:
            print(f"\n[!] Truncation Risks ({len(truncations)}):")
            for issue in truncations:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.lhs_width}b < {issue.rhs_width}b")

        if extensions:
            print(f"\n[i] Extensions ({len(extensions)}):")
            for issue in extensions:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.lhs_width}b > {issue.rhs_width}b")

        if port_mismatches:
            print(f"\n[!] Instance Port Mismatches ({len(port_mismatches)}):")
            for issue in port_mismatches:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:30s} - expected {issue.lhs_width}b, got {issue.rhs_width}b")

        if partselect_bounds:
            print(f"\n[!] Part-select Bounds Errors ({len(partselect_bounds)}):")
            for issue in partselect_bounds:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.description}")

        if partselect_overflow:
            print(f"\n[!] Part-select Overflow ({len(partselect_overflow)}):")
            for issue in partselect_overflow:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.description}")

        if concat_duplicates:
            print(f"\n[!] Concat Duplicates ({len(concat_duplicates)}):")
            for issue in concat_duplicates:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.description}")

        if concat_width_mismatch:
            print(f"\n[!] Concat/Repeat Width Mismatch ({len(concat_width_mismatch)}):")
            for issue in concat_width_mismatch:
                print(f"  Line {issue.lineno:3d}: {issue.symbol_name:20s} - {issue.description}")

        print(f"\nTotal: {len(self.issues)} width issues")


def check_width(dfg_builder: DFGBuilder) -> List[WidthIssue]:
    """Convenience function for width checking"""
    checker = WidthChecker(dfg_builder)
    issues = checker.check()
    return issues
