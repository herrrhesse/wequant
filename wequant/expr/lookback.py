"""What an expression reads: the fields it names and how many of a stock's bars back it reaches.

The lookback counts bars, as the time-series operators do; they skip cells without a finite input, so the true reach can
be longer and a lookback understates it. A WEEK or MONTH counts its bars as 5 and 23 trading days, the most a week or
a month has, plus the ended period that a day inside the next one reads (S4.8).
"""

from __future__ import annotations

from wequant.expr.tree import Codes, Expr, Field, Op
from wequant.expr.vocab import PERIODS

WINDOWS = frozenset("MA WMA STD STDP AVEDEV SLOPE TS_RANK CORR SUM HHV LLV HHVBARS LLVBARS COUNT EVERY EXIST".split())
WHOLE = frozenset("EMA SMA BARSLAST BARSCOUNT VALUEWHEN".split())  # every bar from the panel's first row
PERIOD_DAYS = {"WEEK": 5, "MONTH": 23}  # the most trading days a week, a month has


def lookback(expr: Expr) -> int | None:
    """Bars before the last one that expr reads at most; None when it reads a stock's whole history."""
    if isinstance(expr, Field | Codes):
        return 0
    reach = [lookback(a) for a in expr.args if isinstance(a, Expr)]
    if expr.name in WHOLE or None in reach:
        return None
    deepest, n = max(reach), next((a for a in expr.args if type(a) is int), 0)
    if expr.name in PERIOD_DAYS:  # deepest bars back from the period read, which a day inside the next one reads
        return (deepest + 2) * PERIOD_DAYS[expr.name] - 1
    if expr.name == "REF":
        return n + deepest
    if expr.name in WINDOWS:
        return None if n == 0 else n - 1 + deepest
    return deepest + (expr.name == "CROSS")


def periods(expr: Expr) -> tuple[str, ...]:
    """The periods ("week", "month") expr reads bars of, in that order."""
    found, stack = set(), [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, Op):
            found |= {PERIODS[node.name]} if node.name in PERIODS else set()
            stack.extend(a for a in node.args if isinstance(a, Expr))
    return tuple(p for p in PERIODS.values() if p in found)


def fields(expr: Expr) -> frozenset[str]:
    """The names of the fields expr reads."""
    if isinstance(expr, Field):
        return frozenset((expr.name,))
    if isinstance(expr, Codes):
        return frozenset()
    return frozenset().union(*(fields(a) for a in expr.args if isinstance(a, Op | Field)))
