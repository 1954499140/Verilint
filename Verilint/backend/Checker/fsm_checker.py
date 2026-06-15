"""
FSM Checker - Finite State Machine Analyzer

Detects:
1. Dead states (states that once entered cannot be left)
2. Unreachable states (states that cannot be reached from initial state)
3. One-hot encoding violations
4. Incomplete case coverage
5. Missing default case
"""

from typing import List, Set, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyverilog.vparser.ast import (
    ModuleDef, Always, CaseStatement, Case, Block, IfStatement,
    Identifier, IntConst, Parameter, Localparam, NonblockingSubstitution,
    BlockingSubstitution, Lvalue, Rvalue, Eq,
    Partselect, Concat, Pointer, UnaryOperator, Land, Lor,
    Reg, Wire, Decl, GenerateStatement, Unot
)
from pyverilog.vparser.parser import parse
from symbol_table_builder import SymbolTableBuilder
from dfg_builder import DFGBuilder


def find_next_state_var(state_var: str, module) -> Optional[str]:
    """
    Find the next-state variable for a given state register by analyzing DFG.
    For two-stage FSMs, the next state is stored in a separate signal (e.g., state_new, state_next)
    before being assigned to the state register.

    Args:
        state_var: The state register variable name (e.g., 'sha256_ctrl_reg')
        module: The ModuleDef AST node

    Returns:
        The next-state variable name if found, None otherwise
    """
    # Look through all always blocks to find which signal drives the state register
    for item in module.items:
        if isinstance(item, Always):
            next_var = _find_driver_in_always(item, state_var)
            if next_var:
                return next_var
    return None


def _find_driver_in_always(always: Always, target_var: str) -> Optional[str]:
    """Find the signal that drives target_var in an always block"""
    if not always.statement:
        return None

    # Look for assignments like: state_reg <= next_var
    # or: if (we) state_reg <= next_var
    return _search_driver(always.statement, target_var)


def _search_driver(stmt, target_var: str) -> Optional[str]:
    """Recursively search for the driver of target_var"""
    if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
        # Check if this is an assignment to target_var
        lval = stmt.left
        if isinstance(lval, Lvalue):
            lval = lval.var

        if isinstance(lval, Identifier) and lval.name == target_var:
            # Found assignment to target_var, get the source
            rval = stmt.right
            if isinstance(rval, Rvalue):
                rval = rval.var
            if isinstance(rval, Identifier):
                return rval.name
            # If it's a conditional assignment, look for the signal in conditions
            return None

    elif isinstance(stmt, IfStatement):
        # Check both branches
        result = _search_driver(stmt.true_statement, target_var)
        if result:
            return result
        if stmt.false_statement:
            result = _search_driver(stmt.false_statement, target_var)
            if result:
                return result

    elif isinstance(stmt, Block):
        for s in stmt.statements:
            result = _search_driver(s, target_var)
            if result:
                return result

    return None


class FSMIssueType(Enum):
    """FSM issue types"""
    DEAD_STATE = "dead_state"              # State that cannot be left
    UNREACHABLE_STATE = "unreachable"      # State that cannot be reached
    INCOMPLETE_CASE = "incomplete_case"    # Case doesn't cover all states
    MISSING_DEFAULT = "missing_default"    # No default in case statement
    NOT_ONE_HOT = "not_one_hot"            # Not using one-hot encoding
    INVALID_TRANSITION = "invalid_transition"  # Transition to undefined state
    STATE_OVERFLOW = "state_overflow"      # State value exceeds encoding width


@dataclass
class FSMState:
    """Represents an FSM state"""
    name: str
    value: int
    lineno: int
    transitions: Dict[str, Tuple[str, int]] = field(default_factory=dict)  # condition -> (target_name, target_lineno)
    is_initial: bool = False


@dataclass
class FSMIssue:
    """FSM issue report"""
    issue_type: FSMIssueType
    state_name: str
    lineno: int
    description: str
    severity: str = "warning"  # error, warning, info


@dataclass
class FSMInfo:
    """FSM information extracted from module"""
    state_var: str = ""                    # State variable name
    states: Dict[str, FSMState] = field(default_factory=dict)
    initial_state: Optional[str] = None
    is_one_hot: bool = False
    state_width: int = 0
    has_default: bool = False
    case_lineno: int = 0


