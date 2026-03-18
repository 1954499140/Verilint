#安全性检查（可能会做）
import semanticAnalyzer
from pyverilog.vparser.ast import *


class SecurityChecker:
    def __init__(self, semanticAnalyzer):
        self.ast = semanticAnalyzer.ast
        self.always = semanticAnalyzer.always
        self.isError = False
        self.ErrorList = []
class SecurityComputer:
    def __init__(self):
        return