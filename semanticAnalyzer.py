from pyverilog.vparser.parser import parse
from pyverilog.vparser.ast import *


class Symbol:
    def __init__(self, name, scope, linenum, node):
        self.name = name
        self.scope = scope
        self.linenum = linenum,
        self.node = node
        self.level = 0
        self.isinit = False

    def getName(self):
        return self.name

    def getType(self):
        return self.type

    def getScope(self):
        return self.scope

    def getLine(self):
        return self.linenum

    def getNode(self):
        return self.node


class SymbolTable:
    def __init__(self, fatherSymbolTable, node, scopeLevel: int):
        self.symbolMap = {}  # 替代Java的LinkedHashMap
        self.fatherSymbolTable = fatherSymbolTable  # 父符号表
        self.sonSymbolTable = []  # 子符号表列表
        self.node = node  # 关联的语法节点
        self.scopeLevel = scopeLevel  # 作用域层级

    def getScopeLevel(self) -> int:
        """获取作用域层级"""
        return self.scopeLevel

    def setNode(self, node):
        """设置关联的语法节点"""
        self.node = node

    def addSymbol(self, symbol: Symbol):
        """添加一个符号到当前符号表"""
        self.symbolMap[symbol.getName()] = symbol

    def containSymbol(self, name: str) -> bool:
        """判断符号是否存在于当前符号表（仅判断名称）"""
        return name in self.symbolMap

    def getSymbol(self, symbolName):
        """根据名称获取符号：当前表找不到则逐级找父表，直到根表"""
        current_table = self  # 从当前表开始
        while current_table is not None:  # 循环直到无父表
            for symbol in current_table.symbolMap.keys():
                if symbolName == symbol:
                    return current_table.symbolMap[symbol]
                # print(symbolName+" "+symbol)
            # 没找到，切换到父表继续找
            current_table = current_table.fatherSymbolTable
        # 所有表都没找到，返回None
        return None

    def addSon(self, symbolTable):
        """添加子符号表"""
        self.sonSymbolTable.append(symbolTable)