class FSMExtractor:
    """Extract FSM information from AST"""

    def __init__(self, ast, stb: SymbolTableBuilder):
        self.ast = ast
        self.stb = stb
        self.fsms: List[FSMInfo] = []
        self.module: Optional[ModuleDef] = None  # Current module being analyzed

    def extract(self) -> List[FSMInfo]:
        """Extract all FSMs from the AST"""
        self.fsms = []
        self._traverse_ast(self.ast)
        return self.fsms

    def _traverse_ast(self, node):
        """Traverse AST to find FSMs"""
        if isinstance(node, ModuleDef):
            self.module = node  # Store current module for DFG analysis
            fsm_info = self._extract_fsm_from_module(node)
            if fsm_info and fsm_info.states:
                self.fsms.append(fsm_info)
            self.module = None  # Clear after processing

        for child in self._get_children(node):
            self._traverse_ast(child)

    def _get_children(self, node) -> List[Any]:
        """Get child nodes"""
        children = []
        if hasattr(node, '__dict__'):
            for attr_name, attr_val in node.__dict__.items():
                # Handle both list and tuple (like definitions)
                if isinstance(attr_val, (list, tuple)):
                    children.extend(attr_val)
                elif attr_val is not None and hasattr(attr_val, '__dict__'):
                    children.append(attr_val)
        return children

    def _extract_fsm_from_module(self, module: ModuleDef) -> Optional[FSMInfo]:
        """Extract FSM information from a module"""
        fsm_info = FSMInfo()

        # Step 1: Find state parameters/localparams
        state_params = self._extract_state_params(module)

        # Step 2: Find always blocks with case statements for state transitions
        # Keep the FSM with the most transitions (state logic, not output logic)
        best_fsm = None
        best_transition_count = 0

        for item in module.items:
            if isinstance(item, Always):
                state_var, states, has_default, case_lineno = self._analyze_always_block(item, state_params)
                if state_var and states:
                    # Count total transitions
                    trans_count = sum(len(s.transitions) for s in states.values())
                    # Prefer FSMs with more transitions (state logic vs output logic)
                    if trans_count > best_transition_count:
                        best_transition_count = trans_count
                        best_fsm = (state_var, states, has_default, case_lineno)

        if best_fsm:
            fsm_info.state_var, fsm_info.states, fsm_info.has_default, fsm_info.case_lineno = best_fsm

        # Step 3: Determine initial state from reset logic
        self._find_initial_state(module, fsm_info)

        # Step 4: Check if using one-hot encoding
        fsm_info.is_one_hot = self._check_one_hot_encoding(fsm_info.states)

        return fsm_info if fsm_info.states else None

    def _extract_state_params(self, module: ModuleDef) -> Dict[str, int]:
        """Extract state parameter values"""
        state_values = {}

        for item in module.items:
            if isinstance(item, Decl):
                for decl_item in item.list:
                    if isinstance(decl_item, (Parameter, Localparam)):
                        param_name = decl_item.name
                        # Handle Rvalue wrapper
                        param_val = decl_item.value
                        if isinstance(param_val, Rvalue):
                            param_val = param_val.var
                        param_value = self._eval_const_expr(param_val)
                        if param_value is not None:
                            state_values[param_name] = param_value

        return state_values

    def _is_input_signal(self, var_name: str) -> bool:
        """Check if a variable is an input signal (not a state register)"""
        if not self.stb or not var_name:
            return False

        # Look up the symbol in symbol table
        symbol = self.stb.lookup(var_name)
        if symbol and hasattr(symbol, 'is_input'):
            return symbol.is_input()

        # If not found in symbol table, check if it's assigned in always block
        # State registers are assigned in always blocks, inputs are not
        return False

    def _is_assigned_in_always(self, var_name: str, always: Always) -> bool:
        """Check if a variable is assigned (written to) in an always block"""
        if not always.statement:
            return False

        def find_assignment(stmt, target_var):
            if stmt is None:
                return False

            if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
                lval = stmt.left
                if isinstance(lval, Lvalue):
                    lval = lval.var
                if isinstance(lval, Identifier) and lval.name == target_var:
                    return True

            elif isinstance(stmt, IfStatement):
                if find_assignment(stmt.true_statement, target_var):
                    return True
                if find_assignment(stmt.false_statement, target_var):
                    return True

            elif isinstance(stmt, Block):
                for s in stmt.statements:
                    if find_assignment(s, target_var):
                        return True

            elif isinstance(stmt, CaseStatement):
                for case in stmt.caselist:
                    if find_assignment(case.statement, target_var):
                        return True

            return False

        return find_assignment(always.statement, var_name)

    def _analyze_always_block(self, always: Always, state_params: Dict[str, int]) -> Tuple[str, Dict[str, FSMState], bool, int]:
        """Analyze an always block to find state machine
        Returns: (state_var, states_dict, has_default, case_lineno)

        A valid FSM must have:
        1. Case condition variable is a state register (assigned in the always block)
        2. Not just an input signal decoder (case based on input)
        """
        state_var = ""
        states = {}
        has_default = False
        case_lineno = 0

        if not always.statement:
            return state_var, states, has_default, case_lineno

        # Find case statement in the always block
        case_stmt = self._find_case_statement(always.statement)
        if not case_stmt:
            return state_var, states, has_default, case_lineno

        case_lineno = case_stmt.lineno

        # Try to get state variable from case condition
        case_cond_var = self._get_state_variable(case_stmt.comp)

        # CRITICAL: Check if case condition variable is a state register
        # A state register must be assigned in this always block (feedback loop)
        if case_cond_var:
            if not self._is_assigned_in_always(case_cond_var, always):
                # Case is based on an input signal, not a state register
                # This is a decoder, not an FSM - skip it
                return "", states, has_default, case_lineno
            state_var = case_cond_var

        # If case condition is not directly a state variable, check assignments in case branches
        # This handles FSMs where case(input) is used to set state (but we already filtered those above)
        if not state_var:
            potential_state_var = self._find_state_var_from_assignments(case_stmt)
            # Only accept if the variable is assigned in this always block (state register)
            if potential_state_var and self._is_assigned_in_always(potential_state_var, always):
                state_var = potential_state_var
            else:
                return "", states, has_default, case_lineno

        if not state_var:
            return state_var, states, has_default, case_lineno

        # Find the next-state variable for this state register (for two-stage FSMs)
        # This is the signal that holds the next state before it's assigned to the register
        next_state_var = find_next_state_var(state_var, self.module) if self.module else None

        # Analyze each case
        for case in case_stmt.caselist:
            if case.cond is None:
                # This is a default case
                has_default = True
            else:
                state_info = self._analyze_case(case, state_params, state_var, next_state_var)
                if state_info:
                    state_name, fsm_state = state_info
                    states[state_name] = fsm_state

        return state_var, states, has_default, case_lineno

    def _find_case_statement(self, stmt) -> Optional[CaseStatement]:
        """Find case statement in the statement tree"""
        if isinstance(stmt, CaseStatement):
            return stmt

        if isinstance(stmt, Block) and stmt.statements:
            for s in stmt.statements:
                result = self._find_case_statement(s)
                if result:
                    return result

        if isinstance(stmt, IfStatement):
            result = self._find_case_statement(stmt.true_statement)
            if result:
                return result
            if stmt.false_statement:
                result = self._find_case_statement(stmt.false_statement)
                if result:
                    return result

        return None

    def _get_state_variable(self, comp) -> str:
        """Get state variable name from case comparison expression"""
        if isinstance(comp, Identifier):
            return comp.name
        if isinstance(comp, Partselect) and isinstance(comp.var, Identifier):
            return comp.var.name
        return ""

    def _find_state_var_from_assignments(self, case_stmt: CaseStatement) -> str:
        """Find state variable by looking at assignments in case branches
        Prioritizes FSM-like variable names (state, next_state, current_state, etc.)
        """
        from collections import Counter

        assignment_targets = []
        fsm_like_patterns = ['state', 'next_', 'current_', 'ns', 'cs']  # Common FSM naming patterns

        for case in case_stmt.caselist:
            if case.statement:
                targets = self._find_assignment_targets(case.statement)
                assignment_targets.extend(targets)

        if not assignment_targets:
            return ""

        # First, look for FSM-like variable names
        counter = Counter(assignment_targets)
        for target, count in counter.most_common():
            lower_target = target.lower()
            if any(pattern in lower_target for pattern in fsm_like_patterns):
                return target

        # If no FSM-like name found, return the most common target
        # but only if it's assigned in multiple branches (FSM pattern)
        most_common = counter.most_common(1)[0]
        if most_common[1] >= 2:  # At least 2 branches assign to this variable
            return most_common[0]

        return ""

    def _find_assignment_targets(self, stmt) -> List[str]:
        """Find all assignment targets in a statement"""
        targets = []

        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var
            if isinstance(lval, Identifier):
                targets.append(lval.name)

        elif isinstance(stmt, Block):
            for s in stmt.statements:
                targets.extend(self._find_assignment_targets(s))

        elif isinstance(stmt, IfStatement):
            targets.extend(self._find_assignment_targets(stmt.true_statement))
            if stmt.false_statement:
                targets.extend(self._find_assignment_targets(stmt.false_statement))

        return targets

    def _analyze_case(self, case: Case, state_params: Dict[str, int], state_var: str = "", next_state_var: str = "") -> Optional[Tuple[str, FSMState]]:
        """Analyze a single case to extract state information"""
        # Get state name from condition
        state_name = self._get_state_name(case.cond, state_params)
        if not state_name:
            return None

        # Get state value from condition or state_params
        state_value = state_params.get(state_name)
        if state_value is None:
            # Try to extract from condition (for IntConst like 3'h3)
            if isinstance(case.cond, tuple) and len(case.cond) > 0:
                cond = case.cond[0]
            else:
                cond = case.cond
            if isinstance(cond, IntConst):
                state_value = self._eval_const_expr(cond)
            if state_value is None:
                # Generate unique value based on existing states
                state_value = len(state_params)

        fsm_state = FSMState(
            name=state_name,
            value=state_value,
            lineno=case.lineno
        )

        # Analyze transitions from this state
        # For two-stage FSMs, use next_state_var to find transitions
        # For one-stage FSMs, use state_var directly
        target_var = next_state_var if next_state_var else state_var
        if case.statement:
            transitions = self._extract_transitions(case.statement, target_var)
            fsm_state.transitions = transitions

        return state_name, fsm_state

    def _get_state_name(self, cond, state_params: Dict[str, int]) -> str:
        """Get state name from condition (cond can be a tuple of conditions)"""
        # Handle tuple of conditions - use the first one
        if isinstance(cond, tuple):
            if len(cond) == 0:
                return ""
            cond = cond[0]

        if isinstance(cond, Identifier):
            return cond.name
        if isinstance(cond, IntConst):
            # Try to find parameter with this value
            val = self._eval_const_expr(cond)
            if val is not None:
                for name, value in state_params.items():
                    if value == val:
                        return name
                return f"STATE_{val}"
        return ""

    def _extract_transitions(self, stmt, state_var: str = "") -> Dict[str, Tuple[str, int]]:
        """Extract state transitions from statement
        Recognizes common FSM patterns like:
        - case(current_state): next_state = ...
        - case(state): state = ...
        """
        transitions = {}

        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            # Check if this is an assignment to the state variable
            # or to a next_state variable (identified via DFG analysis)
            lval = stmt.left
            if isinstance(lval, Lvalue):
                lval = lval.var

            is_state_assignment = False
            if isinstance(lval, Identifier):
                # If state_var is provided, only match assignments to that specific variable
                # This is determined via DFG analysis (find_next_state_var)
                if state_var and lval.name == state_var:
                    is_state_assignment = True
                # If no state_var provided, fall back to naming patterns (for backward compatibility)
                elif not state_var:
                    var_name = lval.name.lower()
                    fsm_patterns = ['state', 'next_', 'ns', 'cs']
                    if any(pattern in var_name for pattern in fsm_patterns):
                        is_state_assignment = True

            if is_state_assignment:
                target = self._get_assignment_target(stmt)
                if target:
                    transitions["default"] = (target, stmt.lineno)

        elif isinstance(stmt, IfStatement):
            # Conditional transitions
            self._extract_if_transitions(stmt, transitions, state_var)

        elif isinstance(stmt, Block):
            for s in stmt.statements:
                sub_trans = self._extract_transitions(s, state_var)
                transitions.update(sub_trans)

        return transitions

    def _extract_if_transitions(self, if_stmt: IfStatement, transitions: Dict, state_var: str = ""):
        """Extract transitions from if statement"""
        cond_str = self._condition_to_str(if_stmt.cond)

        # True branch
        if if_stmt.true_statement:
            true_trans = self._extract_transitions(if_stmt.true_statement, state_var)
            for key, (target, lineno) in true_trans.items():
                transitions[f"{cond_str}"] = (target, lineno)

        # False branch
        if if_stmt.false_statement:
            false_cond = f"!({cond_str})"
            false_trans = self._extract_transitions(if_stmt.false_statement, state_var)
            for key, (target, lineno) in false_trans.items():
                transitions[false_cond] = (target, lineno)

    def _get_assignment_target(self, subst) -> str:
        """Get target state from assignment"""
        rval = subst.right
        if isinstance(rval, Rvalue):
            rval = rval.var

        if isinstance(rval, Identifier):
            return rval.name
        if isinstance(rval, IntConst):
            val = self._eval_const_expr(rval)
            return f"STATE_{val}"

        return ""

    def _condition_to_str(self, cond) -> str:
        """Convert condition to string representation"""
        if isinstance(cond, Identifier):
            return cond.name
        if isinstance(cond, IntConst):
            return str(cond.value)
        if isinstance(cond, Eq):
            left = self._condition_to_str(cond.left)
            right = self._condition_to_str(cond.right)
            return f"{left} == {right}"
        if isinstance(cond, (Land, Lor)):
            left = self._condition_to_str(cond.left)
            right = self._condition_to_str(cond.right)
            op = "&&" if isinstance(cond, Land) else "||"
            return f"({left} {op} {right})"
        if isinstance(cond, Unot):
            return f"!{self._condition_to_str(cond.next)}"
        return str(cond)

    def _find_initial_state(self, module: ModuleDef, fsm_info: FSMInfo):
        """Find initial state from reset logic"""
        for item in module.items:
            if isinstance(item, Always):
                init_state = self._find_reset_state(item)
                if init_state:
                    fsm_info.initial_state = init_state
                    if init_state in fsm_info.states:
                        fsm_info.states[init_state].is_initial = True
                    break

    def _find_reset_state(self, always: Always) -> str:
        """Find initial state from reset logic in always block"""
        if not always.statement:
            return ""

        # Look for if reset statement
        reset_if = self._find_reset_if(always.statement)
        if reset_if and reset_if.true_statement:
            # Extract state assignment from reset branch
            init_state = self._extract_initial_state_from_stmt(reset_if.true_statement)
            return init_state

        return ""

    def _find_reset_if(self, stmt) -> Optional[IfStatement]:
        """Find if statement that checks reset"""
        if isinstance(stmt, IfStatement):
            # Check if condition involves reset (negedge rst_n or posedge reset)
            cond_str = str(stmt.cond).lower()
            if any(r in cond_str for r in ['rst', 'reset']):
                return stmt

        if isinstance(stmt, Block):
            for s in stmt.statements:
                result = self._find_reset_if(s)
                if result:
                    return result

        return None

    def _extract_initial_state_from_stmt(self, stmt) -> str:
        """Extract initial state value from statement"""
        if isinstance(stmt, (NonblockingSubstitution, BlockingSubstitution)):
            rval = stmt.right
            if isinstance(rval, Rvalue):
                rval = rval.var
            if isinstance(rval, Identifier):
                return rval.name
            if isinstance(rval, IntConst):
                return f"STATE_{self._eval_const_expr(rval)}"

        if isinstance(stmt, Block):
            for s in stmt.statements:
                result = self._extract_initial_state_from_stmt(s)
                if result:
                    return result

        return ""

    def _check_one_hot_encoding(self, states: Dict[str, FSMState]) -> bool:
        """Check if states use one-hot encoding"""
        if not states:
            return False

        values = [s.value for s in states.values()]

        # One-hot: exactly one bit is set
        for val in values:
            if val == 0:
                return False  # One-hot should not have state 0
            if bin(val).count('1') != 1:
                return False

        return True

    def _eval_const_expr(self, expr) -> Optional[int]:
        """Evaluate constant expression"""
        if isinstance(expr, IntConst):
            try:
                val = str(expr.value)
                # Handle sized literals like "3'h3", "8'hFF", "16'd255"
                # Format: <size>'<base><value>
                if "'" in val:
                    parts = val.split("'")
                    if len(parts) >= 2:
                        # parts[0] is size (e.g., "3", "8", "16" or empty)
                        # parts[1] is base+value (e.g., "h3", "hFF", "d255")
                        val_part = parts[1]
                        if val_part:
                            base_char = val_part[0].lower()
                            value_str = val_part[1:]
                            if base_char == 'h':
                                return int(value_str, 16)
                            elif base_char == 'b':
                                return int(value_str, 2)
                            elif base_char == 'd':
                                return int(value_str, 10)
                            elif base_char == 'o':
                                return int(value_str, 8)
                # Handle unsized literals like "'h3", "'b10"
                if val.startswith("'h") or val.startswith("'H"):
                    return int(val[2:], 16)
                elif val.startswith("'b") or val.startswith("'B"):
                    return int(val[2:], 2)
                elif val.startswith("'d") or val.startswith("'D"):
                    return int(val[2:], 10)
                else:
                    return int(val, 0)
            except (ValueError, TypeError):
                return None
        return None


