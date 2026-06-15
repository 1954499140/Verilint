from typing import Optional, Dict, List, Any, Set, Tuple
from pyverilog.vparser.ast import *
from symbol import (
    Symbol, SymbolType, DefSite, UseSite, DataflowEdge,
)
from symbol_table_builder import Scope, SymbolTableBuilder


class DFGNode:
    """
    数据流图节点
    表示一个操作或赋值语句
    """

    def __init__(self, node_id: int, stmt: Any, lineno: int, node_type: str = ""):
        self.node_id = node_id          # 节点ID
        self.stmt = stmt                # 关联的AST语句
        self.lineno = lineno            # 行号
        self.node_type = node_type      # 节点类型

        self.defs: List[DefSite] = []   # 此节点中的定义
        self.uses: List[UseSite] = []   # 此节点中的使用
        self.preds: List['DFGNode'] = []  # 前驱节点（数据依赖）
        self.succs: List['DFGNode'] = []  # 后继节点

        self.basic_block_id: Optional[int] = None  # 所属基本块

    def add_def(self, def_site: DefSite):
        """添加定义"""
        self.defs.append(def_site)

    def add_use(self, use_site: UseSite):
        """添加使用"""
        self.uses.append(use_site)

    def add_pred(self, node: 'DFGNode'):
        """添加前驱节点"""
        if node not in self.preds:
            self.preds.append(node)

    def add_succ(self, node: 'DFGNode'):
        """添加后继节点"""
        if node not in self.succs:
            self.succs.append(node)

    def __repr__(self) -> str:
        return f"DFGNode(id={self.node_id}, line={self.lineno}, type={self.node_type})"


