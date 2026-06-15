"""
Instance Builder - 模块实例化关系构建器

管理模块之间的调用关系（实例化关系）
提供模块层次结构分析和查询功能
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from pyverilog.vparser.ast import (
    ModuleDef, Instance, InstanceList, PortArg, Identifier
)
from symbol_table_builder import SymbolTableBuilder


@dataclass
class ModuleInstance:
    """模块实例信息"""
    name: str                      # 实例名
    module_type: str               # 实例化的模块类型（模块名）
    parent_module: str             # 父模块名（包含该实例的模块）
    lineno: int                    # 行号
    port_connections: Dict[str, Any] = field(default_factory=dict)  # 端口连接


@dataclass
class ModuleHierarchy:
    """模块层次结构"""
    name: str                      # 模块名
    instances: List[ModuleInstance] = field(default_factory=list)  # 子实例列表
    parents: List[str] = field(default_factory=list)  # 父模块列表（被哪些模块实例化）


class InstanceBuilder:
    """
    模块实例化关系构建器

    功能：
    1. 分析所有模块之间的实例化关系
    2. 构建模块层次结构树
    3. 提供模块依赖查询
    4. 支持递归查找子模块和父模块
    """

    def __init__(self, ast, stb: SymbolTableBuilder, debug: bool = False):
        self.ast = ast
        self.stb = stb
        self.debug = debug

        # 模块名 -> ModuleHierarchy
        self.hierarchy: Dict[str, ModuleHierarchy] = {}

        # 实例名 -> ModuleInstance (全局唯一实例名: "parent.instance_name")
        self.instances: Dict[str, ModuleInstance] = {}

        # 顶层模块列表（没有被其他模块实例化的模块）
        self.top_modules: List[str] = []

        # 模块实例化图的边 (caller -> callee)
        self.call_graph: Dict[str, Set[str]] = defaultdict(set)

        self._build_hierarchy()

    def _dbg(self, msg: str):
        if self.debug:
            print(f"[InstanceBuilder] {msg}")

    def _build_hierarchy(self):
        """构建模块层次结构"""
        # 第一步：收集所有模块定义
        modules = self._get_modules()
        for module in modules:
            self.hierarchy[module.name] = ModuleHierarchy(name=module.name)

        # 第二步：分析每个模块内部的实例化
        for module in modules:
            self._analyze_module_instances(module)

        # 第三步：确定顶层模块
        self._find_top_modules()

        # 第四步：构建调用图
        self._build_call_graph()

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

    def _analyze_module_instances(self, module: ModuleDef):
        """分析模块内部的实例化"""
        parent_name = module.name

        for item in module.items:
            if isinstance(item, InstanceList):
                for instance in item.instances:
                    self._process_instance(instance, parent_name)
            elif isinstance(item, Instance):
                self._process_instance(item, parent_name)

    def _process_instance(self, instance: Instance, parent_module: str):
        """处理单个实例"""
        instance_name = instance.name
        module_type = instance.module
        lineno = instance.lineno

        # 创建实例信息
        inst = ModuleInstance(
            name=instance_name,
            module_type=module_type,
            parent_module=parent_module,
            lineno=lineno,
            port_connections=self._extract_port_connections(instance)
        )

        # 添加到全局实例表
        global_name = f"{parent_module}.{instance_name}"
        self.instances[global_name] = inst

        # 添加到父模块的层次结构
        if parent_module in self.hierarchy:
            self.hierarchy[parent_module].instances.append(inst)

        # 记录子模块的父模块关系
        if module_type in self.hierarchy:
            if parent_module not in self.hierarchy[module_type].parents:
                self.hierarchy[module_type].parents.append(parent_module)

        self._dbg(f"Found instance: {global_name} -> {module_type}")

    def _extract_port_connections(self, instance: Instance) -> Dict[str, Any]:
        """提取实例的端口连接信息"""
        connections = {}

        if hasattr(instance, 'portlist') and instance.portlist:
            for port in instance.portlist:
                if isinstance(port, PortArg):
                    port_name = port.portname
                    arg = port.argname
                    connections[port_name] = arg

        return connections

    def _find_top_modules(self):
        """找出顶层模块（没有被其他模块实例化的模块）"""
        instantiated = set()

        for inst in self.instances.values():
            instantiated.add(inst.module_type)

        self.top_modules = [
            name for name in self.hierarchy.keys()
            if name not in instantiated
        ]

        self._dbg(f"Top modules: {self.top_modules}")

    def _build_call_graph(self):
        """构建模块调用图"""
        for inst in self.instances.values():
            self.call_graph[inst.parent_module].add(inst.module_type)

    # ==================== 查询接口 ====================

    def get_module_hierarchy(self, module_name: str) -> Optional[ModuleHierarchy]:
        """获取模块的层次结构信息"""
        return self.hierarchy.get(module_name)

    def get_instance(self, global_name: str) -> Optional[ModuleInstance]:
        """通过全局名称获取实例"""
        return self.instances.get(global_name)

    def get_instances_of_module(self, module_type: str) -> List[ModuleInstance]:
        """获取所有指定类型的实例"""
        return [
            inst for inst in self.instances.values()
            if inst.module_type == module_type
        ]

    def get_child_instances(self, module_name: str) -> List[ModuleInstance]:
        """获取模块的直接子实例"""
        hierarchy = self.hierarchy.get(module_name)
        if hierarchy:
            return hierarchy.instances
        return []

    def get_parent_modules(self, module_name: str) -> List[str]:
        """获取模块的所有父模块（被哪些模块实例化）"""
        hierarchy = self.hierarchy.get(module_name)
        if hierarchy:
            return hierarchy.parents
        return []

    def get_submodules(self, module_name: str, recursive: bool = False) -> Set[str]:
        """
        获取模块的子模块

        Args:
            module_name: 模块名
            recursive: 是否递归获取所有子模块

        Returns:
            子模块类型名称集合
        """
        submodules = set()

        for inst in self.get_child_instances(module_name):
            submodules.add(inst.module_type)
            if recursive:
                submodules.update(self.get_submodules(inst.module_type, recursive=True))

        return submodules

    def get_instance_path(self, global_name: str) -> List[str]:
        """
        获取实例的完整层次路径

        例如: "top.sub1.sub2" -> ["top", "top.sub1", "top.sub1.sub2"]
        """
        parts = global_name.split('.')
        path = []
        current = ""
        for part in parts:
            if current:
                current += "." + part
            else:
                current = part
            path.append(current)
        return path

    def is_top_module(self, module_name: str) -> bool:
        """检查是否是顶层模块"""
        return module_name in self.top_modules

    def get_all_modules(self) -> List[str]:
        """获取所有模块名称"""
        return list(self.hierarchy.keys())

    def get_callers(self, module_name: str) -> Set[str]:
        """获取调用该模块的所有模块"""
        callers = set()
        for parent, children in self.call_graph.items():
            if module_name in children:
                callers.add(parent)
        return callers

    def get_callees(self, module_name: str) -> Set[str]:
        """获取该模块调用的所有模块"""
        return self.call_graph.get(module_name, set())

    def has_circular_dependency(self) -> Optional[List[str]]:
        """
        检测是否存在循环依赖

        Returns:
            如果存在循环依赖，返回循环路径；否则返回 None
        """
        visited = set()
        rec_stack = set()
        path = []

        def dfs(module):
            visited.add(module)
            rec_stack.add(module)
            path.append(module)

            for callee in self.call_graph.get(module, set()):
                if callee not in visited:
                    result = dfs(callee)
                    if result:
                        return result
                elif callee in rec_stack:
                    # 找到循环
                    cycle_start = path.index(callee)
                    return path[cycle_start:] + [callee]

            path.pop()
            rec_stack.remove(module)
            return None

        for module in self.hierarchy:
            if module not in visited:
                result = dfs(module)
                if result:
                    return result

        return None

    def print_hierarchy(self, module_name: str = None, indent: int = 0):
        """打印模块层次结构"""
        if module_name is None:
            # 打印所有顶层模块
            for top in self.top_modules:
                self.print_hierarchy(top, indent)
            return

        prefix = "  " * indent
        print(f"{prefix}{module_name}")

        hierarchy = self.hierarchy.get(module_name)
        if hierarchy:
            for inst in hierarchy.instances:
                print(f"{prefix}  └── {inst.name} : {inst.module_type}")
                self.print_hierarchy(inst.module_type, indent + 2)

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_modules": len(self.hierarchy),
            "total_instances": len(self.instances),
            "top_modules": self.top_modules,
            "max_depth": self._calculate_max_depth(),
            "has_circular": self.has_circular_dependency() is not None
        }

    def _calculate_max_depth(self) -> int:
        """计算模块层次结构的最大深度"""
        def depth(module_name, visited=None):
            if visited is None:
                visited = set()

            if module_name in visited:
                return 0  # 避免循环

            visited.add(module_name)

            hierarchy = self.hierarchy.get(module_name)
            if not hierarchy or not hierarchy.instances:
                return 1

            max_child_depth = 0
            for inst in hierarchy.instances:
                child_depth = depth(inst.module_type, visited.copy())
                max_child_depth = max(max_child_depth, child_depth)

            return 1 + max_child_depth

        if not self.top_modules:
            return 0

        return max(depth(m) for m in self.top_modules)


def build_instance_hierarchy(ast, stb: SymbolTableBuilder, debug: bool = False) -> InstanceBuilder:
    """便捷函数：构建模块实例化层次结构"""
    return InstanceBuilder(ast, stb, debug=debug)


if __name__ == "__main__":
    import sys
    from pyverilog.vparser.parser import parse

    if len(sys.argv) < 2:
        print("Usage: python instance_builder.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Build instance hierarchy
    builder = InstanceBuilder(ast, stb, debug=True)

    print("\n" + "=" * 70)
    print("Module Hierarchy")
    print("=" * 70)
    builder.print_hierarchy()

    print("\n" + "=" * 70)
    print("Statistics")
    print("=" * 70)
    stats = builder.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Check for circular dependencies
    circular = builder.has_circular_dependency()
    if circular:
        print(f"\n⚠️  Circular dependency detected: {' -> '.join(circular)}")
