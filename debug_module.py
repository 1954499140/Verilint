import sys
sys.path.insert(0, 'final_lint')
from pyverilog.vparser.parser import parse
from pyverilog.vparser.ast import ModuleDef
ast, _ = parse(['example/axi/fifo.v'])

for node in ast.children():
    if isinstance(node, ModuleDef):
        print(f'Module: {node.name}')
        print(f'Number of items: {len(node.items)}')
        for i, item in enumerate(node.items):
            print(f'  {i}: {type(item).__name__} at line {getattr(item, "lineno", 0)}')