class SemanticAnalyzer:
    def __init__(self):
        self.tables = []
        self.modules = []
        self.cfg = {}  # 构建基于数据的CFG流图
        self.dfg = {}  # 构建数据流图，其中a的前驱列表是self.dfg[a]
        self.stmt = []  # 所有有关的句子
        self.always = []
        self.caseStatements = []
        self.decl = []
        self.ast = None
        self.stmts = []
        self.oriinstanceMap = {}
        self.curinstanceMap = {}
        self.instances = []
        self.pointers = []
        self.pointerMap = {}
        self.constantMap = {}
        self.right_value = set()
        self.branch = {}
        self.moduleBranch={}
        self.DefAlways={}

    def buildDecl(self, item, symbolTable):
        for attr in item.children():
            if isinstance(attr, Assign):
                self.buildAssign(attr, symbolTable)
                return
            symbol = Symbol(attr.name, symbolTable.getScopeLevel(), attr.lineno, item)
            if symbol.name not in self.dfg:
                self.dfg[symbol.name]=[]
            if symbolTable.getSymbol(attr.name) is None:
                symbolTable.addSymbol(symbol)
                self.decl.append(attr)
            if isinstance(attr, Input):
                symbol = Symbol(attr.name, symbolTable.getScopeLevel(), attr.lineno, item)
                if symbolTable.getSymbol(attr.name) is None:
                    symbolTable.addSymbol(symbol)
                symbolTable.getSymbol(attr.name).isinit = True
            if isinstance(attr,Parameter):
                symbol = Symbol(attr.name, symbolTable.getScopeLevel(), attr.lineno, item)
                if symbolTable.getSymbol(attr.name) is None:
                    symbolTable.addSymbol(symbol)
                symbolTable.getSymbol(attr.name).isinit = True

        return

    def buildBlock(self, item, symbolTable, curmodule,alway):
        for attr in item.children():
            if isinstance(attr, IfStatement):
                self.buildIfStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, CaseStatement):
                self.buildCaseStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, WhileStatement):
                self.buildWhileStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, ForStatement):
                self.buildForStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, Substitution):
                self.buildSubstitution(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, GenerateStatement):
                self.buildGenerateStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, InstanceList):
                curmodule.items = curmodule.items + (attr,)
                for instance in attr.instances:
                    self.buildInstance(instance, symbolTable, curmodule,alway)
        return

    def buildGenerateStatement(self, item, symbolTable, curmodule,alway):
        for attr in item.children():
            if isinstance(attr, Decl):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable.sonSymbolTable.append(new_symbolTable)
                new_symbolTable.fatherSymbolTable = symbolTable
                symbolTable = new_symbolTable
                self.buildDecl(attr, symbolTable)
            elif isinstance(attr, ForStatement):
                self.buildForStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, Block):
                self.buildBlock(attr, symbolTable, curmodule,alway)
        return

    def buildIfStatement(self, item, symbolTable, curmodule,alway):
        if not item.false_statement:
            log_print("可能缺少分支，注意查看第" + str(item.lineno) + "行")
        self.buildCond(item.cond, None, symbolTable, curmodule)
        if curmodule not in self.branch:
            self.branch[curmodule] = []
        self.branch[curmodule].append(item.cond)
        for attr in item.children():
            if isinstance(attr, Block):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable.sonSymbolTable.append(new_symbolTable)
                new_symbolTable.fatherSymbolTable = symbolTable
                self.buildBlock(attr, new_symbolTable, curmodule,alway)
            elif isinstance(attr, Substitution):
                self.buildSubstitution(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, IfStatement):
                self.buildIfStatement(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, CaseStatement):
                self.buildCaseStatement(attr, symbolTable, curmodule)
        return

    def buildCase(self, item, symbolTable, curmodule,alway):
        for attr in item.children():
            if isinstance(attr, Block):
                self.buildBlock(attr, symbolTable, curmodule,alway)
            elif isinstance(attr, Substitution):
                self.buildSubstitution(attr, symbolTable, curmodule,alway)
        return

    def buildCaseDfg(self, item, case, symbolTable, curmodule):
        if case.cond:
            self.buildCond(case.cond, str(item.comp), symbolTable, curmodule)
        for attr in case.children():
            if isinstance(attr, Identifier):
                if attr.name not in self.dfg:
                    self.dfg[attr.name] = []
                    if item.comp not in self.dfg[attr.name]:
                        self.dfg[attr.name].append(item.comp)

    def buildCasesDfg(self, item, cases, symbolTable, curmodule):
        for case in cases:
            self.buildCaseDfg(item, case, symbolTable, curmodule)
        return

    def buildCaseStatement(self, item, symbolTable, curmodule,alway):
        self.caseStatements.append(item)
        cases = []
        comp = symbolTable.getSymbol(str(item.comp))
        if str(item.comp) not in self.dfg:
            self.dfg[str(item.comp)]=[]
            self.dfg[str(item.comp)].append(comp)
        else: self.dfg[str(item.comp)].append(comp)
        flag = True
        for attr in item.children():
            if isinstance(attr, Case):
                self.buildCase(attr, symbolTable, curmodule,alway)
                cases.append(attr)
                if attr.cond is None:
                    flag = False
        if flag:
            log_print("CaseStatement缺少默认选项，在第" + str(item.lineno) + "行")
        self.buildCasesDfg(item, cases, symbolTable, curmodule)

        return

    def buildWhileStatement(self, item, symbolTable, curmodule):
        for attr in item.children():
            if isinstance(attr, Block):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable.sonSymbolTable.append(new_symbolTable)
                new_symbolTable.fatherSymbolTable = symbolTable
                self.buildBlock(attr, new_symbolTable, curmodule)
        return

    def buildForStatement(self, item, symbolTable, curmodule,alway):
        first = True
        for attr in item.children():
            if isinstance(attr, Block):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable.sonSymbolTable.append(new_symbolTable)
                new_symbolTable.fatherSymbolTable = symbolTable
                self.buildBlock(attr, new_symbolTable, curmodule,alway)
            elif isinstance(attr, Substitution):
                if first:
                    new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                    symbolTable.sonSymbolTable.append(new_symbolTable)
                    new_symbolTable.fatherSymbolTable = symbolTable
                    first = False
                    symbolTable = new_symbolTable
                    self.buildSubstitution(attr, symbolTable, curmodule,alway)
                else:
                    self.buildSubstitution(attr, symbolTable, curmodule,alway)

        return

    def buildValue(self, item, symbolTable):
        for attr in item.children():
            symbol = Symbol(attr.name, symbolTable.getScopeLevel(), attr.lineno, item)
            if symbolTable.getSymbol(attr.name) is None:
                symbolTable.addSymbol(symbol)
        return

    def buildSubstitutionDfg(self, lval, rval, symbolTable):
        max = 0
        # 往dfg中添加val
        if lval.name not in self.dfg:
            self.dfg[lval.name] = []
        if self.dfg[lval.name] is None:
            self.dfg[lval.name] = rval
        else:
            list = self.dfg[lval.name]
            for val in rval:
                if val not in list:
                    if val.level > max:
                        max = val.level
                    list.append(val)
            lval.level = max + 1
            self.dfg[lval.name] = list
        for item in list:
            if not isinstance(item,Identifier):
                continue
            if item.isinit == False:
                log_print(str(item.name) + "未初始化,在第" + str(item.node.lineno) + "行")
        return

    def buildSubstitution(self, item, symbolTable, curmodule,alway):
        self.stmts.append(item)
        lval = None
        rval = []
        for val in item.children():
            if isinstance(val, Lvalue):
                symbol = Symbol(str(val.var), symbolTable.getScopeLevel(), val.lineno, item)
                if symbolTable.getSymbol(str(val.var)) is None:
                    symbolTable.addSymbol(symbol)
                lval = symbolTable.getSymbol(str(val.var))
            elif isinstance(val, Rvalue):
                self.buildRvalue(val, symbolTable, rval)
            if isinstance(val.var, Pointer):
                self.pointers.append(val.var)
                self.pointerMap[val.var] = curmodule
        self.buildSubstitutionDfg(lval, rval, symbolTable)
        if lval not in self.DefAlways:
            self.DefAlways[lval]=set()
        self.DefAlways[lval].add(alway)
        lval.isinit = True
        return

    def buildAlways(self, item, symbolTable, curmodule,alway):
        self.always.append(item)
        new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
        symbolTable.sonSymbolTable.append(new_symbolTable)
        new_symbolTable.fatherSymbolTable = symbolTable
        symbolTable = new_symbolTable
        for attr in item.children():
            if isinstance(attr, Block):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable = new_symbolTable
                symbolTable.sonSymbolTable.append(new_symbolTable)
                self.buildBlock(attr, new_symbolTable, curmodule,item)
        return

    def buildAssign(self, item, symbolTable):
        self.stmts.append(item)
        lval = None
        rval = []
        for val in item.children():
            if isinstance(val, Lvalue):
                symbol = Symbol(str(val.var), symbolTable.getScopeLevel(), val.lineno, item)
                if symbolTable.getSymbol(str(val.var)) is None:
                    symbolTable.addSymbol(symbol)
                lval = symbolTable.getSymbol(str(val.var))
                # lval.isinit=True
            elif isinstance(val, Rvalue):
                self.buildRvalue(val, symbolTable, rval)
        self.buildSubstitutionDfg(lval, rval, symbolTable)
        lval.isinit = True
        if lval not in self.DefAlways:
            self.DefAlways[lval]=set()
        self.DefAlways[lval].add(None)
        return

    def buildInstance(self, instance, symbolTable, curmodule,alway):
        for module in self.modules:
            if module.name == instance.module:
                if instance not in self.oriinstanceMap:
                    self.oriinstanceMap[instance] = None
                self.oriinstanceMap[instance] = module
                self.curinstanceMap[instance] = curmodule
                symbol = Symbol(str(instance.name), symbolTable.getScopeLevel(), instance.lineno, instance)
                symbolTable.addSymbol(symbol)
                self.instances.append(instance)
                for item in instance.children():
                    if isinstance(item, PortArg):
                        for attr in item.children():
                            if isinstance(attr, Pointer):
                                self.pointers.append(attr)
                                self.pointerMap[attr] = curmodule
                return

    def buildInstances(self, item, symbolTable, curmodule,alway):
        for instance in item.children():
            if isinstance(instance, Instance):
                self.buildInstance(instance, symbolTable, curmodule,alway)
        return

    def buildIoport(self, port, symbolTable):
        symbol = Symbol(str(port.first.name), symbolTable.getScopeLevel(), port.lineno, port)
        symbolTable.addSymbol(symbol)
        if isinstance(port.first,Input):
            symbol.isinit=True
        if symbol.name not in self.dfg:
            self.dfg[symbol.name]=[]
        if symbol.name not in self.DefAlways:
            self.DefAlways[symbol]=set()
        return

    def buildPortlist(self, ports, symbolTable):
        for port in ports.children():
            if isinstance(port, Ioport):
                self.buildIoport(port, symbolTable)
        return

    def buildParamlist(self, params, symbolTable):
        for param in params.children():
            if isinstance(param, Parameter):
                symbol = Symbol(str(param.name), symbolTable.getScopeLevel(), param.lineno, param)
                symbolTable.addSymbol(symbol)
                if symbol.name not in self.dfg:
                    self.dfg[symbol.name] = []
                if symbol.name not in self.DefAlways:
                    self.DefAlways[symbol]=set()
                return

        return

    def analyzer(self, ast):
        self.ast = ast
        description = ast.description
        modules = description.children()
        for module in modules:
            self.modules.append(module)
        for module in modules:
            rootTable = SymbolTable(None, module, 0)
            self.tables.append(rootTable)
            self.buildParamlist(module.paramlist, rootTable)
            self.buildPortlist(module.portlist, rootTable)
            for item in module.items:
                if isinstance(item, Decl):
                    self.buildDecl(item, rootTable)
                if isinstance(item, Always):
                    self.buildAlways(item, rootTable, module,item)
                if isinstance(item, Assign):
                    self.buildAssign(item, rootTable)
                if isinstance(item, InstanceList):
                    self.buildInstances(item, rootTable, module,None)
                if isinstance(item, GenerateStatement):
                    self.buildGenerateStatement(item, rootTable, module,None)
                if isinstance(item, Initial):
                    self.buildInitial(item, rootTable, module)

    def buildInitial(self, item, symbolTable, curmodule):
        for attr in item.children():
            if isinstance(attr, Decl):
                new_symbolTable = SymbolTable(symbolTable, item, symbolTable.scopeLevel + 1)
                symbolTable.sonSymbolTable.append(new_symbolTable)
                new_symbolTable.fatherSymbolTable = symbolTable
                symbolTable = new_symbolTable
                self.buildDecl(attr, symbolTable)
            elif isinstance(attr, ForStatement):
                self.buildForStatement(attr, symbolTable, curmodule)
            elif isinstance(attr, Block):
                self.buildBlock(attr, symbolTable, curmodule)
        return

    #     进一步解析ReValue的值，Substitution和assign均有用到，但是assign的内容是一旦赋值就需要被改变，因此assign需要考虑毛刺，
    #     此处的主要内容是解析Rvalue，构建数据依赖，为后续检查assign的右值的层级是否对应
    def buildRvalue(self, val, symbolTable, rval):
        if isinstance(val, Variable):
            symbol = Symbol(str(val), symbolTable.getScopeLevel(), val.lineno, val)
            if not symbolTable.getSymbol(str(val)):
                symbolTable.addSymbol(symbol)
            if symbolTable.getSymbol(str(val)) not in rval:
                rval.append(symbolTable.getSymbol(str(val)))
        elif isinstance(val, Identifier):
            symbol = Symbol(str(val), symbolTable.getScopeLevel(), val.lineno, val)
            if not symbolTable.getSymbol(str(val)):
                symbolTable.addSymbol(symbol)
            if symbolTable.getSymbol(str(val)) not in rval:
                rval.append(symbolTable.getSymbol(str(val)))
        elif isinstance(val, IntConst):
            return
        elif isinstance(val,Concat):
            return
        elif isinstance(val,Cond):
            return
        elif isinstance(val.var, Identifier):
            symbol = Symbol(str(val.var), symbolTable.getScopeLevel(), val.var.lineno, val.var)
            if not symbolTable.getSymbol(str(val.var)):
                symbolTable.addSymbol(symbol)
            if symbolTable.getSymbol(str(val.var)) not in rval:
                rval.append(symbolTable.getSymbol(str(val.var)))
        elif isinstance(val.var, IntConst):
            return
        elif isinstance(val.var, UnaryOperator):
            symbol = Symbol(str(val.var.right), symbolTable.getScopeLevel(), val.var.right.lineno, val.var.right)
            if not symbolTable.getSymbol(str(val.var.right)):
                symbolTable.addSymbol(symbol)
            if symbolTable.getSymbol(str(val.var.right)) not in rval:
                rval.append(symbolTable.getSymbol(str(val.var.right)))
        elif isinstance(val.var, Operator):
            if isinstance(val.var, Cond):
                self.buildRvalue(val.var.true_value, symbolTable, rval)
                self.buildRvalue(val.var.false_value, symbolTable, rval)
            else:
                symbol = Symbol(str(val.var.left), symbolTable.getScopeLevel(), val.var.left.lineno, val.var.left)
                if not symbolTable.getSymbol(str(val.var.left)):
                    symbolTable.addSymbol(symbol)
                self.buildRvalue(val.var.right, symbolTable, rval)
                if symbolTable.getSymbol(str(val.var.left)) not in rval:
                    rval.append(symbolTable.getSymbol(str(val.var.left)))
        return
    # 考虑汉明码的码矩
    def buildCond(self, cond, type, symbolTable, curmodule):
        # ifStatement
        if type is None:
            if isinstance(cond, UnaryOperator):
                self.buildBranch(cond.right, symbolTable,curmodule)
                return
            if isinstance(cond, Eq) or isinstance(cond,LessEq) or isinstance(cond,NotEq)\
                    or isinstance(cond,GreaterEq) or isinstance(cond,LessThan) or isinstance(cond,GreaterThan):
                if cond.left in self.constantMap:
                    self.constantMap[cond.left].add(cond.right)
                else:
                    self.constantMap[cond.left] = set()
                    self.constantMap[cond.left].add(cond.right)
                self.buildBranch(cond.left, symbolTable,curmodule)
        else:
            # CasesStatement
            if isinstance(cond[0], Identifier):
                symbol = symbolTable.getSymbol(cond[0].name)
                if isinstance(symbol.node, Decl):
                    for item in symbol.node.list:
                        if isinstance(item, Parameter):
                            if isinstance(item.value, Rvalue):
                                if isinstance(item.value.var, IntConst):
                                    if type in self.constantMap:
                                        self.constantMap[type].add(item.value.var)
                                    else:
                                        self.constantMap[type] = set()
                                        self.constantMap[type].add(item.value.var)
                self.buildBranch(type, symbolTable,curmodule)
            elif isinstance(cond[0], IntConst):
                if type in self.constantMap:
                    self.constantMap[type].add(cond[0])
                else:
                    self.constantMap[type] = set()
                    self.constantMap[type].add(cond[0])
        return

    # 构建分支，如果决定分支的是输入，那么该模块的输入不能为常数，否则在优化时会省略部分分支导致错误
    def buildBranch(self, cond, symbolTable,curmodule):
        if symbolTable.getSymbol(str(cond)):
            if curmodule not in self.moduleBranch:
                self.moduleBranch[curmodule]=[]
            list=[]
            for port in curmodule.portlist.ports:
                list.append(port)
            for param in curmodule.paramlist.params:
                list.append(param)
            if self.IsInput(symbolTable.getSymbol(str(cond)),list,symbolTable):
                self.moduleBranch[curmodule].append(symbolTable.getSymbol(str(cond)))
        return

    def IsInput(self, param, list,symbolTable):
        for item in list:
            symbol=None
            if isinstance(item,Ioport):
                symbol=symbolTable.getSymbol(item.first.name)
            else:
                symbol=symbolTable.getSymbol(item.name)
            if param == symbol:
                return True
        return False

def log_print(msg,flag=False):
    log_msg = f"{msg}"
    # 追加写入文件
    with open('dma_log.txt', 'a', encoding='utf-8') as f:
        f.write(log_msg + '\n')

