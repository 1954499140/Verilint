from pyverilog.vparser.parser import parse
from semanticAnalyzer import *
from staticAnalyzer import  *
from pyverilog.vparser.ast import *
from weakref import WeakKeyDictionary
from  DFGChecker import *
# 解析Verilog文件
ast, directives = parse(['example\\adf4159-main\\tb\control_interface_tb.v'])
description = ast.description
# 显示AST结构
ast.show()
# def clear_file(file_path: str):
#     """清空文件内容（通用方法）"""
#     try:
#         # 'w' 模式会截断文件（清空内容），encoding 确保兼容中文
#         with open(file_path, 'w', encoding='utf-8') as f:
#             f.write('')  # 写入空字符串（等同于清空）
#         print(f"✅ 文件 {file_path} 已清空")
#     except Exception as e:
#         print(f"❌ 清空文件失败：{str(e)}")
# clear_file("dma_log.txt")
# semanticAnalyzer = SemanticAnalyzer()
# semanticAnalyzer.analyzer(ast)
# staticChecker = StaticConsistencyChecker(semanticAnalyzer)
# staticChecker.ConsistencyChecker()

# checker=DFGChecker(semanticAnalyzer)
# checker.check()
# GLOBAL_ERRORS.print_all()
# # if not checker.isError:
# #     print("目前没有出现爆错")
# # for key,values in semanticAnalyzer.dfg.items():
# #     print(key)
# #     print("下面是val:")
# #     for val in values:
# #         print(val.name)
# #     print("-----------------------------------------------")