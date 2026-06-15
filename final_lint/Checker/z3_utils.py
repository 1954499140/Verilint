"""
Z3 Utils - Z3 Solver 工具模块

提供Z3表达式转换、条件检查和可达性分析功能
"""

from typing import List, Set, Dict, Tuple, Optional, Any, Union
from dataclasses import dataclass
import sys
import os

# Try to import Z3 solver
try:
    import z3
    from z3 import Solver, And, Or, Not, sat, unsat, BitVec, BitVecVal, UGE, UGT, ULE, ULT
    Z3_AVAILABLE = True
except ImportError:
    Z3_AVAILABLE = False

# Import Pyverilog AST types
from pyverilog.vparser.ast import (
    Identifier, IntConst, Eq, NotEq, LessEq, GreaterEq, LessThan, GreaterThan,
    Land, Lor, Ulnot, And, Or, Xor
)


@dataclass
class Z3Condition:
    """Z3条件表示"""
    expr: Any           # Z3表达式
    variables: Set[str] # 依赖的变量
    original: Any       # 原始AST节点


class Z3Converter:
    """
    Z3表达式转换器
    将Verilog表达式转换为Z3表达式
    """

    def __init__(self, scope_lookup_func=None):
        self.scope_lookup_func = scope_lookup_func
        self.z3_vars: Dict[str, Any] = {}

    def _create_var(self, var_name: str, width: int = 32):
        """创建或获取Z3变量（使用BitVec表示Verilog无符号整数）"""
        cache_key = f"{var_name}_{width}"
        if cache_key not in self.z3_vars:
            self.z3_vars[cache_key] = BitVec(var_name, width)
        return self.z3_vars[cache_key]

    def _eval_const_expr(self, expr) -> Optional[int]:
        """评估常量表达式"""
        if isinstance(expr, IntConst):
            try:
                val = str(expr.value)
                if "'" in val:
                    parts = val.split("'")
                    if len(parts) >= 2:
                        val_part = parts[1]
                        if val_part:
                            base_char = val_part[0].lower()
                            value_str = val_part[1:]
                            if base_char == 'h':
                                return int(value_str, 16)
                            elif base_char == 'b':
                                return int(value_str, 2)
                            elif base_char == 'd':
                                return int(value_str, 10)
                            elif base_char == 'o':
                                return int(value_str, 8)
                return int(val, 0)
            except (ValueError, TypeError):
                return None
        return None

    def _get_variable_width(self, var_name: str) -> Optional[int]:
        """获取变量的位宽"""
        if self.scope_lookup_func:
            symbol = self.scope_lookup_func(var_name)
            if symbol:
                if hasattr(symbol, 'width') and symbol.width:
                    return symbol.width
                if hasattr(symbol, 'width_msb') and hasattr(symbol, 'width_lsb'):
                    if symbol.width_msb is not None and symbol.width_lsb is not None:
                        return abs(symbol.width_msb - symbol.width_lsb) + 1
        return None

    def _match_bit_width(self, expr1, expr2):
        """匹配两个Z3表达式的位宽"""
        if isinstance(expr1, z3.BitVecRef) and isinstance(expr2, z3.BitVecRef):
            width1 = expr1.size()
            width2 = expr2.size()
            if width1 < width2:
                expr1 = z3.ZeroExt(width2 - width1, expr1)
            elif width2 < width1:
                expr2 = z3.ZeroExt(width1 - width2, expr2)
        return expr1, expr2

    def to_z3(self, expr, width: int = 32) -> Optional[Z3Condition]:
        """
        将Verilog表达式转换为Z3表达式
        """
        if not Z3_AVAILABLE:
            return None

        try:
            # 标识符 -> Z3变量
            if isinstance(expr, Identifier):
                var_name = expr.name
                actual_width = self._get_variable_width(var_name) or width
                z3_var = self._create_var(var_name, actual_width)
                return Z3Condition(z3_var, {var_name}, expr)

            # 整数常量 -> Z3 BitVecVal
            if isinstance(expr, IntConst):
                val = self._eval_const_expr(expr)
                if val is not None:
                    mask = (1 << width) - 1 if width < 64 else 0xFFFFFFFFFFFFFFFF
                    return Z3Condition(BitVecVal(val & mask, width), set(), expr)
                return None

            # 相等比较 (==)
            if isinstance(expr, Eq):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(left_expr == right_expr, left.variables | right.variables, expr)
                return None

            # 不等比较 (!=)
            if isinstance(expr, NotEq):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(left_expr != right_expr, left.variables | right.variables, expr)
                return None

            # 小于 (<) - 无符号比较
            if isinstance(expr, LessThan):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(ULT(left_expr, right_expr), left.variables | right.variables, expr)
                return None

            # 大于 (>) - 无符号比较
            if isinstance(expr, GreaterThan):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(UGT(left_expr, right_expr), left.variables | right.variables, expr)
                return None

            # 小于等于 (<=) - 无符号比较
            if isinstance(expr, LessEq):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(ULE(left_expr, right_expr), left.variables | right.variables, expr)
                return None

            # 大于等于 (>=) - 无符号比较
            if isinstance(expr, GreaterEq):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(UGE(left_expr, right_expr), left.variables | right.variables, expr)
                return None

            # 逻辑与 (&&)
            if isinstance(expr, Land):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_width = left.expr.size()
                    right_width = right.expr.size()
                    target_width = max(left_width, right_width)
                    if left_width < target_width:
                        left.expr = z3.ZeroExt(target_width - left_width, left.expr)
                    if right_width < target_width:
                        right.expr = z3.ZeroExt(target_width - right_width, right.expr)
                    left_bool = left.expr != BitVecVal(0, target_width)
                    right_bool = right.expr != BitVecVal(0, target_width)
                    return Z3Condition(And(left_bool, right_bool), left.variables | right.variables, expr)
                return None

            # 逻辑或 (||)
            if isinstance(expr, Lor):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_width = left.expr.size()
                    right_width = right.expr.size()
                    target_width = max(left_width, right_width)
                    if left_width < target_width:
                        left.expr = z3.ZeroExt(target_width - left_width, left.expr)
                    if right_width < target_width:
                        right.expr = z3.ZeroExt(target_width - right_width, right.expr)
                    left_bool = left.expr != BitVecVal(0, target_width)
                    right_bool = right.expr != BitVecVal(0, target_width)
                    return Z3Condition(z3.Or(left_bool, right_bool), left.variables | right.variables, expr)
                return None

            # 逻辑非 (!)
            if isinstance(expr, Ulnot):
                operand = self.to_z3(expr.right, width)
                if operand:
                    operand_width = operand.expr.size()
                    operand_bool = operand.expr != BitVecVal(0, operand_width)
                    return Z3Condition(Not(operand_bool), operand.variables, expr)
                return None

            # 位与 (&)
            if isinstance(expr, And):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(left_expr & right_expr, left.variables | right.variables, expr)
                return None

            # 位或 (|)
            if isinstance(expr, Or):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(left_expr | right_expr, left.variables | right.variables, expr)
                return None

            # 位异或 (^)
            if isinstance(expr, Xor):
                left = self.to_z3(expr.left, width)
                right = self.to_z3(expr.right, width)
                if left and right:
                    left_expr, right_expr = self._match_bit_width(left.expr, right.expr)
                    return Z3Condition(left_expr ^ right_expr, left.variables | right.variables, expr)
                return None

        except Exception as e:
            # print(f"[Z3] Error converting expression: {e}")
            return None

        return None


