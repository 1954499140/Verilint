"""
复位信号极性检查测试
检测低有效复位信号是否正确使用
"""
import sys
sys.path.insert(0, 'final_lint')

from pyverilog.vparser.parser import parse
from pyverilog.vparser.ast import (
    ModuleDef, Always, IfStatement, Identifier, Sens,
    BlockingSubstitution, NonblockingSubstitution
)
from symbol_table_builder import SymbolTableBuilder

def check_reset_polarity(verilog_file):
    """检查复位信号极性"""
    ast, _ = parse([verilog_file])
    stb = SymbolTableBuilder()
    stb.build(ast)

    issues = []

    def find_always_nodes(node):
        """递归查找所有 always 节点"""
        always_nodes = []
        if isinstance(node, Always):
            always_nodes.append(node)
        if hasattr(node, '__dict__'):
            for attr_val in node.__dict__.values():
                if isinstance(attr_val, (list, tuple)):
                    for item in attr_val:
                        always_nodes.extend(find_always_nodes(item))
                elif attr_val is not None and hasattr(attr_val, '__dict__'):
                    always_nodes.extend(find_always_nodes(attr_val))
        return always_nodes

    def check_always_reset(always_node):
        """检查 always 块中的复位信号"""
        if not hasattr(always_node, 'sens_list') or not always_node.sens_list:
            return

        # 收集敏感列表中的复位信号
        reset_signals = {}
        if hasattr(always_node.sens_list, 'list'):
            for sens in always_node.sens_list.list:
                if hasattr(sens, 'type') and sens.type in ('posedge', 'negedge'):
                    if hasattr(sens, 'sig') and isinstance(sens.sig, Identifier):
                        sig_name = sens.sig.name
                        reset_signals[sig_name] = sens.type

        if not reset_signals:
            return

        print(f"  Found reset signals: {reset_signals}")

        # 检查 if 语句中的复位条件
        if hasattr(always_node, 'statement') and always_node.statement:
            check_if_condition(always_node.statement, reset_signals, issues)

    def check_if_condition(stmt, reset_signals, issues):
        """递归检查 if 条件"""
        if stmt is None:
            return

        if isinstance(stmt, IfStatement):
            cond = stmt.cond

            # 检查条件是否是复位信号
            if isinstance(cond, Identifier) and cond.name in reset_signals:
                reset_name = cond.name
                edge_type = reset_signals[reset_name]

                # 如果是低有效复位（negedge），但条件是 if (rst) 而不是 if (!rst)
                if edge_type == 'negedge':
                    issues.append({
                        'lineno': stmt.lineno,
                        'message': f"Reset polarity mismatch: '{reset_name}' is active-low (negedge) but checked with 'if ({reset_name})'",
                        'suggestion': f"Change 'if ({reset_name})' to 'if (!{reset_name})' for active-low reset"
                    })

            # 递归检查分支
            if hasattr(stmt, 'true_statement') and stmt.true_statement:
                check_if_condition(stmt.true_statement, reset_signals, issues)
            if hasattr(stmt, 'false_statement') and stmt.false_statement:
                check_if_condition(stmt.false_statement, reset_signals, issues)

        # 递归处理块语句
        if hasattr(stmt, 'statements'):
            for s in stmt.statements:
                check_if_condition(s, reset_signals, issues)

        # 递归处理 case 语句
        if hasattr(stmt, 'caselist'):
            for case in stmt.caselist:
                if case.statement:
                    check_if_condition(case.statement, reset_signals, issues)

    # 遍历所有模块
    for node in ast.children():
        if isinstance(node, ModuleDef):
            print(f"\nModule: {node.name}")
            always_nodes = find_always_nodes(node)
            print(f"  Found {len(always_nodes)} always blocks")
            for always in always_nodes:
                check_always_reset(always)

    return issues

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python test_reset_check.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]
    issues = check_reset_polarity(verilog_file)

    print("\n" + "="*70)
    print("Reset Polarity Check Results")
    print("="*70)

    if issues:
        print(f"\nFound {len(issues)} reset polarity issues:")
        for issue in issues:
            print(f"\n  Line {issue['lineno']}:")
            print(f"    {issue['message']}")
            print(f"    Suggestion: {issue['suggestion']}")
    else:
        print("\nNo reset polarity issues found")
