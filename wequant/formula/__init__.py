"""TDX-style formula text (S4.1): read, compiled into wequant.expr trees, and trees written back as text."""

from wequant.formula.compile import CONDITION_ROLES, MAX_TEXT, PERIODS, Compiled, Variable, compile_formula
from wequant.formula.format import format_formula
from wequant.formula.syntax import FormulaError, Span

__all__ = [
    "CONDITION_ROLES",
    "MAX_TEXT",
    "PERIODS",
    "Compiled",
    "FormulaError",
    "Span",
    "Variable",
    "compile_formula",
    "format_formula",
]
