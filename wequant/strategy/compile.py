"""A strategy as the roots select evaluates: the pool is the universe and the pool items, and every cross-sectional
operator in a condition, factor or formula ranks within it, so no condition depends on another. A simple comparison
also names the side whose value a screen shows."""

from __future__ import annotations

import operator
from functools import reduce
from typing import TYPE_CHECKING, NamedTuple

from wequant.expr import IF, Codes, Expr, Field, Op

if TYPE_CHECKING:
    from wequant.strategy.strategy import Strategy

CROSS_SECTIONAL = frozenset(("CS_RANK", "CS_PCT", "CS_ZSCORE", "CS_DEMEAN"))
FLIPPED = {"GT": "lt", "GE": "le", "LT": "gt", "LE": "ge", "EQ": "eq"}  # the comparison with the constant moved right


def pool_items(strategy: Strategy) -> dict[str, Expr]:
    """The set pool items as conditions, in the order index, boards, exclude_st, min_list_days; a fixed list of
    stocks is part of the scope instead (scope)."""
    pool, items = strategy.pool, {}
    if pool.index is not None:
        items["index"] = Field(f"member@{pool.index}")
    if pool.boards is not None:
        items["boards"] = reduce(operator.or_, [Field(f"board@{board}") for board in pool.boards])
    if pool.exclude_st:
        items["exclude_st"] = ~Field("st")
    if pool.min_list_days is not None:
        items["min_list_days"] = Field("list_days") >= pool.min_list_days
    return items


def scope(strategy: Strategy) -> Expr:
    """The universe, and the fixed list of stocks when the pool has one: the funnel's first level after the bars."""
    stocks = strategy.pool.stocks
    return strategy.universe if stocks is None else strategy.universe & Codes(stocks.codes)


def pool_expr(strategy: Strategy) -> Expr:
    """The scope and every pool item."""
    return reduce(operator.and_, pool_items(strategy).values(), scope(strategy))


def wrap(expr: Expr, pool: Expr) -> Expr:
    """expr with the ranked argument x of every cross-sectional operator, nested ones too, replaced by
    IF(pool, x, None); expr itself when it has none."""
    if not isinstance(expr, Op):
        return expr
    args = [wrap(a, pool) if isinstance(a, Expr) else a for a in expr.args]
    if expr.name in CROSS_SECTIONAL:
        args[0] = IF(pool, args[0], None)
    elif all(a is b for a, b in zip(args, expr.args)):
        return expr
    return Op(expr.name, *args)


def contains(expr: Expr, node: Expr) -> bool:
    """node is expr or one of its subexpressions, by key."""
    return expr == node or (isinstance(expr, Op) and any(isinstance(a, Expr) and contains(a, node) for a in expr.args))


def expressions(strategy: Strategy) -> dict[str, Expr]:
    """The roots by name: "universe", each pool item, "pool", each enabled step's condition, each enabled factor and
    "formula", the last three wrapped in the pool."""
    pool = pool_expr(strategy)
    roots = {"universe": scope(strategy), **pool_items(strategy), "pool": pool}
    roots |= {step.name: wrap(step.expr, pool) for step in strategy.steps() if step.expr is not None}
    roots |= {f.name: wrap(f.expr, pool) for f in strategy.factors if f.enabled}
    if strategy.weighting == "formula":
        roots["formula"] = wrap(strategy.formula, pool)
    return roots


class Comparison(NamedTuple):
    """A simple comparison: its expression side against a constant, or between two constants."""

    side: Expr
    op: str  # gt, ge, lt, le, eq or between
    threshold: float
    threshold_hi: float | None  # the upper constant of between


def comparison(expr: Expr) -> Comparison | None:
    """expr as a simple comparison, its constant on the right; None for any other shape."""
    if isinstance(expr, Op) and expr.name in FLIPPED:
        a, b = expr.args
        if isinstance(a, Expr) and isinstance(b, float):
            return Comparison(a, expr.name.lower(), b, None)
        if isinstance(b, Expr) and isinstance(a, float):
            return Comparison(b, FLIPPED[expr.name], a, None)
    if isinstance(expr, Op) and expr.name == "BETWEEN":
        x, lo, hi = expr.args
        if isinstance(x, Expr) and isinstance(lo, float) and isinstance(hi, float):
            return Comparison(x, "between", min(lo, hi), max(lo, hi))
    return None
