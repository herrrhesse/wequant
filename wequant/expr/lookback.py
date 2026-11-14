"""What an expression reads: the fields it names and how many of a stock's bars back it reaches.

The lookback counts bars, as the time-series operators do; they skip cells without a finite input, so the true reach can
be longer and a lookback understates it, never overstates it.
"""

from __future__ import annotations

from wequant.expr.tree import Codes, Expr, Field, Op

WINDOWS = frozenset("MA WMA STD STDP AVEDEV SLOPE TS_RANK CORR SUM HHV LLV HHVBARS LLVBARS COUNT EVERY EXIST".split())
WHOLE = frozenset("EMA SMA BARSLAST BARSCOUNT VALUEWHEN".split())  # every bar from the panel's first row


def lookback(expr: Expr) -> int | None:
    """Bars before the last one that expr reads at most; None when it reads a stock's whole history."""
    if isinstance(expr, Field | Codes):
        return 0
    reach = [lookback(a) for a in expr.args if isinstance(a, Expr)]
    if expr.name in WHOLE or None in reach:
        return None
    deepest, n = max(reach), next((a for a in expr.args if type(a) is int), 0)
    if expr.name == "REF":
        return n + deepest
    if expr.name in WINDOWS:
        return None if n == 0 else n - 1 + deepest
    return deepest + (expr.name == "CROSS")


def fields(expr: Expr) -> frozenset[str]:
    """The names of the fields expr reads."""
    if isinstance(expr, Field):
        return frozenset((expr.name,))
    if isinstance(expr, Codes):
        return frozenset()
    return frozenset().union(*(fields(a) for a in expr.args if isinstance(a, Op | Field)))
