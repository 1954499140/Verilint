"""
Verilint Checker - 综合检查器

整合所有检查器，提供统一的检查接口和错误格式。
支持 VSCode 集成输出。
"""
import time
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
import os
import sys
import hashlib
import time
import tempfile
import subprocess
import re

# 全局项目缓存: project_root -> {timestamp, modules, file_list}
_project_cache: Dict[str, Dict] = {}

from pyverilog.vparser.parser import parse

from Checker.width_checker import WidthChecker
from Checker.cycle_checker import CycleChecker, CycleIssue, CycleType
from symbol_table_builder import SymbolTableBuilder
from dfg_builder import DFGBuilder
from Checker.register_checker import RegisterChecker, RegisterIssue, RegisterIssueType
from Checker.instance_checker import InstanceChecker, InstanceIssue, InstanceIssueType
from Checker.glitch_checker import GlitchChecker, GlitchIssue, GlitchIssueType
from Checker.combdly_checker import CombdlyChecker, CombdlyIssue, CombdlyIssueType
from Checker.fsm_checker import FSMChecker, FSMIssue, FSMIssueType
from Checker.branch_coverage_checker import BranchCoverageChecker, BranchIssue, BranchIssueType
from Checker.reset_checker import ResetChecker, ResetIssue, ResetIssueType
from Checker.array_bound_checker import ArrayBoundChecker, ArrayBoundIssue, ArrayBoundIssueType
from Checker.width_checker import WidthChecker, WidthIssue, WidthIssueType