class DataflowGraph:
    """
    数据流图
    表示整个模块的数据依赖关系
    """

    def __init__(self, name: str, graph_type: str = "module"):
        self.name = name                    # 图名称（模块名或always块名）
        self.graph_type = graph_type        # 图类型: "module", "combinational", "sequential"
        self.nodes: List[DFGNode] = []      # 所有节点
        self.node_map: Dict[int, DFGNode] = {}  # 行号到节点的映射

        # 按符号组织的定义和使用
        self.symbol_defs: Dict[str, List[DefSite]] = {}   # 符号名 -> 定义点列表
        self.symbol_uses: Dict[str, List[UseSite]] = {}   # 符号名 -> 使用点列表

        # 数据流边
        self.edges: List[Tuple[DefSite, UseSite]] = []

        # Always块特定信息
        self.sensitivity_list: List[str] = []             # 敏感列表信号
        self.is_combinational: bool = False               # 是否是组合逻辑
        self.is_sequential: bool = False                  # 是否是时序逻辑
        self.clock_signal: Optional[str] = None           # 时钟信号名（时序逻辑）
        self.reset_signal: Optional[str] = None           # 复位信号名（时序逻辑）
        self.parent_module: Optional[str] = None          # 所属模块名
        self.always_ast_node: Optional[Always] = None     # 原始always块AST节点

        # 模块级别的子图（用于包含always块的模块）
        self.subgraphs: Dict[str, 'DataflowGraph'] = {}   # always块名 -> 子图
        self.assign_graph: Optional['DataflowGraph'] = None  # assign语句的子图

    def add_node(self, node: DFGNode):
        """添加节点"""
        self.nodes.append(node)
        self.node_map[node.lineno] = node

    def get_node(self, lineno: int) -> Optional[DFGNode]:
        """根据行号获取节点"""
        return self.node_map.get(lineno)

    def add_def(self, symbol_name: str, def_site: DefSite):
        """记录符号定义"""
        if symbol_name not in self.symbol_defs:
            self.symbol_defs[symbol_name] = []
        self.symbol_defs[symbol_name].append(def_site)

    def add_use(self, symbol_name: str, use_site: UseSite):
        """记录符号使用"""
        if symbol_name not in self.symbol_uses:
            self.symbol_uses[symbol_name] = []
        self.symbol_uses[symbol_name].append(use_site)

    def add_edge(self, def_site: DefSite, use_site: UseSite):
        """添加数据流边"""
        self.edges.append((def_site, use_site))
        def_site.symbol.add_dataflow_edge(def_site, use_site)

    def get_defs(self, symbol_name: str) -> List[DefSite]:
        """获取符号的所有定义点"""
        return self.symbol_defs.get(symbol_name, [])

    def get_uses(self, symbol_name: str) -> List[UseSite]:
        """获取符号的所有使用点"""
        return self.symbol_uses.get(symbol_name, [])

    def get_dataflow_predecessors(self, symbol_name: str) -> List[str]:
        """
        获取符号的数据流前驱
        即影响该符号定义的其他符号
        """
        predecessors = set()
        for def_site in self.get_defs(symbol_name):
            for sym in def_site.rhs_symbols:
                predecessors.add(sym.name)
        return list(predecessors)

    def get_dataflow_successors(self, symbol_name: str) -> List[str]:
        """
        获取符号的数据流后继
        即受此符号定义影响的其他符号
        """
        successors = set()
        symbol_uses = self.get_uses(symbol_name)
        for def_site in self.symbol_defs.get(symbol_name, []):
            for edge in def_site.symbol.dataflow_edges:
                if edge.source_def == def_site:
                    successors.add(edge.target_use.symbol.name)
        return list(successors)

    def find_undefined_uses(self) -> List[UseSite]:
        """查找未定义的使用（使用前没有定义）"""
        undefined = []
        for symbol_name, uses in self.symbol_uses.items():
            defs = self.symbol_defs.get(symbol_name, [])
            for use in uses:
                # 检查此使用前是否有定义
                has_def_before = any(d.lineno < use.lineno for d in defs)
                if not has_def_before and not use.symbol.is_port():
                    undefined.append(use)
        return undefined

    def find_multiple_drivers(self) -> List[Tuple[str, List[DefSite]]]:
        """查找有多个驱动源的符号"""
        multiple_drivers = []
        for symbol_name, defs in self.symbol_defs.items():
            # 过滤掉不同 always 块中的定义
            unique_sites = []
            for d in defs:
                if d.def_type == 'assign':
                    unique_sites.append(d)
                elif d.def_type in ('blocking', 'nonblocking'):
                    unique_sites.append(d)
            if len(unique_sites) > 1:
                multiple_drivers.append((symbol_name, unique_sites))
        return multiple_drivers

    def add_subgraph(self, name: str, subgraph: 'DataflowGraph'):
        """添加子图（用于always块）"""
        self.subgraphs[name] = subgraph

    def set_assign_graph(self, graph: 'DataflowGraph'):
        """设置assign语句的子图"""
        self.assign_graph = graph

    def print_graph(self, indent: int = 0):
        """打印数据流图信息"""
        prefix = "  " * indent
        print(f"\n{prefix}=== Dataflow Graph: {self.name} (type={self.graph_type}) ===")

        if self.graph_type == "sequential":
            print(f"{prefix}Clock: {self.clock_signal}, Reset: {self.reset_signal}")
        elif self.graph_type == "combinational":
            print(f"{prefix}Sensitivity: {self.sensitivity_list}")

        print(f"{prefix}Nodes: {len(self.nodes)}")
        print(f"{prefix}Symbols with defs: {len(self.symbol_defs)}")
        print(f"{prefix}Symbols with uses: {len(self.symbol_uses)}")
        print(f"{prefix}Dataflow edges: {len(self.edges)}")

        if self.symbol_defs:
            print(f"\n{prefix}--- Definitions ---")
            for name, defs in self.symbol_defs.items():
                print(f"{prefix}  {name}: {[f'line{d.lineno}({d.def_type})' for d in defs]}")

        if self.symbol_uses:
            print(f"\n{prefix}--- Uses ---")
            for name, uses in self.symbol_uses.items():
                print(f"{prefix}  {name}: {[f'line{u.lineno}({u.use_type})' for u in uses]}")

        # 打印子图
        if self.subgraphs:
            print(f"\n{prefix}--- Subgraphs ({len(self.subgraphs)} always blocks) ---")
            for name, subgraph in self.subgraphs.items():
                subgraph.print_graph(indent + 1)

        if self.assign_graph:
            print(f"\n{prefix}--- Assign Graph ---")
            self.assign_graph.print_graph(indent + 1)


