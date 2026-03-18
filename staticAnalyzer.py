# 只有在出现内存溢出的情况下才会爆错

import semanticAnalyzer
from pyverilog.vparser.ast import *
from semanticAnalyzer import *

class StaticConsistencyChecker:
    def __init__(self, semanticAnalyzer):
        self.modules = semanticAnalyzer.modules
        self.stmts = semanticAnalyzer.stmts
        self.decls = semanticAnalyzer.decl
        self.always = semanticAnalyzer.always
        self.isError = False
        self.oriinstanceMaps = semanticAnalyzer.oriinstanceMap
        self.pointers=semanticAnalyzer.pointers
        self.pointerMap=semanticAnalyzer.pointerMap
        self.instancesList=semanticAnalyzer.instances
        self.moduleBranch=semanticAnalyzer.moduleBranch
        self.DefAlway=semanticAnalyzer.DefAlways
        self.ErrorList = []
        self.alwaysRval = {}

    def MulDriveCheck(self):
        for key, values in self.DefAlway.items():
            if len(values) >1:
                log_print("存在多驱动")
        return
    def ConsistencyChecker(self):
        for module in self.modules:
            for item in module.children():
                if isinstance(item, InstanceList):
                    self.PortconnectionChecker(module, item)
                    self.InstancesBranchChecker(module,item)
                    self.checkInstanceGlitch(module,item)
            self.checkReset(module)
            self.checkSens(module)
        for pointer in self.pointers:
            self.pointerChecker(pointer)
        self.MulDriveCheck()
        return

    def InstancesBranchChecker(self,curmodule,instances):
        for instance in instances.children():
            if isinstance(instance,Instance):
                if instance not in self.oriinstanceMaps: continue
                originmodule = self.oriinstanceMaps[instance]
                if originmodule not in self.moduleBranch:continue
                branchs = self.moduleBranch[originmodule]
                branchlist=[]
                for branch in branchs:
                    branchlist.append(branch.name)
                for portArg in instance.portlist:
                    if self.isConstant(portArg):
                        if portArg.portname in branchlist:
                            log_print("传入的参数会影响状态，实例化后的模块状态缺失,在第"+str(portArg.lineno)+"行")
                return
        return
    def PortconnectionChecker(self, curmodule, instances):
        for instance in instances.children():
            if isinstance(instance,Instance):
                orimodule = self.oriinstanceMaps[instance]
                self.PortConnectService(instance, orimodule, curmodule)  # orimodule是原来的,curmodule是当前module
        return

    def PortConnectService(self, instance, orimodule, curmodule):
        if len(instance.portlist)!=len(orimodule.portlist.ports):
            log_print("参数数目不匹配，在第"+str(instance.lineno)+"行")
        portlist=orimodule.portlist.ports
        paramno=0
        for param in instance.portlist:
            paramDef = ParamDef(param, orimodule, curmodule)
            paramCur = paramDef.findDef(curmodule)
            Oriparam = portlist[paramno]
            curwidth=self.getParamWidth(paramCur)
            oriwidth=self.getParamWidth(Oriparam)
            if not self.compareWidth(curwidth,oriwidth):
                log_print("参数宽度不匹配，在第"+str(instance.lineno)+"行")
                return
            paramno=paramno+1
        pass

    def compareWidth(self,width1,width2):
        if isinstance(width1,Constant):
            if isinstance(width2,Constant):
                if width2==width1:
                    return True
                return False
            return False
        if width1 ==None and width2==None:
            return True
        if width1!=width2:
            return False
        for key1,key2 in zip(width1.children(),width2.children()):
            if not key1==key2:
                return False
        return True

    def getParamWidth(self, param):
        if isinstance(param,Decl):
            for item in param.children():
                if isinstance(item,Variable):
                    return item.width
        elif isinstance(param,Ioport):
            return param.first.width
        elif isinstance(param,Constant):
            return param

    def pointerChecker(self, pointer):
        pointerDef = PointerDef(pointer,None,None)
        decl = pointerDef.findDef(self.pointerMap[pointer])
        pointerDef.checkerOverFlow(decl)
        pass

    def isConstant(self, portArg):
        for item in portArg.children():
            if isinstance(item,IntConst):
                return True
        return False

    def checkInstanceGlitch(self,curmodule,instancelist):
        curportlist = curmodule.portlist
        ports=[]
        for curport in curportlist.ports:
            if isinstance(curport,Ioport):
                if isinstance(curport.first,Input):
                    ports.append(curport.first.name)
                    continue
                elif isinstance(curport.second,Input):
                    ports.append(curport.second.name)
                    continue
        for instance in instancelist.instances:
            portlist = instance.portlist
            for port in portlist:
                if isinstance(port.argname,Identifier):
                    if port.argname.name in ports:
                        log_print("实例初始化存在毛刺，在第"+str(instance.lineno)+"行")
        pass

    def checkReset(self,module):
        resetAlways=[]
        AlwaysIfStatement=[]
        for item in module.children():
            if isinstance(item,Always):
                # senslist=item.sens_list
                for block in item.children():
                    if isinstance(block,Block):
                        if self.checkBlock(block):
                            resetAlways.append(item)
                        for item in block.children():
                            if isinstance(item,IfStatement):
                                AlwaysIfStatement.append(item)
                                break
        self.checkResetType(resetAlways)
        self.checkResetCond(AlwaysIfStatement)
        pass

    def checkBlock(self, block):
        for item in block.children():
            if isinstance(item,IfStatement):
                if isinstance(item.true_statement,Substitution):
                    if not self.isSubInit(item.true_statement):
                        return False
                elif isinstance(item.true_statement,Block):
                    self.checkBlock(item.true_statement)
                else:
                    return False
            elif isinstance(item,Substitution):
                if not self.isSubInit(item):
                    return False
        return True

    def isSubInit(self,substitution):
        right = substitution.right
        if isinstance(right,Constant):
            return True
        elif isinstance(right,Rvalue):
            if isinstance(right.var,Constant):
                return True
        return False

    def checkResetType(self, resetAlways):
        sens = set()
        first=True
        for alway in resetAlways:
            if first:
                for sen in alway.sens_list.list:
                    sens.add(sen)
                first=False
                continue
            flag=True
            for sen in alway.sens_list.list:
                if sen in sens:
                    flag=False
                    continue
                if not flag:
                    if sen not in sens:
                        log_print("敏感信号不一致，在第" + str(alway.lineno) + "行")
            if len(sens) !=len(alway.sens_list.list) and not flag:
                log_print("敏感信号不一致，在第" + str(alway.lineno) + "行")

    def checkResetCond(self, AlwaysIfStatement):
        firstcond=None
        first = True
        for ifStatement in AlwaysIfStatement:
            if first:
                firstcond=ifStatement.cond
                first=False
                continue
            if not self.judgeCond(firstcond,ifStatement.cond):
                log_print("复位条件不一致，在第"+str(ifStatement.lineno)+"行")
        return
    def judgeCond(self, firstcond, cond):
        if str(firstcond) == str(cond):
             return True
        else:
            return False

    def checkSens(self, module):
        for item in module.children():
            if isinstance(item,Always):
                self.alwaysRval[item]=[]
                if self.isConbineAlways(item):
                    sensSet=set()
                    for sens in item.sens_list.list:
                        sensSet.add(sens.sig)
                    if sensSet !=set(self.alwaysRval[item]):
                        log_print("组合逻辑敏感列表未包含所有输入信号，导致逻辑延迟 / 功能错误，在第"+str(item.lineno)+"行")

    def isConbineAlways(self, always):
        substitutions=self.getAlwaysSubstitution(always)
        if substitutions is None:
            return False
        for substitution in substitutions:
            if isinstance(substitution,BlockingSubstitution):
                continue
            else: return False
        return True

    def getAlwaysSubstitution(self, always):
        substitutions = []
        for item in always.children():
            if isinstance(item,Block):
                self.putIntoSubstitution(substitutions,item,always)
        return substitutions

    def putIntoSubstitution(self, substitutions, item,always):
        for child in item.children():
            if isinstance(child,Substitution):
                substitutions.append(child)
                for val in child.children():
                    if isinstance(val,Rvalue):
                        self.buildRvalue(val,self.alwaysRval[always])
            else:
                self.putIntoSubstitution(substitutions,child,always)
        pass

    def buildRvalue(self, val, rval):
        if isinstance(val, Variable):
           rval.append(val)
        elif isinstance(val, Identifier):
            rval.append(val)
        elif isinstance(val, IntConst):
            return
        elif isinstance(val, Concat):
            return
        elif isinstance(val, Cond):
            return
        elif isinstance(val.var, Identifier):
            rval.append(val.var)
        elif isinstance(val.var, IntConst):
            return
        elif isinstance(val.var, UnaryOperator):
            rval.append(val.var.right)
        elif isinstance(val.var, Operator):
            if isinstance(val.var, Cond):
                self.buildRvalue(val.var.true_value, rval)
                self.buildRvalue(val.var.false_value, rval)
            else:
                self.buildRvalue(val.var.right, rval)
                rval.append(val.var.left)
        return