class Severity(Enum):
    """问题严重级别"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Category(Enum):
    """问题类别"""
    REGISTER = "register"           # 寄存器使用问题
    INSTANCE = "instance"           # 实例化问题
    GLITCH = "glitch"               # 毛刺问题
    COMBDLY = "combdly"             # 敏感列表问题
    FSM = "fsm"                     # FSM 问题
    BRANCH = "branch"               # 分支覆盖问题
    RESET = "reset"                 # 复位问题
    ARRAY = "array"                 # 数组越界问题
    WIDTH = "width"                 # 位宽问题
    CYCLE = "cycle"                 # 循环依赖问题
    SYNTAX = "syntax"               # 语法问题


class CacheConfig:
    """缓存配置类 - 控制项目解析缓存的行为"""

    # 全局缓存开关
    ENABLED: bool = True

    # 内存缓存开关
    MEMORY_CACHE_ENABLED: bool = True

    # 文件缓存开关
    FILE_CACHE_ENABLED: bool = True

    # 调试模式 - 输出详细缓存操作信息
    DEBUG: bool = False

    # 缓存统计信息
    _stats: Dict[str, int] = {
        'memory_hits': 0,      # 内存缓存命中次数
        'file_hits': 0,        # 文件缓存命中次数
        'misses': 0,           # 缓存未命中次数
        'clears': 0,           # 缓存清空次数
    }

    @classmethod
    def enable(cls):
        """启用缓存"""
        cls.ENABLED = True
        print("[CacheConfig] 缓存已启用")

    @classmethod
    def disable(cls):
        """禁用缓存"""
        cls.ENABLED = False
        print("[CacheConfig] 缓存已禁用")

    @classmethod
    def set_debug(cls, debug: bool):
        """设置调试模式"""
        cls.DEBUG = debug
        print(f"[CacheConfig] 调试模式: {'开启' if debug else '关闭'}")

    @classmethod
    def reset_stats(cls):
        """重置统计信息"""
        cls._stats = {
            'memory_hits': 0,
            'file_hits': 0,
            'misses': 0,
            'clears': 0,
        }
        print("[CacheConfig] 统计信息已重置")

    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """获取缓存统计信息"""
        return cls._stats.copy()

    @classmethod
    def print_stats(cls):
        """打印缓存统计信息"""
        print("\n" + "=" * 50)
        print("Verilint 缓存统计")
        print("=" * 50)
        print(f"  内存缓存命中: {cls._stats['memory_hits']}")
        print(f"  文件缓存命中: {cls._stats['file_hits']}")
        print(f"  缓存未命中:   {cls._stats['misses']}")
        print(f"  缓存清空:     {cls._stats['clears']}")
        total_hits = cls._stats['memory_hits'] + cls._stats['file_hits']
        total = total_hits + cls._stats['misses']
        if total > 0:
            hit_rate = (total_hits / total) * 100
            print(f"  命中率:       {hit_rate:.1f}%")
        print("=" * 50)

    @classmethod
    def _record_memory_hit(cls):
        """记录内存缓存命中"""
        cls._stats['memory_hits'] += 1

    @classmethod
    def _record_file_hit(cls):
        """记录文件缓存命中"""
        cls._stats['file_hits'] += 1

    @classmethod
    def _record_miss(cls):
        """记录缓存未命中"""
        cls._stats['misses'] += 1

    @classmethod
    def _record_clear(cls):
        """记录缓存清空"""
        cls._stats['clears'] += 1


@dataclass
class VerilintIssue:
    """
    统一的问题格式 - 适用于 VSCode 集成

    字段设计参考 LSP (Language Server Protocol) Diagnostic 格式
    """
    # 基本位置信息
    file_path: str                # 文件路径
    line: int                     # 行号 (1-based)
    column: int                   # 列号 (1-based, 默认为1)

    # 问题描述
    category: Category            # 问题类别
    severity: Severity            # 严重级别
    code: str                     # 错误代码 (如 REG001, INST002)
    message: str                  # 错误消息

    # 详细信息
    related_info: List[Dict] = field(default_factory=list)  # 相关信息
    source: str = "verilint"      # 错误来源

    # 原始信息保留
    raw_issue: Any = None         # 原始问题对象

    def __post_init__(self):
        """确保行号和列号至少为1"""
        if self.line < 1:
            self.line = 1
        if self.column < 1:
            self.column = 1

    def to_vscode_format(self) -> Dict:
        """转换为 VSCode 可以解析的格式"""
        return {
            "file": self.file_path,
            "line": self.line,
            "column": self.column,
            "severity": self.severity.value,
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "relatedInformation": self.related_info
        }

    def to_lsp_diagnostic(self) -> Dict:
        """转换为 LSP Diagnostic 格式"""
        severity_map = {
            Severity.ERROR: 1,      # Error
            Severity.WARNING: 2,    # Warning
            Severity.INFO: 3        # Information
        }

        return {
            "range": {
                "start": {
                    "line": self.line - 1,  # LSP 使用 0-based
                    "character": self.column - 1
                },
                "end": {
                    "line": self.line - 1,
                    "character": self.column  # 简化处理，实际应该计算结束位置
                }
            },
            "severity": severity_map.get(self.severity, 2),
            "code": self.code,
            "source": self.source,
            "message": f"[{self.category.value.upper()}] {self.message}"
        }


class VerilintChecker:
    """
    Verilint 综合检查器

    整合所有检查器，提供统一的检查接口。
    """

    # 错误代码映射
    ERROR_CODES = {
        # 寄存器问题 (REG)
        RegisterIssueType.USE_BEFORE_DRIVE: "REG001",
        RegisterIssueType.DRIVE_WITHOUT_USE: "REG002",
        RegisterIssueType.MULTI_DRIVE: "REG003",

        # 实例化问题 (INST)
        InstanceIssueType.FLOATING_PORT: "INST001",
        InstanceIssueType.CONSTANT_BRANCH: "INST002",
        InstanceIssueType.REVERSED_CONNECTION: "INST003",
        InstanceIssueType.CIRCULAR_DEPENDENCY: "INST004",
        InstanceIssueType.UNRESOLVED_MODULE: "INST005",
        InstanceIssueType.DUPLICATE_INSTANCE: "INST006",
        InstanceIssueType.UNUSED_MODULE: "INST007",

        # 毛刺问题 (GLT)
        GlitchIssueType.INVERTED_SIGNAL_PAIR: "GLT001",

        # 敏感列表问题 (CMB)
        CombdlyIssueType.INCOMPLETE_SENSITIVITY: "CMB001",
        CombdlyIssueType.MISSING_SIGNAL: "CMB002",
        CombdlyIssueType.EXTRA_SIGNAL: "CMB003",

        # FSM 问题 (FSM)
        FSMIssueType.DEAD_STATE: "FSM001",
        FSMIssueType.UNREACHABLE_STATE: "FSM002",
        FSMIssueType.INCOMPLETE_CASE: "FSM003",
        FSMIssueType.MISSING_DEFAULT: "FSM004",
        FSMIssueType.NOT_ONE_HOT: "FSM005",
        FSMIssueType.INVALID_TRANSITION: "FSM006",
        FSMIssueType.STATE_OVERFLOW: "FSM007",

        # 分支覆盖问题 (BRH)
        BranchIssueType.INCOMPLETE_CASE: "BRH001",
        BranchIssueType.MISSING_DEFAULT: "BRH002",
        BranchIssueType.UNREACHABLE_COND: "BRH003",
        BranchIssueType.REDUNDANT_COND: "BRH004",
        BranchIssueType.OVERLAPPING_COND: "BRH005",
        BranchIssueType.MISSING_ELSE: "BRH006",
        BranchIssueType.EMPTY_BRANCH: "BRH007",

        # 复位问题 (RST)
        ResetIssueType.NOT_RESET: "RST001",

        # 数组越界问题 (ARR)
        ArrayBoundIssueType.ARRAY_INDEX_OUT_OF_BOUNDS: "ARR001",
        ArrayBoundIssueType.BIT_SELECT_OUT_OF_BOUNDS: "ARR002",
        ArrayBoundIssueType.VECTOR_OUT_OF_BOUNDS: "ARR003",

        # 位宽问题 (WID)
        WidthIssueType.MISMATCH: "WID001",
        WidthIssueType.TRUNCATION: "WID002",
        WidthIssueType.EXTENSION: "WID003",
        WidthIssueType.OPERAND_MISMATCH: "WID004",
        WidthIssueType.PORT_MISMATCH: "WID005",
        WidthIssueType.PARTSELECT_BOUNDS: "WID006",
        WidthIssueType.PARTSELECT_OVERFLOW: "WID007",
        WidthIssueType.CONCAT_DUPLICATE: "WID008",
        WidthIssueType.CONCAT_WIDTH_MISMATCH: "WID009",

        # 循环依赖问题 (CYC)
        CycleType.COMBINATIONAL_DIRECT: "CYC001",
        CycleType.COMBINATIONAL_INDIRECT: "CYC002",
        CycleType.SEQUENTIAL_CYCLE: "CYC003",
        CycleType.INITIALIZATION_DEADLOCK: "CYC004",
    }

    SEVERITY_MAP = {
        RegisterIssueType.USE_BEFORE_DRIVE: Severity.ERROR,
        RegisterIssueType.DRIVE_WITHOUT_USE: Severity.WARNING,
        RegisterIssueType.MULTI_DRIVE: Severity.ERROR,

        InstanceIssueType.FLOATING_PORT: Severity.WARNING,
        InstanceIssueType.CONSTANT_BRANCH: Severity.ERROR,
        InstanceIssueType.REVERSED_CONNECTION: Severity.ERROR,
        InstanceIssueType.CIRCULAR_DEPENDENCY: Severity.ERROR,
        InstanceIssueType.UNRESOLVED_MODULE: Severity.ERROR,
        InstanceIssueType.DUPLICATE_INSTANCE: Severity.ERROR,
        InstanceIssueType.UNUSED_MODULE: Severity.INFO,

        GlitchIssueType.INVERTED_SIGNAL_PAIR: Severity.WARNING,

        CombdlyIssueType.INCOMPLETE_SENSITIVITY: Severity.ERROR,
        CombdlyIssueType.MISSING_SIGNAL: Severity.WARNING,
        CombdlyIssueType.EXTRA_SIGNAL: Severity.INFO,

        # FSM
        FSMIssueType.DEAD_STATE: Severity.ERROR,
        FSMIssueType.UNREACHABLE_STATE: Severity.WARNING,
        FSMIssueType.INCOMPLETE_CASE: Severity.INFO,
        FSMIssueType.MISSING_DEFAULT: Severity.INFO,
        FSMIssueType.NOT_ONE_HOT: Severity.INFO,
        FSMIssueType.INVALID_TRANSITION: Severity.ERROR,
        FSMIssueType.STATE_OVERFLOW: Severity.ERROR,

        # Branch Coverage
        BranchIssueType.INCOMPLETE_CASE: Severity.WARNING,
        BranchIssueType.MISSING_DEFAULT: Severity.INFO,
        BranchIssueType.UNREACHABLE_COND: Severity.WARNING,
        BranchIssueType.REDUNDANT_COND: Severity.INFO,
        BranchIssueType.OVERLAPPING_COND: Severity.WARNING,
        BranchIssueType.MISSING_ELSE: Severity.INFO,
        BranchIssueType.EMPTY_BRANCH: Severity.INFO,

        # Reset
        ResetIssueType.NOT_RESET: Severity.ERROR,

        # Width
        WidthIssueType.TRUNCATION: Severity.ERROR,
        WidthIssueType.EXTENSION: Severity.INFO,
        WidthIssueType.OPERAND_MISMATCH: Severity.WARNING,
        WidthIssueType.PORT_MISMATCH: Severity.WARNING,
        WidthIssueType.PARTSELECT_BOUNDS: Severity.ERROR,
        WidthIssueType.PARTSELECT_OVERFLOW: Severity.ERROR,
        WidthIssueType.CONCAT_DUPLICATE: Severity.WARNING,
        WidthIssueType.CONCAT_WIDTH_MISMATCH: Severity.ERROR,

        # Cycle
        CycleType.COMBINATIONAL_DIRECT: Severity.ERROR,
        CycleType.COMBINATIONAL_INDIRECT: Severity.ERROR,
        CycleType.SEQUENTIAL_CYCLE: Severity.WARNING,
        CycleType.INITIALIZATION_DEADLOCK: Severity.WARNING,

        # Array Bound
        ArrayBoundIssueType.ARRAY_INDEX_OUT_OF_BOUNDS: Severity.ERROR,
        ArrayBoundIssueType.BIT_SELECT_OUT_OF_BOUNDS: Severity.ERROR,
        ArrayBoundIssueType.VECTOR_OUT_OF_BOUNDS: Severity.ERROR,
    }

    CATEGORY_MAP = {
        RegisterIssueType.USE_BEFORE_DRIVE: Category.REGISTER,
        RegisterIssueType.DRIVE_WITHOUT_USE: Category.REGISTER,
        RegisterIssueType.MULTI_DRIVE: Category.REGISTER,

        InstanceIssueType.FLOATING_PORT: Category.INSTANCE,
        InstanceIssueType.CONSTANT_BRANCH: Category.INSTANCE,
        InstanceIssueType.REVERSED_CONNECTION: Category.INSTANCE,
        InstanceIssueType.CIRCULAR_DEPENDENCY: Category.INSTANCE,
        InstanceIssueType.UNRESOLVED_MODULE: Category.INSTANCE,
        InstanceIssueType.DUPLICATE_INSTANCE: Category.INSTANCE,
        InstanceIssueType.UNUSED_MODULE: Category.INSTANCE,

        GlitchIssueType.INVERTED_SIGNAL_PAIR: Category.GLITCH,

        CombdlyIssueType.INCOMPLETE_SENSITIVITY: Category.COMBDLY,
        CombdlyIssueType.MISSING_SIGNAL: Category.COMBDLY,
        CombdlyIssueType.EXTRA_SIGNAL: Category.COMBDLY,

        # FSM
        FSMIssueType.DEAD_STATE: Category.FSM,
        FSMIssueType.UNREACHABLE_STATE: Category.FSM,
        FSMIssueType.INCOMPLETE_CASE: Category.FSM,
        FSMIssueType.MISSING_DEFAULT: Category.FSM,
        FSMIssueType.NOT_ONE_HOT: Category.FSM,
        FSMIssueType.INVALID_TRANSITION: Category.FSM,
        FSMIssueType.STATE_OVERFLOW: Category.FSM,

        # Branch Coverage
        BranchIssueType.INCOMPLETE_CASE: Category.BRANCH,
        BranchIssueType.MISSING_DEFAULT: Category.BRANCH,
        BranchIssueType.UNREACHABLE_COND: Category.BRANCH,
        BranchIssueType.REDUNDANT_COND: Category.BRANCH,
        BranchIssueType.OVERLAPPING_COND: Category.BRANCH,
        BranchIssueType.MISSING_ELSE: Category.BRANCH,
        BranchIssueType.EMPTY_BRANCH: Category.BRANCH,

        # Reset
        ResetIssueType.NOT_RESET: Category.RESET,

        # Width
        WidthIssueType.TRUNCATION: Category.WIDTH,
        WidthIssueType.EXTENSION: Category.WIDTH,
        WidthIssueType.OPERAND_MISMATCH: Category.WIDTH,
        WidthIssueType.PORT_MISMATCH: Category.WIDTH,
        WidthIssueType.PARTSELECT_BOUNDS: Category.WIDTH,
        WidthIssueType.PARTSELECT_OVERFLOW: Category.WIDTH,
        WidthIssueType.CONCAT_DUPLICATE: Category.WIDTH,
        WidthIssueType.CONCAT_WIDTH_MISMATCH: Category.WIDTH,

        # Cycle
        CycleType.COMBINATIONAL_DIRECT: Category.CYCLE,
        CycleType.COMBINATIONAL_INDIRECT: Category.CYCLE,
        CycleType.SEQUENTIAL_CYCLE: Category.CYCLE,
        CycleType.INITIALIZATION_DEADLOCK: Category.CYCLE,

        # Array Bound
        ArrayBoundIssueType.ARRAY_INDEX_OUT_OF_BOUNDS: Category.ARRAY,
        ArrayBoundIssueType.BIT_SELECT_OUT_OF_BOUNDS: Category.ARRAY,
        ArrayBoundIssueType.VECTOR_OUT_OF_BOUNDS: Category.ARRAY,
    }

    def __init__(self, file_path: str, debug: bool = False, include_paths: List[str] = None, project: Any = None, ignored_codes: List[str] = None):
        self.file_path = file_path
        self.debug = debug
        self.include_paths: List[str] = include_paths or []
        self.project = project  # 关联的 Project 对象，用于模块查询
        self.ignored_codes: List[str] = ignored_codes or []  # 要忽略的错误代码列表
        self.issues: List[VerilintIssue] = []

        # 解析结果
        self.ast = None
        self.stb: Optional[SymbolTableBuilder] = None
        self.dfg_builder: Optional[DFGBuilder] = None
        self._line_mapping: Optional[Dict[int, Tuple[str, int]]] = None  # 行号映射

    def _dbg(self, msg: str):
        if self.debug:
            import sys
            print(f"[DEBUG] {msg}", file=sys.stderr)

    def _preprocess_with_line_mapping(self) -> Tuple[str, Dict[int, Tuple[str, int]]]:
        """
        Python实现的预处理器，处理 `include 并保留行号映射

        Returns:
            (preprocessed_content, line_mapping)
            line_mapping: 预处理后的行号 -> (原始文件路径, 原始行号)
        """
        line_mapping = {}
        output_lines = []

        # 已处理文件集合，防止循环包含
        processed_files = set()

        def process_file(file_path: str, current_line: int = 1):
            """递归处理文件，展开 include"""
            if file_path in processed_files:
                return
            processed_files.add(file_path)

            abs_path = os.path.abspath(file_path)

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except Exception as e:
                self._dbg(f"Failed to read {file_path}: {e}")
                return

            i = 0
            while i < len(lines):
                line = lines[i]
                line_content = line.rstrip('\n\r')

                # 检查是否是 `include 行
                include_match = re.match(r'`include\s+"([^"]+)"', line_content.strip())

                if include_match:
                    include_file = include_match.group(1)

                    # 查找 include 文件
                    include_path = None

                    # 首先在当前文件目录查找
                    current_dir = os.path.dirname(os.path.abspath(file_path))
                    candidate = os.path.join(current_dir, include_file)
                    if os.path.exists(candidate):
                        include_path = candidate

                    # 然后在 include 路径中查找
                    if not include_path:
                        for inc_dir in self.include_paths or []:
                            candidate = os.path.join(inc_dir, include_file)
                            if os.path.exists(candidate):
                                include_path = candidate
                                break

                    if include_path and os.path.exists(include_path):
                        # 递归处理 include 文件
                        if self.debug:
                            print(f"[Preprocess] Found include: {include_file} -> {include_path}")
                        process_file(include_path, 1)
                    else:
                        # include 文件找不到，保留原行并添加警告注释
                        if self.debug:
                            print(f"[Preprocess] Warning: Could not find include file: {include_file}")
                            print(f"[Preprocess]   Looked in: {current_dir}")
                            print(f"[Preprocess]   Include paths: {self.include_paths}")
                        output_lines.append(f"// Warning: Could not find include file: {include_file}")
                        output_lines.append(line_content)
                        line_mapping[len(output_lines)] = (abs_path, i + 1)
                else:
                    # 普通行，直接添加
                    output_lines.append(line_content)
                    line_mapping[len(output_lines)] = (abs_path, i + 1)

                i += 1

        # 开始处理主文件
        process_file(self.file_path)

        preprocessed = '\n'.join(output_lines)
        self._dbg(f"Preprocessed {len(output_lines)} lines from {len(processed_files)} files")

        return preprocessed, line_mapping

    def parse(self) -> bool:
        """解析 Verilog 文件"""
        try:
            self._dbg(f"Parsing {self.file_path}")
            self._dbg(f"Include paths: {self.include_paths}")

            # 尝试使用 iverilog 预处理并保留行号映射
            preprocessed, line_mapping = self._preprocess_with_line_mapping()
            if preprocessed and line_mapping:
                # 使用预处理后的内容解析
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.v', delete=False
                ) as tmp_file:
                    tmp_file.write(preprocessed)
                    tmp_path = tmp_file.name

                try:
                    # 已经预处理好，不需要再次预处理
                    self.ast, _ = parse([tmp_path], preprocess_include=[])
                    os.unlink(tmp_path)
                    # 存储行号映射用于后续转换
                    self._line_mapping = line_mapping
                    self._dbg(f"Using line mapping for {len(line_mapping)} lines")
                except Exception as e:
                    os.unlink(tmp_path)
                    raise e
            else:
                # 回退到 pyverilog 的预处理
                self.ast, _ = parse([self.file_path], preprocess_include=self.include_paths or [])
                self._line_mapping = None
            self._dbg("Building symbol table")
            self.stb = SymbolTableBuilder()
            
            self.stb.build(self.ast)
            self._dbg("Building DFG")
            self.dfg_builder = DFGBuilder(self.stb)
            self.dfg_builder.build()

            return True
        except Exception as e:
            # 解析错误也作为问题报告
            self.issues.append(VerilintIssue(
                file_path=self.file_path,
                line=1,
                column=1,
                category=Category.SYNTAX,
                severity=Severity.ERROR,
                code="SYNTAX001",
                message=f"Parse error: {str(e)}"
            ))
            return False

    def _map_line(self, lineno: int) -> Tuple[str, int]:
        """
        将预处理后的行号映射回原始文件行号

        Args:
            lineno: 预处理后的行号（从AST节点获取）

        Returns:
            (原始文件路径, 原始行号)
        """
        if self._line_mapping and lineno in self._line_mapping:
            return self._line_mapping[lineno]
        # 没有映射或行号不在映射中，返回当前文件和原行号
        return self.file_path, lineno

    def check_all(self) -> List[VerilintIssue]:
        """执行所有检查"""
        self.issues = []

        if not self.parse():
            return self.issues

        self._dbg("Running all checks...")

        # 首先运行 InstanceChecker 收集端口连接信息
        instance_checker = InstanceChecker(self.ast, self.stb, debug=self.debug, project=self.project)
        instance_checker.check()  # 这会收集模块端口信息
        # 使用保守策略：没有子模块定义时，instance端口连接的信号既被使用也被驱动
        port_connections = instance_checker.get_port_connections(conservative=True)
        instance_driven = port_connections['driven_signals']
        instance_used = port_connections['used_signals']

        self._dbg(f"Instance driven signals: {instance_driven}")
        self._dbg(f"Instance used signals: {instance_used}")

        # 运行各个检查器，传递 instance 端口信息
        self._check_register(instance_driven, instance_used)
        self._check_instance(instance_checker)  # 复用已创建的 checker
        self._check_instance_hierarchy()  # 检查模块层次结构
        self._check_glitch()
        self._check_combdly()
        self._check_fsm()
        self._check_branch()
        self._check_reset()
        self._check_array_bound()
        self._check_width()
        self._check_cycle()
        # 按行号排序
        self.issues.sort(key=lambda x: (x.line, x.column))

        # 过滤：如果FSM003和BRH003在同一行，只保留FSM003
        self.issues = self._filter_duplicate_issues(self.issues)

        # 过滤：忽略指定的错误代码
        if self.ignored_codes:
            self.issues = [i for i in self.issues if i.code not in self.ignored_codes]

        return self.issues

    def _filter_duplicate_issues(self, issues: List[VerilintIssue]) -> List[VerilintIssue]:
        """
        过滤重复的issues
        如果FSM003和BRH003在同一行，只保留FSM003
        """
        # 按行号分组
        issues_by_line: Dict[int, List[VerilintIssue]] = {}
        for issue in issues:
            line = issue.line
            if line not in issues_by_line:
                issues_by_line[line] = []
            issues_by_line[line].append(issue)

        filtered_issues = []
        for line, line_issues in issues_by_line.items():
            # 检查该行是否有FSM003
            has_fsm003 = any(i.code == "FSM003" for i in line_issues)
            # 检查该行是否有BRH003
            has_brh003 = any(i.code == "BRH003" for i in line_issues)

            if has_fsm003 and has_brh003:
                # 如果两者都有，过滤掉BRH003
                for issue in line_issues:
                    if issue.code != "BRH003":
                        filtered_issues.append(issue)
            else:
                # 否则保留所有
                filtered_issues.extend(line_issues)

        return filtered_issues
    def _check_width(self):
        """位宽检查"""
        self._dbg("Running width check...")
        checker = WidthChecker(self.dfg_builder)
        raw_issues = checker.check()
        for issue in raw_issues:
            self.issues.append(self._convert_width_issue(issue))

    def _convert_width_issue(self, issue: WidthIssue) -> VerilintIssue:
        """转换位宽问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.WIDTH),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "WID000"),
            message=issue.description,
            raw_issue=issue
        )


    def _check_cycle(self):
        """循环依赖检查"""
        self._dbg("Checking cycle dependencies...")
        checker = CycleChecker(self.ast, self.stb, debug=self.debug)
        raw_issues = checker.check()
        for issue in raw_issues:
            self.issues.append(self._convert_cycle_issue(issue))

    def _convert_cycle_issue(self, issue: CycleIssue) -> VerilintIssue:
        """转换循环依赖问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.cycle_type, Category.CYCLE),
            severity=self.SEVERITY_MAP.get(issue.cycle_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.cycle_type, "CYC000"),
            message=issue.description,
            raw_issue=issue
        )
    def _check_register(self, instance_driven: List[str] = None, instance_used: List[str] = None):
        """寄存器检查"""
        self._dbg("Checking registers...")
        checker = RegisterChecker(self.ast, self.dfg_builder, debug=self.debug)
        raw_issues = checker.check(
            instance_driven_signals=instance_driven,
            instance_used_signals=instance_used
        )

        for issue in raw_issues:
            verilint_issue = self._convert_register_issue(issue)
            self.issues.append(verilint_issue)

    def _check_instance(self, existing_checker: InstanceChecker = None):
        """实例化检查"""
        self._dbg("Checking instances...")
        if existing_checker:
            checker = existing_checker
            raw_issues = checker.issues  # 使用已检查的结果
        else:
            checker = InstanceChecker(self.ast, self.stb, debug=self.debug, project=self.project)
            raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_instance_issue(issue)
            self.issues.append(verilint_issue)

    def _check_instance_hierarchy(self):
        """模块层次结构检查"""
        self._dbg("Checking instance hierarchy...")
        try:
            from Checker.instance_checker import check_instance_hierarchy
            raw_issues = check_instance_hierarchy(self.ast, self.stb, debug=self.debug, project=self.project)
            for issue in raw_issues:
                verilint_issue = self._convert_instance_hierarchy_issue(issue)
                self.issues.append(verilint_issue)
        except Exception as e:
            self._dbg(f"Instance hierarchy check failed: {e}")

    def _convert_instance_hierarchy_issue(self, issue) -> VerilintIssue:
        """转换层次结构问题"""
        orig_file, orig_line = self._map_line(issue.lineno)

        # 使用统一的错误代码映射
        error_code = self.ERROR_CODES.get(issue.issue_type, "INST000")

        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=Category.INSTANCE,
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=error_code,
            message=issue.description,
            raw_issue=issue
        )

    def _check_glitch(self):
        """毛刺检查"""
        self._dbg("Checking glitch hazards...")
        checker = GlitchChecker(self.ast, self.stb, debug=self.debug)
        raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_glitch_issue(issue)
            self.issues.append(verilint_issue)

    def _check_combdly(self):
        """组合逻辑敏感列表检查"""
        self._dbg("Checking combdly...")
        checker = CombdlyChecker(self.ast, self.stb, debug=self.debug)
        raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_combdly_issue(issue)
            self.issues.append(verilint_issue)

    def _convert_register_issue(self, issue: RegisterIssue) -> VerilintIssue:
        """转换寄存器问题"""
        orig_file, orig_line = self._map_line(issue.lineno)

        # 根据issue类型重新生成描述，使用映射后的行号
        if issue.issue_type == RegisterIssueType.USE_BEFORE_DRIVE and issue.use_lineno and issue.drive_lineno:
            # 映射use和drive行号
            _, use_line = self._map_line(issue.use_lineno)
            _, drive_line = self._map_line(issue.drive_lineno)
            message = f"Register '{issue.register_name}' is used at line {use_line} before being driven at line {drive_line}"
        elif issue.issue_type == RegisterIssueType.DRIVE_WITHOUT_USE and issue.drive_lineno and issue.use_lineno:
            # 映射drive和use行号
            _, drive_line = self._map_line(issue.drive_lineno)
            _, use_line = self._map_line(issue.use_lineno)
            message = f"Register '{issue.register_name}' is driven at line {drive_line} but not used afterwards (last use at line {use_line})"
        else:
            message = issue.description

        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,  # 默认列号
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.REGISTER),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "REG000"),
            message=message,
            raw_issue=issue
        )

    def _convert_instance_issue(self, issue: InstanceIssue) -> VerilintIssue:
        """转换实例化问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        # 构建相关位置信息
        related_info = []
        if hasattr(issue, 'instance_name') and issue.instance_name:
            related_info.append({
                "location": f"{orig_file}:{orig_line}",
                "message": f"Instance: {issue.instance_name}"
            })

        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.INSTANCE),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "INST000"),
            message=issue.description,
            related_info=related_info,
            raw_issue=issue
        )

    def _convert_glitch_issue(self, issue: GlitchIssue) -> VerilintIssue:
        """转换毛刺问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.GLITCH),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "GLT000"),
            message=issue.description,
            raw_issue=issue
        )

    def _convert_combdly_issue(self, issue: CombdlyIssue) -> VerilintIssue:
        """转换敏感列表问题"""
        orig_file, orig_line = self._map_line(issue.always_lineno)
        # 添加缺失信号的相关信息
        related_info = []
        if issue.missing_signals:
            related_info.append({
                "message": f"Missing signals: {', '.join(issue.missing_signals)}"
            })

        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.COMBDLY),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "CMB000"),
            message=issue.description,
            related_info=related_info,
            raw_issue=issue
        )

    def _check_fsm(self):
        """FSM 检查"""
        self._dbg("Checking FSM...")
        checker = FSMChecker(self.ast, self.stb)
        raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_fsm_issue(issue)
            self.issues.append(verilint_issue)

    def _check_branch(self):
        """分支覆盖检查"""
        self._dbg("Checking branch coverage...")
        checker = BranchCoverageChecker(self.ast, self.stb)
        raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_branch_issue(issue)
            self.issues.append(verilint_issue)

    def _check_reset(self):
        """复位检查"""
        self._dbg("Checking reset...")
        # ResetChecker 需要 CycleTableBuilder
        from cycle_table_builder import CycleTableBuilder
        cycle_builder = CycleTableBuilder(self.dfg_builder)
        cycle_builder.build()

        checker = ResetChecker(cycle_builder, debug=self.debug)
        raw_issues = checker.check()

        for issue in raw_issues:
            verilint_issue = self._convert_reset_issue(issue)
            self.issues.append(verilint_issue)

    def _convert_fsm_issue(self, issue: FSMIssue) -> VerilintIssue:
        """转换 FSM 问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.FSM),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "FSM000"),
            message=issue.description,
            raw_issue=issue
        )

    def _convert_branch_issue(self, issue: BranchIssue) -> VerilintIssue:
        """转换分支覆盖问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.BRANCH),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "BRH000"),
            message=issue.description,
            raw_issue=issue
        )

    def _convert_reset_issue(self, issue: ResetIssue) -> VerilintIssue:
        """转换复位问题"""
        # Use always block line number for grouped reports
        orig_file, orig_line = self._map_line(issue.lineno)

        # Build message showing always block and missing variables
        if issue.missing_vars:
            var_list = ', '.join(issue.missing_vars)
            message = f"Missing reset assignment for: {var_list}"
        else:
            message = issue.description

        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.RESET),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.WARNING),
            code=self.ERROR_CODES.get(issue.issue_type, "RST000"),
            message=message,
            raw_issue=issue
        )

    def _check_array_bound(self):
        """检查数组越界问题"""
        self._dbg("Running array bound check...")
        checker = ArrayBoundChecker(self.ast, self.stb, debug=self.debug)
        raw_issues = checker.check()
        for issue in raw_issues:
            self.issues.append(self._convert_array_bound_issue(issue))

    def _convert_array_bound_issue(self, issue: ArrayBoundIssue) -> VerilintIssue:
        """转换数组越界问题"""
        orig_file, orig_line = self._map_line(issue.lineno)
        return VerilintIssue(
            file_path=orig_file,
            line=orig_line,
            column=1,
            category=self.CATEGORY_MAP.get(issue.issue_type, Category.ARRAY),
            severity=self.SEVERITY_MAP.get(issue.issue_type, Severity.ERROR),
            code=self.ERROR_CODES.get(issue.issue_type, "ARR000"),
            message=issue.description,
            raw_issue=issue
        )

    def to_json(self) -> str:
        """输出为 JSON 格式"""
        data = {
            "file": self.file_path,
            "totalIssues": len(self.issues),
            "errors": len([i for i in self.issues if i.severity == Severity.ERROR]),
            "warnings": len([i for i in self.issues if i.severity == Severity.WARNING]),
            "infos": len([i for i in self.issues if i.severity == Severity.INFO]),
            "issues": [issue.to_vscode_format() for issue in self.issues]
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def to_lsp_json(self) -> str:
        """输出为 LSP Diagnostic[] 格式"""
        diagnostics = [issue.to_lsp_diagnostic() for issue in self.issues]
        return json.dumps(diagnostics, indent=2, ensure_ascii=False)

    def print_report(self):
        """打印检查报告"""
        print("\n" + "=" * 70)
        print("Verilint Check Report")
        print("=" * 70)
        print(f"File: {self.file_path}")
        print(f"Total issues: {len(self.issues)}")

        errors = [i for i in self.issues if i.severity == Severity.ERROR]
        warnings = [i for i in self.issues if i.severity == Severity.WARNING]
        infos = [i for i in self.issues if i.severity == Severity.INFO]

        if errors:
            print(f"\n[ERRORS] ({len(errors)}):")
            for issue in errors:
                print(f"  Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

        if warnings:
            print(f"\n[WARNINGS] ({len(warnings)}):")
            for issue in warnings:
                print(f"  Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

        if infos:
            print(f"\n[INFO] ({len(infos)}):")
            for issue in infos:
                print(f"  Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

        if not self.issues:
            print("\nNo issues found!")

        print("=" * 70)


def check_file(file_path: str, debug: bool = False, output_format: str = "text",
               include_paths: List[str] = None, project: Any = None, ignored_codes: List[str] = None) -> List[VerilintIssue]:
    """
    检查单个文件的便捷函数

    Args:
        file_path: Verilog 文件路径
        debug: 是否启用调试输出
        output_format: 输出格式 ("text", "json", "lsp")
        include_paths: include 文件搜索路径列表
        project: 关联的 Project 对象
        ignored_codes: 要忽略的错误代码列表

    Returns:
        问题列表
    """
    checker = VerilintChecker(file_path, debug=debug, include_paths=include_paths, project=project, ignored_codes=ignored_codes)
    issues = checker.check_all()

    if output_format == "json":
        print(checker.to_json())
    elif output_format == "lsp":
        print(checker.to_lsp_json())
    else:
        checker.print_report()

    return issues


def check_files(file_paths: List[str], debug: bool = False,
                include_paths: List[str] = None) -> Dict[str, List[VerilintIssue]]:
    """
    检查多个文件

    Args:
        file_paths: Verilog 文件路径列表
        debug: 是否启用调试输出
        include_paths: include 文件搜索路径列表

    Returns:
        文件路径到问题列表的映射
    """
    results = {}
    for file_path in file_paths:
        checker = VerilintChecker(file_path, debug=debug, include_paths=include_paths)
        results[file_path] = checker.check_all()
    return results


def _get_project_files_hash(project_root: str, recursive: bool = True) -> str:
    """获取项目文件列表的哈希，用于检测文件变化"""
    import glob

    pattern = os.path.join(project_root, "**", "*.v").replace("\\", "/")
    file_paths = glob.glob(pattern, recursive=recursive)

    # 也检查 .sv 文件
    sv_pattern = os.path.join(project_root, "**", "*.sv").replace("\\", "/")
    file_paths.extend(glob.glob(sv_pattern, recursive=recursive))

    # 按修改时间排序并计算哈希
    file_info = []
    for fp in sorted(file_paths):
        try:
            mtime = os.path.getmtime(fp)
            file_info.append(f"{fp}:{mtime}")
        except:
            pass

    return hashlib.md5("|".join(file_info).encode()).hexdigest()


def _get_cache_file_path(project_root: str) -> str:
    """获取缓存文件路径"""
    import tempfile
    cache_dir = os.path.join(tempfile.gettempdir(), "verilint_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # 使用项目路径的哈希作为缓存文件名
    root_hash = hashlib.md5(project_root.encode()).hexdigest()
    return os.path.join(cache_dir, f"project_{root_hash}.json")


def get_cached_project(project_root: str, include_paths: List[str] = None,
                       recursive: bool = True, debug: bool = False,
                       use_cache: bool = True) -> Any:
    """
    获取缓存的项目对象，如果缓存有效则直接返回

    Args:
        project_root: 项目根目录
        include_paths: include 路径
        recursive: 是否递归扫描
        debug: 是否输出调试信息
        use_cache: 是否使用缓存（优先级低于 CacheConfig.ENABLED）

    Returns:
        Project 对象
    """
    global _project_cache

    # 检查全局缓存配置
    if not CacheConfig.ENABLED or not use_cache:
        # 不使用缓存，重新解析
        if debug or CacheConfig.DEBUG:
            print(f"[Cache] 缓存已禁用，重新解析项目...", file=sys.stderr)
        CacheConfig._record_miss()
        from Project import Project
        project = Project(os.path.basename(project_root))
        project.add_directory(project_root, recursive=recursive)
        project.set_include_paths(include_paths or [])
        project.parse_all(debug=debug)
        return project

    # 检查内存缓存
    cache_key = project_root
    current_hash = _get_project_files_hash(project_root, recursive)

    if CacheConfig.MEMORY_CACHE_ENABLED and cache_key in _project_cache:
        cached = _project_cache[cache_key]
        if cached.get('file_hash') == current_hash:
            if debug or CacheConfig.DEBUG:
                print(f"[Cache] ✓ 内存缓存命中 (hash match)", file=sys.stderr)
                print(f"[Cache]   模块数: {len(cached['project'].modules)}", file=sys.stderr)
            CacheConfig._record_memory_hit()
            return cached['project']

    # 检查文件缓存
    if CacheConfig.FILE_CACHE_ENABLED:
        cache_file = _get_cache_file_path(project_root)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    file_cache = json.load(f)
                if file_cache.get('file_hash') == current_hash:
                    # 从文件缓存重建项目
                    if debug or CacheConfig.DEBUG:
                        print(f"[Cache] ✓ 文件缓存命中 (hash match)", file=sys.stderr)
                    from Project import Project
                    project = Project(file_cache['project_name'])
                    project.set_include_paths(file_cache.get('include_paths', []))
                    # 重新添加文件并解析
                    project.add_directory(project_root, recursive=recursive)
                    parse_result = project.parse_all(debug=debug)
                    # 更新内存缓存
                    _project_cache[cache_key] = {
                        'file_hash': current_hash,
                        'project': project,
                        'timestamp': time.time(),
                        'modules_count': parse_result['total_modules']
                    }
                    CacheConfig._record_file_hit()
                    return project
            except Exception as e:
                if debug or CacheConfig.DEBUG:
                    print(f"[Cache] 文件缓存加载失败: {e}", file=sys.stderr)

    if debug or CacheConfig.DEBUG:
        print(f"[Cache] ✗ 缓存未命中，解析项目中...", file=sys.stderr)

    # 解析项目
    from Project import Project
    project = Project(os.path.basename(project_root))
    project.add_directory(project_root, recursive=recursive)
    project.set_include_paths(include_paths or [])
    parse_result = project.parse_all(debug=debug)

    # 更新内存缓存
    if CacheConfig.MEMORY_CACHE_ENABLED:
        _project_cache[cache_key] = {
            'file_hash': current_hash,
            'project': project,
            'timestamp': time.time(),
            'modules_count': parse_result['total_modules']
        }

    # 尝试保存文件缓存
    if CacheConfig.FILE_CACHE_ENABLED:
        try:
            cache_file = _get_cache_file_path(project_root)
            file_cache = {
                'project_root': project_root,
                'project_name': os.path.basename(project_root),
                'file_hash': current_hash,
                'include_paths': include_paths or [],
                'timestamp': time.time(),
                'modules_count': parse_result['total_modules']
            }
            with open(cache_file, 'w') as f:
                json.dump(file_cache, f)
        except Exception as e:
            if debug or CacheConfig.DEBUG:
                print(f"[Cache] 文件缓存保存失败: {e}", file=sys.stderr)

    CacheConfig._record_miss()

    if debug or CacheConfig.DEBUG:
        print(f"[Cache] 项目已缓存，模块数: {parse_result['total_modules']}", file=sys.stderr)

    return project


def clear_project_cache(project_root: str = None):
    """清除项目缓存"""
    global _project_cache
    CacheConfig._record_clear()
    if project_root:
        _project_cache.pop(project_root, None)
        if CacheConfig.DEBUG:
            print(f"[Cache] 已清除项目缓存: {project_root}", file=sys.stderr)
    else:
        _project_cache.clear()
        if CacheConfig.DEBUG:
            print(f"[Cache] 已清除所有缓存", file=sys.stderr)


def check_project(project, debug: bool = False, output_format: str = "text", ignored_codes: List[str] = None) -> Dict[str, List[VerilintIssue]]:
    """
    检查整个项目中的所有文件

    Args:
        project: Project 对象
        debug: 是否启用调试输出
        output_format: 输出格式 ("text", "json")
        ignored_codes: 要忽略的错误代码列表

    Returns:
        文件路径到问题列表的映射
    """
    import time
    from Project import File

    results = {}
    total_issues = []
    total_lines = 0
    start_time = time.time()

    print(f"\nChecking project: {project.project_name}")
    print(f"Total files: {len(project.files)}")

    # 首先解析所有文件，提取模块信息
    print(f"Parsing all files to extract module definitions...")
    parse_result = project.parse_all(debug=debug)
    print(f"Parsed {parse_result['parsed_files']} files, found {parse_result['total_modules']} modules")
    if debug and parse_result['modules']:
        print(f"Modules found: {', '.join(parse_result['modules'][:10])}{'...' if len(parse_result['modules']) > 10 else ''}")
    print()

    for file_obj in project.files:
        if isinstance(file_obj, File):
            file_path = file_obj.file_path
            # Use project's include paths
            include_paths = project.include_paths if hasattr(project, 'include_paths') else []

            # Count lines in file
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = len(f.readlines())
                    total_lines += lines
            except Exception:
                lines = 0

            print(f"Checking: {file_path} ({lines} lines)")
            checker = VerilintChecker(file_path, debug=debug, include_paths=include_paths, project=project, ignored_codes=ignored_codes)
            issues = checker.check_all()
            results[file_path] = issues
            total_issues.extend(issues)

            if output_format == "text":
                if issues:
                    print(f"  Found {len(issues)} issues")
                else:
                    print(f"  No issues")

    # Print file-by-file report
    if output_format == "text":
        print(f"\n{'='*70}")
        print(f"Detailed Report")
        print(f"{'='*70}")

        # 按文件分组显示错误
        for file_path, issues in results.items():
            if not issues:
                continue

            # 只显示文件名（缩短路径）
            file_name = os.path.basename(file_path)
            print(f"\n[FILE] {file_name}")
            print(f"  Path: {file_path}")

            # 按严重级别分组
            file_errors = [i for i in issues if i.severity == Severity.ERROR]
            file_warnings = [i for i in issues if i.severity == Severity.WARNING]
            file_infos = [i for i in issues if i.severity == Severity.INFO]

            # 显示错误
            for issue in file_errors:
                print(f"  [ERROR]   Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

            # 显示警告
            for issue in file_warnings:
                print(f"  [WARNING] Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

            # 显示信息
            for issue in file_infos:
                print(f"  [INFO]    Line {issue.line:4d}:{issue.column:3d} [{issue.code}] {issue.message}")

        # Print summary
        print(f"\n{'='*70}")
        print(f"Project Summary: {project.project_name}")
        print(f"{'='*70}")
        print(f"Total files checked: {len(project.files)}")
        print(f"Total issues found: {len(total_issues)}")

        errors = len([i for i in total_issues if i.severity == Severity.ERROR])
        warnings = len([i for i in total_issues if i.severity == Severity.WARNING])
        infos = len([i for i in total_issues if i.severity == Severity.INFO])

        if errors:
            print(f"  Errors: {errors}")
        if warnings:
            print(f"  Warnings: {warnings}")
        if infos:
            print(f"  Infos: {infos}")

        # Print timing and line count stats
        elapsed_time = time.time() - start_time
        print(f"  Lines: {total_lines}")
        print(f"  Time: {elapsed_time:.2f}s")
        if elapsed_time > 0:
            print(f"  Speed: {total_lines/elapsed_time:.0f} lines/s")
        print(f"{'='*70}")

    # JSON 输出格式 - 每行一个文件的 JSON 对象
    elif output_format == "json":
        for file_path, issues in results.items():
            # 统计各 severity 的数量
            errors = len([i for i in issues if i.severity == Severity.ERROR])
            warnings = len([i for i in issues if i.severity == Severity.WARNING])
            infos = len([i for i in issues if i.severity == Severity.INFO])

            result_obj = {
                "file": file_path,
                "totalIssues": len(issues),
                "errors": errors,
                "warnings": warnings,
                "infos": infos,
                "issues": [issue.to_vscode_format() for issue in issues]
            }
            print(json.dumps(result_obj))

    # LSP 输出格式
    elif output_format == "lsp":
        lsp_diagnostics = []
        for file_path, issues in results.items():
            for issue in issues:
                lsp_diagnostics.append(issue.to_lsp_diagnostic())
        print(json.dumps(lsp_diagnostics))

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python verilint_checker.py <file> [options]")
        print("")
        print("Arguments:")
        print("  <file>                    Verilog file path to check")
        print("")
        print("Options:")
        print("  --project <dir>           Project root directory for module resolution")
        print("  --project-only            Treat <file_or_dir> as project directory (legacy)")
        print("  --recursive               Recursively scan directories (default: True)")
        print("  --no-recursive            Disable recursive scanning")
        print("  --json                    Output in JSON format (for VSCode integration)")
        print("  --lsp                     Output in LSP Diagnostic format")
        print("  --debug                   Enable debug output")
        print("  --root <path>             Set project root directory for path resolution")
        print("  -I <path>                 Add include path (can be used multiple times)")
        print("  --include <path>          Add include path (can be used multiple times)")
        print("")
        print("Cache Options:")
        print("  --no-cache                Disable project parsing cache")
        print("  --cache-debug             Enable cache debug output")
        print("  --cache-stats             Print cache statistics after checking")
        print("  --clear-cache             Clear project cache before checking")
        print("")
        print("Filter Options:")
        print("  --ignore <code>           Ignore specific error code (can be used multiple times)")
        print("                            Example: --ignore WID008 --ignore REG002")
        print("")
        print("Examples:")
        print("  python verilint_checker.py file.v")
        print("  python verilint_checker.py file.v --project ./my_project")
        print("  python verilint_checker.py project_dir --project-only -I include/path")
        print("  python verilint_checker.py file.v --project ./my_project --no-cache")
        print("  python verilint_checker.py file.v --project ./my_project --cache-stats")
        print("  python verilint_checker.py file.v --ignore WID008 --ignore REG002")
        sys.exit(1)

    target_file = sys.argv[1]
    output_format = "text"
    include_paths = []
    is_project_only = False  # 旧的项目模式（扫描整个目录）
    recursive = True
    project_root = None  # 项目根目录（用于模块解析）
    use_cache = True
    cache_debug = False
    cache_stats = False
    clear_cache = False
    ignored_codes = ['SYNTAX001', 'REG002','WID008','INST001','INST005','WID003','BRH002']  # 要忽略的错误代码列表，默认忽略SYNTAX001解析错误和REG002

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--json":
            output_format = "json"
        elif arg == "--lsp":
            output_format = "lsp"
        elif arg == "--debug":
            pass  # 后面统一处理
        elif arg == "--project-only":
            is_project_only = True
        elif arg == "--project":
            # --project 现在接受一个目录参数作为项目根
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                project_root = sys.argv[i + 1]
                i += 1
            else:
                print("Error: --project requires a directory path")
                sys.exit(1)
        elif arg == "--recursive":
            recursive = True
        elif arg == "--no-recursive":
            recursive = False
        elif arg == "--root":
            # 项目根目录（用于解析相对路径）
            if i + 1 < len(sys.argv):
                project_root = sys.argv[i + 1]
                i += 1
            else:
                print("Error: --root requires a path argument")
                sys.exit(1)
        elif arg == "-I" or arg == "--include":
            if i + 1 < len(sys.argv):
                include_paths.append(sys.argv[i + 1])
                i += 1
            else:
                print(f"Error: {arg} requires a path argument")
                sys.exit(1)
        elif arg.startswith("-I"):
            # 处理 -Ipath 格式
            include_paths.append(arg[2:])
        # 缓存控制选项
        elif arg == "--no-cache":
            use_cache = False
        elif arg == "--cache-debug":
            cache_debug = True
        elif arg == "--cache-stats":
            cache_stats = True
        elif arg == "--clear-cache":
            clear_cache = True
        elif arg == "--ignore":
            # 忽略指定的错误代码
            if i + 1 < len(sys.argv):
                ignored_codes.append(sys.argv[i + 1])
                i += 1
            else:
                print("Error: --ignore requires an error code argument")
                sys.exit(1)
        i += 1

    debug = "--debug" in sys.argv

    # 应用缓存配置
    if not use_cache:
        CacheConfig.disable()
    if cache_debug:
        CacheConfig.set_debug(True)

    # 清空缓存（如果需要）
    if clear_cache:
        clear_project_cache()
        print("[Cache] 项目缓存已清空")

    starttime = time.time()
    # 判断是项目模式还是单文件模式
    if is_project_only or os.path.isdir(target_file):
        # 项目模式：扫描整个目录
        scan_path = project_root if project_root else target_file

        # 使用缓存获取项目
        project = get_cached_project(
            scan_path,
            include_paths=include_paths,
            recursive=recursive,
            debug=debug,
            use_cache=True
        )
        check_project(project, debug=debug, output_format=output_format, ignored_codes=ignored_codes)
    else:
        # 单文件模式：检查指定文件，但可以用 --project 传入项目根进行模块解析
        project = None
        if project_root:
            if os.path.isdir(project_root):
                # 使用缓存获取项目（避免重复解析）
                project = get_cached_project(
                    project_root,
                    include_paths=include_paths,
                    recursive=recursive,
                    debug=debug,
                    use_cache=True
                )
            else:
                print(f"Warning: Project root '{project_root}' is not a directory, ignoring")

        check_file(target_file, debug=debug, output_format=output_format,
                   include_paths=include_paths, project=project, ignored_codes=ignored_codes)
        endtime = time.time()
        print(f"[Time] {endtime-starttime:.2f}s")

    # 输出缓存统计
    if cache_stats:
        CacheConfig.print_stats()