class FSMChecker:
    """
    FSM Checker - Checks for various FSM issues
    """

    def __init__(self, ast, stb: SymbolTableBuilder):
        self.ast = ast
        self.stb = stb
        self.extractor = FSMExtractor(ast, stb)
        self.issues: List[FSMIssue] = []

    def check(self) -> List[FSMIssue]:
        """Run all FSM checks"""
        self.issues = []

        # Extract FSMs
        fsms = self.extractor.extract()

        for fsm in fsms:
            self._check_fsm(fsm)

        return self.issues

    def _check_fsm(self, fsm: FSMInfo):
        """Check a single FSM for issues"""
        # 1. Check for missing default
        if not fsm.has_default:
            self.issues.append(FSMIssue(
                issue_type=FSMIssueType.MISSING_DEFAULT,
                state_name="",
                lineno=fsm.case_lineno,
                description=f"FSM using state variable '{fsm.state_var}' is missing default case",
                severity="warning"
            ))

        # 2. Check for incomplete case coverage
        self._check_case_coverage(fsm)

        # 3. Check for dead states
        self._check_dead_states(fsm)

        # 4. Check for unreachable states
        self._check_unreachable_states(fsm)

        # 5. Check one-hot encoding
        if not fsm.is_one_hot and len(fsm.states) > 2:
            # Suggest one-hot for FSMs with more than 2 states
            self.issues.append(FSMIssue(
                issue_type=FSMIssueType.NOT_ONE_HOT,
                state_name="",
                lineno=0,
                description=f"FSM with {len(fsm.states)} states is not using one-hot encoding "
                           f"(values: {[s.value for s in fsm.states.values()]}). "
                           f"Consider using one-hot encoding for better performance",
                severity="info"
            ))

    def _check_case_coverage(self, fsm: FSMInfo):
        """Check if all possible state values are covered"""
        if not fsm.states:
            return

        # Calculate number of bits needed
        max_state_val = max(s.value for s in fsm.states.values())
        num_bits = max_state_val.bit_length()
        total_possible_states = 2 ** num_bits

        covered_states = set(s.value for s in fsm.states.values())

        if len(covered_states) < total_possible_states and not fsm.has_default:
            missing = total_possible_states - len(covered_states)
            self.issues.append(FSMIssue(
                issue_type=FSMIssueType.INCOMPLETE_CASE,
                state_name="",
                lineno=fsm.case_lineno,
                description=f"Incomplete case coverage: {len(covered_states)}/{total_possible_states} "
                           f"state values covered. {missing} values missing (no default).",
                severity="warning"
            ))

    def _check_dead_states(self, fsm: FSMInfo):
        """Check for dead states (states that cannot be left - all transitions point to itself)"""
        for state_name, state in fsm.states.items():
            # A state is dead if:
            # 1. It has transitions, AND
            # 2. ALL transitions point to itself

            if not state.transitions:
                # No transitions defined - check if there's a default
                # If no default, this might trap the FSM
                continue  # We'll handle this as incomplete coverage, not dead state

            # Check if ALL transitions point to itself
            all_self_loops = True
            for cond, (target, lineno) in state.transitions.items():
                # Check if target is the same state
                target_value = self._get_state_value(target, fsm)
                if target_value != state.value:
                    all_self_loops = False
                    break

            if all_self_loops:
                self.issues.append(FSMIssue(
                    issue_type=FSMIssueType.DEAD_STATE,
                    state_name=state_name,
                    lineno=state.lineno,
                    description=f"Dead state '{state_name}': once entered, cannot be left "
                               f"(all {len(state.transitions)} transition(s) point to itself)",
                    severity="error"
                ))

    def _check_unreachable_states(self, fsm: FSMInfo):
        """Check for unreachable states using BFS from initial state"""
        if not fsm.initial_state or fsm.initial_state not in fsm.states:
            return

        # BFS to find all reachable states
        reachable = set()
        queue = [fsm.initial_state]

        while queue:
            current = queue.pop(0)
            if current in reachable:
                continue
            reachable.add(current)

            if current in fsm.states:
                state = fsm.states[current]
                for cond, (target, lineno) in state.transitions.items():
                    if target not in reachable and target in fsm.states:
                        queue.append(target)

        # Find unreachable states
        all_states = set(fsm.states.keys())
        unreachable = all_states - reachable

        for state_name in unreachable:
            state = fsm.states[state_name]
            self.issues.append(FSMIssue(
                issue_type=FSMIssueType.UNREACHABLE_STATE,
                state_name=state_name,
                lineno=state.lineno,
                description=f"Unreachable state '{state_name}': cannot be reached from initial state '{fsm.initial_state}'",
                severity="warning"
            ))

    def _get_state_value(self, state_name: str, fsm: FSMInfo) -> int:
        """Get numeric value of a state"""
        if state_name in fsm.states:
            return fsm.states[state_name].value
        # Try to parse STATE_X format
        if state_name.startswith("STATE_"):
            try:
                return int(state_name[6:])
            except ValueError:
                pass
        return -1

    def print_report(self):
        """Print FSM check report"""
        print("\n" + "=" * 70)
        print("FSM Check Report")
        print("=" * 70)

        if not self.issues:
            print("No FSM issues found")
            return

        # Group by type
        dead_states = [i for i in self.issues if i.issue_type == FSMIssueType.DEAD_STATE]
        unreachable = [i for i in self.issues if i.issue_type == FSMIssueType.UNREACHABLE_STATE]
        missing_default = [i for i in self.issues if i.issue_type == FSMIssueType.MISSING_DEFAULT]
        incomplete = [i for i in self.issues if i.issue_type == FSMIssueType.INCOMPLETE_CASE]
        not_onehot = [i for i in self.issues if i.issue_type == FSMIssueType.NOT_ONE_HOT]

        if dead_states:
            print(f"\n[!] Dead States ({len(dead_states)}):")
            for issue in dead_states:
                print(f"  Line {issue.lineno:3d}: {issue.state_name:15s} - {issue.description}")

        if unreachable:
            print(f"\n[!] Unreachable States ({len(unreachable)}):")
            for issue in unreachable:
                print(f"  Line {issue.lineno:3d}: {issue.state_name:15s} - {issue.description}")

        if missing_default:
            print(f"\n[!] Missing Default ({len(missing_default)}):")
            for issue in missing_default:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if incomplete:
            print(f"\n[!] Incomplete Case Coverage ({len(incomplete)}):")
            for issue in incomplete:
                print(f"  Line {issue.lineno:3d}: {issue.description}")

        if not_onehot:
            print(f"\n[i] One-Hot Encoding Suggestions ({len(not_onehot)}):")
            for issue in not_onehot:
                print(f"  {issue.description}")

        print(f"\nTotal: {len(self.issues)} FSM issues")


def check_fsm(ast, stb: SymbolTableBuilder) -> List[FSMIssue]:
    """Convenience function for FSM checking"""
    checker = FSMChecker(ast, stb)
    issues = checker.check()
    return issues


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python fsm_checker.py <verilog_file>")
        sys.exit(1)

    verilog_file = sys.argv[1]

    # Parse
    ast, _ = parse([verilog_file])

    # Build symbol table
    stb = SymbolTableBuilder()
    stb.build(ast)

    # Check FSM
    checker = FSMChecker(ast, stb)
    issues = checker.check()
    checker.print_report()
