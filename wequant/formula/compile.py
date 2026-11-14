"""A formula compiled into one expression tree: variables inlined, constants folded and TDX names mapped onto
wequant.expr's fields and operators, every refusal located in the text (S4.1).

TDX's habits hold: a comparison is 1/0; a number where a condition belongs is true when it is not 0 (NOT(x = 0)); a
condition in arithmetic is its 1/0.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from wequant.expr import BOOL, NOT, OPERATORS, PERIOD_FIELDS, Codes, Expr, ExprError, Field, Op
from wequant.expr.lookback import fields as read_fields
from wequant.expr.vocab import CROSS_SECTIONAL
from wequant.formula.names import (
    DRAWING,
    FAMILY_FUNCTIONS,
    FIELD_NAMES,
    FUNCTIONS,
    FUTURE_NAMES,
    INDICATORS,
    INTERNAL,
    NO_DATA,
    PERIOD_FUNCTIONS,
    REFUSED_FIELDS,
    UNSUPPORTED,
    known,
)
from wequant.formula.syntax import Binary, Call, FormulaError, Name, Num, Span, Statement, Text, Unary, parse

MAX_TEXT = 20_000  # characters: a formula far longer than any written by hand is refused before it is read
WARNINGS = ("adjusted_level", "formula_shadows")  # the codes of Compiled.warnings
PERIODS = ("day", "week", "month")
CONDITION_ROLES = ("condition", "universe", "sell", "keep")
OPERATOR_NAMES = {"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", ">": "GT", ">=": "GE", "<": "LT", "<=": "LE"}
OPERATOR_NAMES |= {"=": "EQ", "AND": "AND", "OR": "OR"}
LEVELS = frozenset(f"adj_{name}" for name in ("open", "high", "low", "close", "preclose"))
SAME_LEVEL = frozenset("REF MA EMA SMA WMA HHV LLV MAX MIN IF VALUEWHEN ADD SUB".split())


@dataclass(frozen=True)
class Variable:
    """A named line, or an unnamed output (NONAME0, NONAME1, ... as TDX names them)."""

    name: str
    line: int
    kind: str  # num, bool or const
    value: float | None  # a constant's value
    output: bool
    used: bool
    expr: Expr | None  # its tree on the formula's bars; None for a constant


@dataclass(frozen=True)
class Compiled:
    """The formula's tree (under WEEK or MONTH for those periods), its output and variables, and its warnings."""

    expr: Expr
    output: str | None
    variables: tuple[Variable, ...]
    warnings: tuple[FormulaError, ...] = field(default=())
    direct: tuple[str, ...] = field(default=())  # the variables the output line names, in the order it names them


def compile_formula(text: str, period: str = "day", role: str | None = None) -> Compiled:
    """The formula's tree; FormulaError (or ExprError for a rule of the tree itself) located in the text."""
    if len(text) > MAX_TEXT:
        raise FormulaError("formula_too_long", f"the formula has {len(text)} characters, more than {MAX_TEXT}")
    statements = parse(text)
    return _Compiler(period).compile(statements, role)


