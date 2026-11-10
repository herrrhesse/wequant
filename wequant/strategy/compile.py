"""A strategy as the roots select evaluates: the pool is the universe and the pool items, and every cross-sectional
operator in a condition, factor or formula ranks within it, so no condition depends on another."""

from __future__ import annotations

import operator
from functools import reduce
from typing import TYPE_CHECKING

from wequant.expr import IF, Expr, Field, Op

if TYPE_CHECKING:
    from wequant.strategy.strategy import Strategy

CROSS_SECTIONAL = frozenset(("CS_RANK", "CS_PCT", "CS_ZSCORE", "CS_DEMEAN"))


def pool_items(strategy: Strategy) -> dict[str, Expr]:
    """The set pool items as conditions, in the order index, boards, exclude_st, min_list_days."""
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


def pool_expr(strategy: Strategy) -> Expr:
    """The universe and every pool item."""
    return reduce(operator.and_, pool_items(strategy).values(), strategy.universe)


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


def expressions(strategy: Strategy) -> dict[str, Expr]:
    """The roots by name: "universe", each pool item, "pool", each enabled step's condition, each enabled factor and
    "formula", the last three wrapped in the pool."""
    pool = pool_expr(strategy)
    roots = {"universe": strategy.universe, **pool_items(strategy), "pool": pool}
    roots |= {step.name: wrap(step.expr, pool) for step in strategy.steps() if step.expr is not None}
    roots |= {f.name: wrap(f.expr, pool) for f in strategy.factors if f.enabled}
    if strategy.weighting == "formula":
        roots["formula"] = wrap(strategy.formula, pool)
    return roots
