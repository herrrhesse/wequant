"""An expression tree written as formula text, without variables (S4.1): parsing the text gives the same tree back."""

from __future__ import annotations

from wequant.expr import Codes, Expr, Field, Op
from wequant.formula.names import FIELD_NAMES

INFIX = {"ADD": ("+", 4), "SUB": ("-", 4), "MUL": ("*", 5), "DIV": ("/", 5), "GT": (">", 3), "GE": (">=", 3)}
INFIX |= {"LT": ("<", 3), "LE": ("<=", 3), "EQ": ("=", 3), "AND": ("AND", 2), "OR": ("OR", 1)}
FAMILIES = {"member": "MEMBER", "board": "BOARD", "exchange": "EXCHANGE"}
PERIODS = {"WEEK": "week", "MONTH": "month"}
TEXT_OF = {expr.key: name for name, expr in reversed(FIELD_NAMES.items())}  # the long name wins over C, O, H, L


def format_formula(expr: Expr) -> tuple[str, str]:
    """(text, period): a WEEK or MONTH at the root becomes the period, the rest the text."""
    if isinstance(expr, Op) and expr.name in PERIODS:
        return _text(expr.args[0], 0), PERIODS[expr.name]
    return _text(expr, 0), "day"


def _text(node: Expr | float | int | None, floor: int) -> str:
    """node's text, in parentheses when its operator binds looser than floor requires."""
    if not isinstance(node, Expr):
        return _number(node)
    if node.key in TEXT_OF:
        return TEXT_OF[node.key]
    if isinstance(node, Field):
        family, _, param = node.name.partition("@")
        return f"{FAMILIES[family]}('{param}')" if param else node.name.upper()
    if isinstance(node, Codes):
        return "CODES(" + ", ".join(f"'{code}'" for code in node.codes) + ")"
    if node.name == "SUB" and node.args[0] == 0.0 and isinstance(node.args[1], Expr):
        return _wrap("-" + _text(node.args[1], 6), 6, floor)
    if node.name == "NOT":
        return _wrap("NOT " + _text(node.args[0], 6), 6, floor)
    if node.name in INFIX:
        symbol, power = INFIX[node.name]
        left, right = _text(node.args[0], power), _text(node.args[1], power + 1)  # left-associative
        return _wrap(f"{left} {symbol} {right}", power, floor)
    return f"{node.name}(" + ", ".join(_text(a, 0) for a in node.args) + ")"


def _wrap(text: str, power: int, floor: int) -> str:
    return f"({text})" if power < floor else text


def _number(value: float | int | None) -> str:
    if value is None:
        raise ValueError("a tree with an empty argument has no formula text")
    if isinstance(value, int):
        return str(value)
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