class _Compiler:
    def __init__(self, period: str) -> None:
        self.period, self.values, self.used, self.later, self.refs = period, {}, set(), {}, []
        self.warnings: list[FormulaError] = []

    def compile(self, statements: list[Statement], role: str | None) -> Compiled:
        names, unnamed, named = [], 0, {}
        self.later = {s.name: s.span.line for s in reversed(statements) if s.name is not None}  # first definitions
        for statement in statements:
            name = statement.name
            if name is None:
                name, unnamed = f"NONAME{unnamed}", unnamed + 1
            elif name in self.values:
                raise FormulaError("formula_redefined", f"{name} is defined twice", statement.span, name)
            elif name in FIELD_NAMES:  # the user's formula names a line C: from there on C is that line
                message = f"{name} is a built-in name; after this line {name} means this variable"
                self.warnings.append(FormulaError("formula_shadows", message, statement.span, name))
            self.refs.clear()
            self.values[name] = self.value(statement.expr)
            named[name] = tuple(dict.fromkeys(self.refs))
            names.append((name, statement))
        outputs = [(name, s) for name, s in names if s.kind != "variable"]
        if not outputs:
            span = statements[-1].span if statements else None
            raise FormulaError("formula_no_output", "the formula has no output line (NAME: expr, or a bare expr)", span)
        output, statement = outputs[-1]
        expr = self.series(self.values[output], statement.span)
        if role in CONDITION_ROLES and expr.kind != BOOL:
            raise FormulaError(
                "formula_needs_bool", f"{output} is a number; a {role} needs a condition", statement.span, output
            )
        self.used.add(output)
        variables = tuple(self.variable(name, s) for name, s in names)
        wrapped = expr if self.period == "day" else self.op(self.period.upper(), statement.span, expr)
        return Compiled(wrapped, statement.name, variables, tuple(self.warnings), named[output])

    def variable(self, name: str, statement: Statement) -> Variable:
        """A line of the formula as compiled: a constant's value, or its tree."""
        value, output = self.values[name], statement.kind != "variable"
        if not isinstance(value, Expr):
            return Variable(name, statement.span.line, "const", value, output, name in self.used, None)
        return Variable(name, statement.span.line, value.kind, None, output, name in self.used, value)

    # ------------------------------------------------------------ values

    def value(self, node: object) -> Expr | float | str:
        """A node's tree, constant or string."""
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Text):
            return node.value
        if isinstance(node, Name):
            return self.name(node)
        if isinstance(node, Unary):
            return self.unary(node)
        if isinstance(node, Binary):
            return self.binary(node)
        return self.call(node)

    def name(self, node: Name) -> Expr | float:
        if node.name in self.values:
            self.used.add(node.name)
            self.refs.append(node.name)
            return self.values[node.name]
        if node.name in FIELD_NAMES:
            expr = FIELD_NAMES[node.name]
            self.period_field(expr, node.span, node.name)
            return expr
        if node.name in REFUSED_FIELDS:
            return self.located(lambda: Field(REFUSED_FIELDS[node.name]), node.span, node.name)
        raise self.unknown(node.name, node.span, called=False)

    def unary(self, node: Unary) -> Expr | float:
        operand = self.value(node.operand)
        if node.op == "-":
            return (
                -operand
                if isinstance(operand, float)
                else self.op("SUB", node.span, 0.0, self.series(operand, node.span))
            )
        return self.op("NOT", node.span, self.condition(operand, node.span))

    def binary(self, node: Binary) -> Expr | float:
        left, right = self.value(node.left), self.value(node.right)
        name = "NE" if node.op == "<>" else OPERATOR_NAMES[node.op]
        if name in ("AND", "OR"):
            return self.op(name, node.span, self.condition(left, node.span), self.condition(right, node.span))
        if isinstance(left, float) and isinstance(right, float):
            if name in ("ADD", "SUB", "MUL", "DIV"):
                return _fold(name, left, right, node.span)
            raise FormulaError("needs_series", f"both sides of {node.op} are constants", node.span, node.op)
        left, right = self.operand(left, node.span), self.operand(right, node.span)
        if name not in ("ADD", "SUB", "MUL", "DIV"):
            self.level_warning(left, right, node)
        if name == "NE":
            return self.op("NOT", node.span, self.op("EQ", node.span, left, right))
        return self.op(name, node.span, left, right)

    def call(self, node: Call) -> Expr:
        name, span = node.name, node.name_span
        if name in FUTURE_NAMES:
            raise FormulaError("future_function", f"{name} is a future function: it reads bars after day t", span, name)
        if name in PERIOD_FUNCTIONS:
            return self.period_call(node)
        if name in FAMILY_FUNCTIONS:
            return self.family(node)
        if name == "CODES":
            return self.codes(node)
        if name not in FUNCTIONS:
            raise self.unknown(name, span, called=True)
        op = FUNCTIONS[name]
        if self.period != "day" and op in CROSS_SECTIONAL:
            raise FormulaError(
                "period_cross_section",
                f"the cross-sectional {name} cannot be used in a {self.period}ly formula",
                span,
                name,
            )
        roles = OPERATORS[op].roles
        if len(node.args) != len(roles):
            raise FormulaError("bad_arity", f"{name} takes {len(roles)} arguments, not {len(node.args)}", span, name)
        args = [self.argument(role, self.value(arg), arg, name) for role, arg in zip(roles, node.args)]
        return self.op(op, span, *args, nodes=node.args)

    def period_call(self, node: Call) -> Expr:
        name, span = node.name, node.name_span
        if self.period != "day":
            raise FormulaError("period_nested", f"{name} cannot be used in a {self.period}ly formula", span, name)
        if len(node.args) != 1:
            raise FormulaError("bad_arity", f"{name} takes 1 argument, not {len(node.args)}", span, name)
        inner = _Compiler(PERIOD_FUNCTIONS[name])
        inner.values, inner.used, inner.later, inner.warnings = self.values, self.used, self.later, self.warnings
        inner.refs = self.refs
        return self.op(name, span, inner.series(inner.value(node.args[0]), span))

    def family(self, node: Call) -> Expr:
        name, span = node.name, node.name_span
        if len(node.args) != 1 or not isinstance(node.args[0], Text):
            raise FormulaError("bad_arity", f"{name} takes one quoted name, as in {name}('...')", span, name)
        expr = self.located(lambda: Field(f"{FAMILY_FUNCTIONS[name]}@{node.args[0].value}"), node.span, name)
        self.period_field(expr, span, name)
        return expr

    def codes(self, node: Call) -> Expr:
        if not node.args or not all(isinstance(arg, Text) for arg in node.args):
            raise FormulaError(
                "bad_arity", "CODES takes quoted codes, as in CODES('600000.SH')", node.name_span, "CODES"
            )
        expr = self.located(lambda: Codes([arg.value for arg in node.args]), node.span, "CODES")
        self.period_field(expr, node.name_span, "CODES")
        return expr

    # ------------------------------------------------------------ kinds

    def argument(self, role: str, value: Expr | float | str, node: object, name: str) -> Expr | float | int:
        span = node.whole if isinstance(node, Binary) else node.span
        if isinstance(value, str):
            raise FormulaError("needs_series", f"{name} does not take a quoted name", span)
        if role == "c":
            return self.condition(value, span)
        if role == "s":
            return self.series(value, span)
        if role == "x":
            return value
        if isinstance(value, Expr):
            token = node.name if isinstance(node, Name) else None
            raise FormulaError("formula_not_constant", f"{name}'s period must be a constant number", span, token)
        return int(value) if value.is_integer() else value

    def series(self, value: Expr | float | str, span: Span) -> Expr:
        if not isinstance(value, Expr):
            raise FormulaError("needs_series", f"a constant {value!r} where a series belongs", span)
        return value

    def operand(self, value: Expr | float | str, span: Span) -> Expr | float:
        if isinstance(value, str):
            raise FormulaError("needs_series", f"a quoted name {value!r} cannot be computed with", span)
        return value

    def condition(self, value: Expr | float | str, span: Span) -> Expr:
        expr = self.series(value, span)
        return expr if expr.kind == BOOL else NOT(Op("EQ", expr, 0.0))

    def op(self, name: str, span: Span, *args, nodes: tuple = ()) -> Expr:
        """Op(name, *args), an ExprError located at span, or at the argument it names."""
        try:
            return Op(name, *args)
        except ExprError as error:
            at = error.arg if error.arg is not None and error.arg < len(nodes) else None
            place = span if at is None else (nodes[at].whole if isinstance(nodes[at], Binary) else nodes[at].span)
            raise FormulaError(error.code, str(error), place, name) from None

    def located(self, build, span: Span, token: str) -> Expr:
        try:
            return build()
        except ExprError as error:
            raise FormulaError(error.code, str(error), span, token) from None

    # ------------------------------------------------------------ checks

    def period_field(self, expr: Expr, span: Span, token: str) -> None:
        """period_field for a name a weekly or monthly formula's bars do not have."""
        if self.period == "day":
            return
        names = read_fields(expr) if not isinstance(expr, Codes) else {"CODES"}
        if not names <= PERIOD_FIELDS:
            message = (
                f"{token} cannot be read in a {self.period}ly formula: its bars have only prices, volume and amount"
            )
            raise FormulaError("period_field", message, span, token)

    def level_warning(self, left: Expr | float, right: Expr | float, node: Binary) -> None:
        """adjusted_level: a back-adjusted price level compared with a constant price."""
        side = left if isinstance(right, float) else right if isinstance(left, float) else None
        if isinstance(side, Expr) and _adjusted_level(side):
            message = f"a back-adjusted price compared with the constant {left if isinstance(left, float) else right}"
            self.warnings.append(FormulaError("adjusted_level", message, node.whole, node.op))

    def unknown(self, name: str, span: Span, called: bool) -> FormulaError:
        """The error of a name the formula cannot use, saying why."""
        if name in FUTURE_NAMES:
            return FormulaError(
                "future_function", f"{name} is a future function: it reads bars after day t", span, name
            )
        if name in NO_DATA:
            return FormulaError(
                "formula_no_data", f"{name} needs {NO_DATA[name]}, which the data does not have", span, name
            )
        if name in DRAWING:
            return FormulaError("formula_unsupported", f"{name} is a drawing function: it selects nothing", span, name)
        if name in UNSUPPORTED:
            return FormulaError("formula_unsupported", f"{name} is not supported", span, name)
        if name in INDICATORS:
            return FormulaError("formula_unknown_name", f"{name} is an indicator, not a function", span, name)
        if name in INTERNAL:
            return FormulaError("formula_unknown_name", f"{name} is written as an operator in a formula", span, name)
        if not called and (name in FUNCTIONS or name in FAMILY_FUNCTIONS or name in PERIOD_FUNCTIONS):
            return FormulaError("formula_unknown_name", f"{name} is a function: call it with parentheses", span, name)
        if name in self.later:
            return FormulaError(
                "formula_unknown_name", f"{name} is used before line {self.later[name]} defines it", span, name
            )
        close = difflib.get_close_matches(name, [*known(), *self.values], n=1, cutoff=0.75) if len(name) > 2 else []
        hint = f"; did you mean {close[0]}?" if close else ""
        return FormulaError("formula_unknown_name", f"unknown name {name}{hint}", span, name)


def _fold(name: str, left: float, right: float, span: Span) -> float:
    """The constant left name right."""
    if name == "DIV" and right == 0:
        raise FormulaError("bad_constant", "a constant divided by 0", span, "/")
    return (
        left + right
        if name == "ADD"
        else left - right
        if name == "SUB"
        else left * right
        if name == "MUL"
        else left / right
    )


def _adjusted_level(expr: Expr) -> bool:
    """expr is a back-adjusted price level: such a field, or an average, extreme, shift or sum of levels."""
    if isinstance(expr, Field):
        return expr.name in LEVELS
    if isinstance(expr, Op) and expr.name in SAME_LEVEL:
        series = [a for a in expr.args if isinstance(a, Expr)]
        if expr.name == "IF":
            series = series[1:]
        if expr.name == "VALUEWHEN":
            series = series[1:]
        return bool(series) and all(_adjusted_level(a) for a in series)
    return False