class ParamDef:
    def __init__(self, param1, module, module1):
        self.param = param1
        self.orimodule = module
        self.curmodule = module1

    def findDef(self,module):
        decllist = []
        for item in module.children():
            if isinstance(item, Paramlist):
                for para in item.children():
                    decllist.append(para)
            elif isinstance(item, Portlist):
                for port in item.children():
                    decllist.append(port)
            elif isinstance(item, Decl):
                decllist.append(item)
        if isinstance(self.param.argname,Pointer):
            name = str(self.param.argname.var)
        else:
            name = str(self.param.argname)
        for decl in decllist:
            if isinstance(decl, Decl):
                if name == self.getDeclName(decl):
                    return decl
            elif isinstance(decl, Port):
                if name == str(decl.name):
                    return decl
            elif isinstance(decl, Parameter):
                if name == str(decl.name):
                    return decl
            elif isinstance(decl, Ioport):
                if name == self.getDeclName(decl):
                    return decl
    def getDeclName(self, decl):
        for attr in decl.children():
            return attr.name
class PointerDef:
    def __init__(self, param1, module, module1):
        self.pointer = param1
        self.orimodule = module
        self.curmodule = module1

    def findDef(self,module):
        decllist = []
        for item in module.children():
            if isinstance(item, Paramlist):
                for para in item.children():
                    decllist.append(para)
            elif isinstance(item, Portlist):
                for port in item.children():
                    decllist.append(port)
            elif isinstance(item, Decl):
                decllist.append(item)
        for decl in decllist:
            if isinstance(decl, Decl):
                if str(self.pointer.var) == self.getDeclName(decl):
                    return decl
            elif isinstance(decl, Port):
                if str(self.pointer.var) == str(decl.name):
                    return decl
            elif isinstance(decl, Parameter):
                if str(self.pointer.var) == str(decl.name):
                    return decl
            elif isinstance(decl, Ioport):
                if str(self.pointer.var) == self.getDeclName(decl):
                    return decl
    def getDeclName(self, decl):
        for attr in decl.children():
            return attr.name

    def checkerOverFlow(self, decl):
        dimension=None
        if isinstance(decl,Decl):
            for item in decl.children():
                if isinstance(item,Variable):
                    dimension = item.dimensions
        if dimension is None:return
        for length in dimension.lengths:
            msb=None
            lsb=None
            if isinstance(length.msb,IntConst):
                msb=length.msb
            else: return
            if isinstance(length.lsb,IntConst):
                lsb=length.lsb
            else: return
            if isinstance(self.pointer.ptr,IntConst) :
                if int(self.pointer.ptr.value)>=int(lsb.value) or int(self.pointer.ptr.value)<=int(msb.value):
                    log_print("数组存在溢出情况,在第"+str(self.pointer.lineno)+"行")
            if isinstance(self.pointer.ptr,Uminus) :
                number = 0-int(self.pointer.ptr.right.value)
                if number>=int(lsb.value) or number<=int(msb.value):
                    log_print("数组存在溢出情况,在第"+str(self.pointer.lineno)+"行")
        pass