class AlwaysBlockInfo:
    """
    Always块信息
    用于区分组合逻辑和时序逻辑
    """

    def __init__(self, lineno: int, sens_list: List[str], ast_node: Always = None):
        self.lineno = lineno
        self.sensitivity_list = sens_list
        self.ast_node: Optional[Always] = ast_node  # 原始AST节点引用
        self.is_combinational = False
        self.is_sequential = False
        self.clock_signal: Optional[str] = None
        self.reset_signal: Optional[str] = None
        self.edge_type: Dict[str, str] = {}  # 信号名 -> "posedge"/"negedge"/"level"

        self._analyze_sensitivity()

    def _analyze_sensitivity(self):
        """分析敏感列表，确定是组合逻辑还是时序逻辑"""
        if not self.sensitivity_list:
            # 空敏感列表可能是组合逻辑（always @(*) 解析后可能为空）
            self.is_combinational = True
            return

        has_clock = False
        has_reset = False

        for sig in self.sensitivity_list:
            sig_lower = sig.lower()
            edge = "level"

            # 检查边沿触发
            if sig.startswith("posedge "):
                edge = "posedge"
                sig_name = sig[8:].strip()
                has_clock = True
                if self.clock_signal is None:
                    self.clock_signal = sig_name
            elif sig.startswith("negedge "):
                edge = "negedge"
                sig_name = sig[8:].strip()
                if "rst" in sig_lower or "reset" in sig_lower:
                    has_reset = True
                    self.reset_signal = sig_name
                else:
                    has_clock = True
                    if self.clock_signal is None:
                        self.clock_signal = sig_name
            else:
                # 电平敏感
                if "rst" in sig_lower or "reset" in sig_lower:
                    has_reset = True
                    self.reset_signal = sig
                elif "clk" in sig_lower or "clock" in sig_lower:
                    has_clock = True
                    if self.clock_signal is None:
                        self.clock_signal = sig

            self.edge_type[sig] = edge

        # 判断逻辑类型
        if has_clock:
            self.is_sequential = True
        else:
            self.is_combinational = True