class Z3Checker:
    """
    Z3条件检查器
    使用Z3 solver进行条件分析和验证
    """

    def __init__(self, converter: Z3Converter = None):
        self.converter = converter or Z3Converter()

    def is_reachable(self, cond) -> Optional[bool]:
        """
        检查条件是否可达
        返回: True(可达), False(不可达), None(无法确定)
        """
        if not Z3_AVAILABLE:
            return None

        z3_cond = self.converter.to_z3(cond)
        if not z3_cond:
            return None

        try:
            solver = Solver()
            expr = z3_cond.expr

            if isinstance(expr, z3.BoolRef):
                solver.add(expr)
            elif isinstance(expr, z3.BitVecRef):
                solver.add(expr != BitVecVal(0, expr.size()))
            else:
                return None

            result = solver.check()
            if result == unsat:
                return False
            elif result == sat:
                return True
            else:
                return None
        except Exception as e:
            # print(f"[Z3] Error checking reachability: {e}")
            return None

    def are_mutex(self, cond1, cond2) -> Optional[bool]:
        """
        检查两个条件是否互斥
        返回: True(互斥), False(可同时为真), None(无法确定)
        """
        if not Z3_AVAILABLE:
            return None

        z3_cond1 = self.converter.to_z3(cond1)
        z3_cond2 = self.converter.to_z3(cond2)

        if not z3_cond1 or not z3_cond2:
            return None

        try:
            expr1 = z3_cond1.expr
            expr2 = z3_cond2.expr

            if isinstance(expr1, z3.BitVecRef):
                expr1 = (expr1 != BitVecVal(0, expr1.size()))
            if isinstance(expr2, z3.BitVecRef):
                expr2 = (expr2 != BitVecVal(0, expr2.size()))

            if not isinstance(expr1, z3.BoolRef) or not isinstance(expr2, z3.BoolRef):
                return None

            solver = Solver()
            solver.add(expr1)
            solver.add(expr2)

            result = solver.check()
            if result == unsat:
                return True
            elif result == sat:
                return False
            else:
                return None
        except Exception as e:
            print(f"[Z3] Error checking mutex: {e}")
            return None

    def check_if_else(self, cond) -> Tuple[Optional[bool], Optional[bool]]:
        """
        检查if-else分支的可达性
        返回: (if_branch_reachable, else_branch_reachable)
        """
        if not Z3_AVAILABLE:
            return None, None

        z3_cond = self.converter.to_z3(cond)
        if not z3_cond:
            return None, None

        try:
            expr = z3_cond.expr
            if isinstance(expr, z3.BitVecRef):
                cond_bool = (expr != BitVecVal(0, expr.size()))
            elif isinstance(expr, z3.BoolRef):
                cond_bool = expr
            else:
                return None, None

            solver = Solver()

            # 检查 if 分支
            solver.push()
            solver.add(cond_bool)
            if_result = solver.check()
            solver.pop()

            # 检查 else 分支
            solver.push()
            solver.add(Not(cond_bool))
            else_result = solver.check()
            solver.pop()

            if_reachable = None
            else_reachable = None

            if if_result == sat:
                if_reachable = True
            elif if_result == unsat:
                if_reachable = False

            if else_result == sat:
                else_reachable = True
            elif else_result == unsat:
                else_reachable = False

            return if_reachable, else_reachable

        except Exception as e:
            # print(f"[Z3] Error in if-else analysis: {e}")
            return None, None


def is_z3_available() -> bool:
    """检查Z3是否可用"""
    return Z3_AVAILABLE
