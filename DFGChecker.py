import semanticAnalyzer
from pyverilog.vparser.ast import *
from semanticAnalyzer import *

class DFGChecker:
    def __init__(self, semanticAnalyzer):
        self.always = semanticAnalyzer.always
        self.caseStatements = semanticAnalyzer.caseStatements
        self.dfg = semanticAnalyzer.dfg
        self.isError = False
        self.ErrorList = []
        self.stmts=semanticAnalyzer.stmts
        self.constantMap=semanticAnalyzer.constantMap
        self.instances=semanticAnalyzer.instances
        self.defAlways = semanticAnalyzer.DefAlways
    def check(self):
        self.checkgitch()
        self.checkhanMing()
        self.checkDeadState()
        self.checkCobineloop()
    def checkgitch(self):
        assigns=[]
        for stmt in self.stmts:
            if isinstance(stmt,Assign):
                assigns.append(stmt)
        dfgs={}
        for key,values in self.dfg.items():
            for assign in assigns:
             if key==str(assign.left.var):
                 dfgs[key]=values
        valuesRelay={}
        for key,values in dfgs.items():
            if values is None:continue
            list=self.judgeValues(values)
            valuesRelay[key]=list
            # val=values[0].level
        for key,values in dfgs.items():
            if not self.findgitch(values,valuesRelay):
                log_print(str(key)+"存在毛刺，即不同时到达改变变量值")
    def checkDeadState(self):
        for caseStatement in self.caseStatements:
            self.checkCaseStatements(caseStatement)
        return

    def findComp(self, defStatementMap, caseStatement):
        list = []
        if str(caseStatement.comp) not in self.dfg:return list
        temp_relays = self.dfg[str(caseStatement.comp)]
        temp_relays.append(caseStatement.comp)
        relays = []
        for relay in temp_relays:
            relays.append(relay.name)
        for value in defStatementMap.keys():
            if str(value) in relays:
                substitutions = defStatementMap[value]
                for substitution in substitutions:
                    list.append(str(substitution.right.var))
        return list

    def checkCaseStatements(self, caseStatement):
        cases = caseStatement.caselist
        self.checkCovered(caseStatement)
        caseVisited = {}
        for case in cases:
            if case.cond is None:
                continue
            if isinstance(case.cond[0],Identifier):
                caseVisited[case.cond[0].name]=False
            elif isinstance(case.cond[0],Constant):
                caseVisited[case.cond[0].value] = False
        for case in cases:
            if case.cond is None:
                continue
            caseDef = CaseDef(case)
            caseDef.buildCaseDef()
            turnlist = self.findComp(caseDef.defStatementMap, caseStatement)
            if turnlist == []:
                return
            flag = False
            for turn in turnlist:
                if isinstance(case.cond[0],Identifier):
                    if turn == case.cond[0].name:
                        flag = True
                        break
                elif isinstance(case.cond[0],Constant):
                    if turn == case.cond[0].value:
                        flag = True
                        break
            if flag and len(turnlist)==1:
                self.isError = True
                log_print("可能存在死亡状态，注意第" + str(case.lineno) + "行")
            for turn in turnlist:
                caseVisited[turn] = True
        for key, value in caseVisited.items():
            if not value:
                self.isError = True
                for case in cases:
                    if case.cond:
                        if key == case.cond[0].name:
                            log_print("该Case无法到达,在第" + str(case.cond[0].lineno) + "行")

    # 判断所有依赖的节点
    def judgeValues(self, values):
        list=[]
        for value in values:
            list.append(value)
            if value.name not in self.dfg: continue
            if value in self.dfg[value.name]: continue
            value_list=self.judgeValues(self.dfg[value.name])
            for v in value_list:
                if v not in list:
                    list.append(v)
        return list

    def findgitch(self, values, valuesRelay):
        relay_all_elements = set()
        common_unique=set()
        flag=True
        if values is None or values==[]: return True
        mark = values[0].level
        first=True
        for value in values:
            if value.name not in valuesRelay:
                relay_all_elements=set()
                common_unique=set(common_unique&relay_all_elements)
                first=False
                continue
            if first:
                common_unique = set(valuesRelay[value.name])
                first=False
            relay_all_elements=set(valuesRelay[value.name])
            common_unique = set(common_unique & relay_all_elements)
            if value.level!=mark:
                flag=False
        if common_unique and flag==False:
            return False
        return True

    def checkCovered(self, caseStatement):
        comp=caseStatement.comp
        for relay in self.dfg[str(comp)]:
            if isinstance(relay.node,Decl):
                return
            elif isinstance(relay.node,Ioport):
                msb = relay.node.first.width.msb
                lsb = relay.node.first.width.lsb
                if isinstance(msb,IntConst):
                    if isinstance(lsb,IntConst):
                        length=int(msb.value)-int(lsb.value)
                        l = pow(2,length)
                        if l > len(caseStatement.caselist):
                            log_print("分支未完全覆盖,在第"+str(caseStatement.lineno)+"行")

    def checkhanMing(self):
        for values in self.constantMap.values():
            self.checkhanmingValue(values)

    def is_str_decimal(self, value_str):
        """判断输入字符串是否为十进制数字（如"0"、"1"）"""
        if not isinstance(value_str, str):
            return False
        # 排除含'b'的Verilog编码，仅纯数字字符串判定为十进制
        return value_str.isdigit() and 'b' not in value_str.lower()

    def str_dec_to_verilog_bin(self, dec_str, bit_width=2):
        """字符串十进制转为Verilog二进制编码（默认位宽2）"""
        dec_val = int(dec_str)
        bin_str = bin(dec_val)[2:].zfill(bit_width)
        return f"{bit_width}'b{bin_str}"

    def parse_verilog_code(self, verilog_str):
        """
        兼容解析：
        - Verilog编码（如"2'b00"）→ 纯二进制串
        - 十进制字符串（如"0"）→ 先转Verilog编码再解析为二进制串
        """
        # 先判断是否为十进制字符串
        if self.is_str_decimal(verilog_str):
            # 十进制str→Verilog编码（默认位宽2，可根据需求调整）
            verilog_str = self.str_dec_to_verilog_bin(verilog_str)

        # 原有Verilog编码解析逻辑
        parts = verilog_str.split("'")
        if len(parts) != 2:
            raise ValueError(f"无效的 Verilog 数值格式: {verilog_str}")

        # 提取基数（h=十六进制，b=二进制，d=十进制，o=八进制）和数值
        base_char = parts[1][0].lower()
        value_str = parts[1][1:]

        # 映射基数到 int() 对应的参数
        base_map = {
            'h': 16,  # 十六进制
            'b': 2,  # 二进制
            'd': 10,  # 十进制
            'o': 8  # 八进制
        }
        return str(int(value_str, base_map[base_char]))

    def calc_hamming_distance(self, bin1, bin2):
        return sum(c1 != c2 for c1, c2 in zip(bin1, bin2))

    def checkhanmingValue(self, values):
        """
        批量检查汉明距离，兼容两种输入：
        - values中元素的value属性为Verilog编码（如"2'b00"）
        - values中元素的value属性为十进制字符串（如"0"）
        """
        # 解析所有编码（自动兼容十进制str/Verilog编码）
        parsed_codes = {code: self.parse_verilog_code(code.value) for code in values}
        values=list(values)
        # 原有两两计算逻辑
        for i in range(len(values)):
            code1 = values[i]
            bin1 = parsed_codes[code1]
            for j in range(i + 1, len(values)):
                code2 = values[j]
                bin2 = parsed_codes[code2]
                dist = self.calc_hamming_distance(bin1, bin2)
                violation = "✅ 违规" if dist == 1 else "❌ 合规"
                log_print(f"{code1.value} ↔ {code2.value}：汉明距离={dist} {violation}")

    def checkCobineloop(self):
        visited = {node: 0 for node in self.dfg}
        cycles = []  # 存储所有环的路径

        def dfs(current_node, path):
            if visited[current_node] == 1:
                # 找到环：从当前节点在path中的位置到末尾，就是闭环路径
                cycle_start_idx = path.index(current_node)
                cycle_path = path[cycle_start_idx:] + [current_node]
                cycles.append(cycle_path)
                return True
            if visited[current_node] == 2:
                return False
            # 标记为访问中
            visited[current_node] = 1
            path.append(current_node)
            # 遍历当前节点的所有依赖（有向边）
            for neighbor in self.dfg[current_node]:
                for alway in self.defAlways[neighbor]:
                    if isinstance(alway,Always):
                        for sen in alway.sens_list.list:
                            if sen.type=="all":
                                if dfs(neighbor.name, path):
                                    continue
            # 标记为已访问
            visited[current_node] = 2
            path.pop()
            return False
        # 对每个节点执行DFS
        for node in self.dfg:
            if visited[node] == 0:
                dfs(node, [])

        if len(cycles)>0:
            for cycle in cycles:
                log_print(str(cycle))

class CaseDef:
    def __init__(self, case):
        self.case = case
        self.defStatementMap = {}

    def buildCaseDef(self):
        if isinstance(self.case.statement, Block):
            self.buildBlock(self.case.statement)
        return

    def buildSubStitution(self, substitution):
        list = []
        if substitution.left.var in self.defStatementMap.keys():
            list = self.defStatementMap[substitution.left.var]
        list.append(substitution)
        self.defStatementMap[substitution.left.var] = list

    def buildBlock(self, block):
        for item in block.statements:
            if isinstance(item, Substitution):
                self.buildSubStitution(item)
            elif isinstance(item, IfStatement):
                self.buildIfStatement(item)

    def buildIfStatement(self, ifStatement):
        for statement in ifStatement.children():
            if isinstance(statement, Block):
                self.buildBlock(statement)
            if isinstance(statement, Substitution):
                self.buildSubStitution(statement)
            if isinstance(statement,IfStatement):
                self.buildIfStatement(statement)
        return