class DFGBuilder:
    """
    数据流图构建器
    基于符号表和AST构建数据流图
    支持为每个always块单独构建子图
    """

    def __init__(self, symbol_table_builder: SymbolTableBuilder):
        self.stb = symbol_table_builder     # 符号表构建器
        self.current_scope: Optional[Scope] = None
        self.current_module: Optional[str] = None
        self.current_always_info: Optional[AlwaysBlockInfo] = None  # 当前always块信息

        self.dfgs: Dict[str, DataflowGraph] = {}  # 模块名 -> DFG
        self.current_dfg: Optional[DataflowGraph] = None
        self.current_always_dfg: Optional[DataflowGraph] = None  # 当前always块的子图

        self.node_counter: int = 0
        self.errors: List[str] = []

    def build(self) -> Dict[str, DataflowGraph]:
        """
        为所有模块构建数据流图
        每个模块包含:
        - assign语句的子图
        - 每个always块的独立子图
        """
        self.dfgs = {}

        # 遍历所有模块
        for module_name in self.stb.get_all_modules():
            module_scope = self.stb.get_module_scope(module_name)
            if module_scope:
                dfg = self._build_module_dfg(module_name, module_scope)
                self.dfgs[module_name] = dfg

        return self.dfgs

    def _build_module_dfg(self, module_name: str, module_scope: Scope) -> DataflowGraph:
        """
        为单个模块构建数据流图
        结构:
        - 模块级DFG (包含assign语句和实例化)
        - 每个always块的独立子图
        """
        self.current_module = module_name
        self.current_scope = module_scope
        self.current_dfg = DataflowGraph(module_name, graph_type="module")
        self.current_dfg.parent_module = module_name
        self.node_counter = 0

        # 创建assign语句的子图
        assign_graph = DataflowGraph(f"{module_name}_assigns", graph_type="assign")
        assign_graph.parent_module = module_name
        self.current_dfg.set_assign_graph(assign_graph)

        # 获取模块的 AST 节点
        module_node = module_scope.node
        if not isinstance(module_node, ModuleDef):
            return self.current_dfg

        # 第一遍：处理assign和实例化（模块级）
        for item in module_node.items:
            if isinstance(item, Assign):
                self._process_assign_for_dfg(item, assign_graph)
            elif isinstance(item, InstanceList):
                self._process_instance_list_for_dfg(item, self.current_dfg)

        # 第二遍：为每个always块和initial块创建独立子图
        for item in module_node.items:
            if isinstance(item, Always):
                self._process_always_for_dfg(item)
            elif isinstance(item, Initial):
                self._process_initial_for_dfg(item)

        # 建立数据流边
        self._build_dataflow_edges_for_graph(self.current_dfg)
        if assign_graph.nodes:
            self._build_dataflow_edges_for_graph(assign_graph)
        for subgraph in self.current_dfg.subgraphs.values():
            self._build_dataflow_edges_for_graph(subgraph)

        return self.current_dfg

    def _process_module_item_for_dfg(self, item):
        """处理模块项，构建DFG节点"""
        if isinstance(item, Assign):
            self._process_assign_for_dfg(item)
        elif isinstance(item, Always):
            self._process_always_for_dfg(item)
        elif isinstance(item, Initial):
            self._process_initial_for_dfg(item)
        elif isinstance(item, InstanceList):
            self._process_instance_list_for_dfg(item)

    def _process_assign_for_dfg(self, assign: Assign, dfg: DataflowGraph = None):
        """处理连续赋值，构建DFG"""
        if dfg is None:
            dfg = self.current_dfg

        lineno = assign.lineno
        node = DFGNode(self._next_node_id(), assign, lineno, "assign")

        # 处理左值（定义点）
        lval = assign.left
        if isinstance(lval, Lvalue):
            lval = lval.var

        if isinstance(lval, Identifier):
            symbol = self._lookup_symbol(lval.name)
            if symbol:
                def_site = symbol.add_def_site(
                    lineno=lineno,
                    stmt=assign,
                    def_type='assign'
                )
                node.add_def(def_site)
                dfg.add_def(symbol.name, def_site)

        # 处理右值（使用点）
        rval = assign.right
        if isinstance(rval, Rvalue):
            rval = rval.var

        rhs_symbols = self._extract_symbols_from_expr(rval)
        for sym in rhs_symbols:
            use_site = sym.add_use_site(
                lineno=lineno,
                stmt=assign,
                use_type='rhs'
            )
            node.add_use(use_site)
            dfg.add_use(sym.name, use_site)

        # 更新定义点的 RHS 符号
        for def_site in node.defs:
            def_site.rhs_symbols = rhs_symbols

        dfg.add_node(node)

    def _process_always_for_dfg(self, always: Always):
        """
        处理 always 块，为每个always块创建独立子图
        """
        # 提取和分析敏感列表
        sens_list = self._extract_sensitivity_list(always)
        always_info = AlwaysBlockInfo(always.lineno, sens_list, ast_node=always)
        self.current_always_info = always_info

        # 确定子图类型和名称
        if always_info.is_combinational:
            graph_type = "combinational"
            graph_name = f"{self.current_module}_always_{always.lineno}_comb"
        else:
            graph_type = "sequential"
            graph_name = f"{self.current_module}_always_{always.lineno}_seq"

        # 创建独立的子图
        always_dfg = DataflowGraph(graph_name, graph_type=graph_type)
        always_dfg.parent_module = self.current_module
        always_dfg.sensitivity_list = sens_list
        always_dfg.is_combinational = always_info.is_combinational
        always_dfg.is_sequential = always_info.is_sequential
        always_dfg.clock_signal = always_info.clock_signal
        always_dfg.reset_signal = always_info.reset_signal
        always_dfg.always_ast_node = always  # 存储原始AST节点

        # 添加到父图的子图列表
        self.current_dfg.add_subgraph(graph_name, always_dfg)
        self.current_always_dfg = always_dfg

        # 处理 always 块内的语句
        if always.statement:
            self._process_statement_for_dfg(always.statement, always.lineno, always_dfg)

        # 重置当前always信息
        self.current_always_info = None
        self.current_always_dfg = None


    def _process_initial_for_dfg(self, initial: Initial):
        """
        处理 initial 块，创建独立的子图
        """
        graph_name = f"{self.current_module}_initial_{initial.lineno}"
        
        # 创建独立的子图
        initial_dfg = DataflowGraph(graph_name, graph_type="initial")
        initial_dfg.parent_module = self.current_module
        
        # 添加到父图的子图列表
        self.current_dfg.add_subgraph(graph_name, initial_dfg)

        
        # 处理 initial 块内的语句
        if initial.statement:
            self._process_statement_for_dfg(initial.statement, initial.lineno, initial_dfg)

    def _process_statement_for_dfg(self, stmt, parent_lineno: int, dfg: DataflowGraph = None):
        """
        递归处理语句构建DFG
        dfg 参数指定将节点添加到哪个图中（默认为当前always子图或模块级图）
        """
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        if isinstance(stmt, Block):
            self._process_block_for_dfg(stmt, parent_lineno, dfg)

        elif isinstance(stmt, Substitution):
            self._process_substitution_for_dfg(stmt, parent_lineno, dfg)

        elif isinstance(stmt, NonblockingSubstitution):
            self._process_nonblocking_substitution_for_dfg(stmt, parent_lineno, dfg)

        elif isinstance(stmt, IfStatement):
            self._process_if_statement_for_dfg(stmt, parent_lineno, dfg)

        elif isinstance(stmt, CaseStatement):
            self._process_case_statement_for_dfg(stmt, parent_lineno, dfg)

        elif isinstance(stmt, ForStatement):
            self._process_for_statement_for_dfg(stmt, parent_lineno, dfg)

    def _process_block_for_dfg(self, block: Block, parent_lineno: int, dfg: DataflowGraph):
        """处理语句块"""
        for s in block.statements:
            self._process_statement_for_dfg(s, parent_lineno, dfg)

    def _process_substitution_for_dfg(self, subst: Substitution, parent_lineno: int, dfg: DataflowGraph = None):
        """处理阻塞赋值，构建DFG"""
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        lineno = subst.lineno
        node = DFGNode(self._next_node_id(), subst, lineno, "blocking_assign")

        # 左值
        lval = subst.left
        if isinstance(lval, Lvalue):
            lval = lval.var

        if isinstance(lval, Identifier):
            symbol = self._lookup_symbol(lval.name)
            if symbol:
                def_site = symbol.add_def_site(
                    lineno=lineno,
                    stmt=subst,
                    def_type='blocking'
                )
                node.add_def(def_site)
                dfg.add_def(symbol.name, def_site)

        # 右值
        rval = subst.right
        if isinstance(rval, Rvalue):
            rval = rval.var

        rhs_symbols = self._extract_symbols_from_expr(rval)
        for sym in rhs_symbols:
            use_site = sym.add_use_site(
                lineno=lineno,
                stmt=subst,
                use_type='rhs'
            )
            node.add_use(use_site)
            dfg.add_use(sym.name, use_site)

        for def_site in node.defs:
            def_site.rhs_symbols = rhs_symbols

        dfg.add_node(node)

    def _process_nonblocking_substitution_for_dfg(self, subst: NonblockingSubstitution, parent_lineno: int, dfg: DataflowGraph = None):
        """处理非阻塞赋值，构建DFG"""
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        lineno = subst.lineno
        node = DFGNode(self._next_node_id(), subst, lineno, "nonblocking_assign")

        # 左值
        lval = subst.left
        if isinstance(lval, Lvalue):
            lval = lval.var

        if isinstance(lval, Identifier):
            symbol = self._lookup_symbol(lval.name)
            if symbol:
                def_site = symbol.add_def_site(
                    lineno=lineno,
                    stmt=subst,
                    def_type='nonblocking'
                )
                node.add_def(def_site)
                dfg.add_def(symbol.name, def_site)

        # 右值
        rval = subst.right
        if isinstance(rval, Rvalue):
            rval = rval.var

        rhs_symbols = self._extract_symbols_from_expr(rval)
        for sym in rhs_symbols:
            use_site = sym.add_use_site(
                lineno=lineno,
                stmt=subst,
                use_type='rhs'
            )
            node.add_use(use_site)
            dfg.add_use(sym.name, use_site)

        for def_site in node.defs:
            def_site.rhs_symbols = rhs_symbols

        dfg.add_node(node)

    def _process_if_statement_for_dfg(self, if_stmt: IfStatement, parent_lineno: int, dfg: DataflowGraph = None):
        """处理 if 语句，构建条件使用"""
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        # 处理条件表达式中的使用
        cond = if_stmt.cond
        cond_symbols = self._extract_symbols_from_expr(cond)
        for sym in cond_symbols:
            use_site = sym.add_use_site(
                lineno=if_stmt.lineno,
                stmt=if_stmt,
                use_type='condition'
            )
            dfg.add_use(sym.name, use_site)

        # 处理 then 分支
        if if_stmt.true_statement:
            self._process_statement_for_dfg(if_stmt.true_statement, parent_lineno, dfg)

        # 处理 else 分支
        if if_stmt.false_statement:
            self._process_statement_for_dfg(if_stmt.false_statement, parent_lineno, dfg)

    def _process_case_statement_for_dfg(self, case_stmt: CaseStatement, parent_lineno: int, dfg: DataflowGraph = None):
        """处理 case 语句"""
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        # 处理选择表达式
        comp = case_stmt.comp
        comp_symbols = self._extract_symbols_from_expr(comp)
        for sym in comp_symbols:
            use_site = sym.add_use_site(
                lineno=case_stmt.lineno,
                stmt=case_stmt,
                use_type='condition'
            )
            dfg.add_use(sym.name, use_site)

        # 处理各个 case
        for case in case_stmt.caselist:
            if case.statement:
                self._process_statement_for_dfg(case.statement, parent_lineno, dfg)

    def _process_for_statement_for_dfg(self, for_stmt: ForStatement, parent_lineno: int, dfg: DataflowGraph = None):
        """处理 for 语句"""
        if dfg is None:
            dfg = self.current_always_dfg if self.current_always_dfg else self.current_dfg

        # 处理初始化
        if for_stmt.pre:
            self._process_statement_for_dfg(for_stmt.pre, parent_lineno, dfg)

        # 处理循环体
        if for_stmt.statement:
            self._process_statement_for_dfg(for_stmt.statement, parent_lineno, dfg)

    def _process_instance_list_for_dfg(self, instance_list: InstanceList, dfg: DataflowGraph = None):
        """处理实例化，构建端口连接的数据流"""
        if dfg is None:
            dfg = self.current_dfg

        for instance in instance_list.instances:
            lineno = instance.lineno

            # 处理端口连接
            if hasattr(instance, 'portlist') and instance.portlist:
                # portlist could be a tuple or a Portlist object
                ports = instance.portlist
                if hasattr(ports, 'ports'):
                    ports = ports.ports
                for port_arg in ports:
                    if hasattr(port_arg, 'argname') and port_arg.argname:
                        # 端口参数是一个表达式，提取其中的符号
                        symbols = self._extract_symbols_from_expr(port_arg.argname)
                        for sym in symbols:
                            use_site = sym.add_use_site(
                                lineno=lineno,
                                stmt=instance,
                                use_type='instance_port'
                            )
                            dfg.add_use(sym.name, use_site)

    def _extract_symbols_from_expr(self, expr) -> List[Symbol]:
        """
        从表达式中提取所有使用的符号
        递归遍历表达式树
        """
        symbols = []

        if expr is None:
            return symbols

        if isinstance(expr, Identifier):
            symbol = self._lookup_symbol(expr.name)
            if symbol:
                symbols.append(symbol)

        elif isinstance(expr, Pointer):
            # 数组访问
            if isinstance(expr.var, Identifier):
                symbol = self._lookup_symbol(expr.var.name)
                if symbol:
                    symbols.append(symbol)
            # 索引表达式
            if hasattr(expr, 'ptr') and expr.ptr:
                symbols.extend(self._extract_symbols_from_expr(expr.ptr))

        elif isinstance(expr, Partselect):
            # 位选择
            if isinstance(expr.var, Identifier):
                symbol = self._lookup_symbol(expr.var.name)
                if symbol:
                    symbols.append(symbol)
            # MSB 和 LSB
            if hasattr(expr, 'msb') and expr.msb:
                symbols.extend(self._extract_symbols_from_expr(expr.msb))
            if hasattr(expr, 'lsb') and expr.lsb:
                symbols.extend(self._extract_symbols_from_expr(expr.lsb))

        elif isinstance(expr, Operator):
            # 运算符 - 递归处理左右操作数
            if hasattr(expr, 'left') and expr.left:
                symbols.extend(self._extract_symbols_from_expr(expr.left))
            if hasattr(expr, 'right') and expr.right:
                symbols.extend(self._extract_symbols_from_expr(expr.right))
            if hasattr(expr, 'cond') and expr.cond:
                symbols.extend(self._extract_symbols_from_expr(expr.cond))
            if hasattr(expr, 'true_value') and expr.true_value:
                symbols.extend(self._extract_symbols_from_expr(expr.true_value))
            if hasattr(expr, 'false_value') and expr.false_value:
                symbols.extend(self._extract_symbols_from_expr(expr.false_value))

        elif isinstance(expr, Concat):
            # 拼接操作
            if hasattr(expr, 'list') and expr.list:
                for item in expr.list:
                    symbols.extend(self._extract_symbols_from_expr(item))

        elif isinstance(expr, Repeat):
            # 重复操作
            if hasattr(expr, 'value') and expr.value:
                symbols.extend(self._extract_symbols_from_expr(expr.value))

        elif isinstance(expr, Lvalue) or isinstance(expr, Rvalue):
            if hasattr(expr, 'var') and expr.var:
                symbols.extend(self._extract_symbols_from_expr(expr.var))

        return symbols

    def _extract_sensitivity_list(self, always: Always) -> List[str]:
        """提取 always 块的敏感列表"""
        sensitivity = []
        if hasattr(always, 'sens_list') and always.sens_list:
            for item in always.sens_list.list:
                if isinstance(item, Sens):
                    if item.sig:
                        sig_name = str(item.sig)
                        sensitivity.append(sig_name)
        return sensitivity

    def _build_dataflow_edges_for_graph(self, dfg: DataflowGraph):
        """
        为指定图建立数据流边
        将每个使用点连接到其最近的定义点
        """
        for symbol_name, uses in dfg.symbol_uses.items():
            defs = dfg.symbol_defs.get(symbol_name, [])

            for use in uses:
                # 找到此使用之前的最后一个定义
                last_def = None
                for def_site in defs:
                    if def_site.lineno <= use.lineno:
                        if last_def is None or def_site.lineno > last_def.lineno:
                            last_def = def_site

                # 如果没有找到定义且符号是输入，创建一个隐式定义
                if last_def is None and use.symbol.is_input():
                    # 输入端口在模块入口隐式定义
                    last_def = use.symbol.add_def_site(
                        lineno=0,  # 模块开始
                        stmt=None,
                        def_type='port'
                    )
                    use.symbol.mark_initialized()
                    dfg.add_def(symbol_name, last_def)

                if last_def:
                    dfg.add_edge(last_def, use)

    def _lookup_symbol(self, name: str) -> Optional[Symbol]:
        """查找符号"""
        return self.stb.lookup(name, self.current_scope)

    def _next_node_id(self) -> int:
        """获取下一个节点ID"""
        self.node_counter += 1
        return self.node_counter

    def get_dfg(self, module_name: str) -> Optional[DataflowGraph]:
        """获取指定模块的数据流图"""
        return self.dfgs.get(module_name)

    def get_all_dfgs(self) -> Dict[str, DataflowGraph]:
        """获取所有模块的数据流图"""
        return self.dfgs
