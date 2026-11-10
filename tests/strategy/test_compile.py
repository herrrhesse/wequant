"""Compiling a strategy: the pool from the universe and its items, and the pool wrapped into cross-sectional operators."""

import pytest

from wequant.expr import CS_PCT, CS_RANK, CS_ZSCORE, IF, MA, Field
from wequant.strategy import (
    SH_SZ,
    Condition,
    Factor,
    Group,
    Pool,
    SpecErrors,
    Strategy,
    expressions,
    pool_expr,
    pool_items,
    wrap,
)

CLOSE, OPEN, AMOUNT = Field("close"), Field("open"), Field("amount")
POOL = SH_SZ & ~Field("st")


def test_each_pool_item_is_its_condition_and_no_item_leaves_the_universe_itself():
    """Design: index -> a member of it, boards -> on one of them, exclude_st -> not ST, min_list_days -> listed at least
    that many days; items come in that order, and the default pool is the universe (same root, same cache key)."""
    strategy = Strategy(pool=Pool(index="000905.SH", boards=("star", "main"), exclude_st=True, min_list_days=60))
    assert pool_items(strategy) == {
        "index": Field("member@000905.SH"),
        "boards": Field("board@main") | Field("board@star"),
        "exclude_st": ~Field("st"),
        "min_list_days": Field("list_days") >= 60,
    }
    assert pool_expr(Strategy()) is SH_SZ and pool_items(Strategy()) == {}


def test_wrap_replaces_the_ranked_argument_of_every_cross_sectional_operator_and_nothing_else():
    assert wrap(CS_RANK(CLOSE) > 2, POOL) == (CS_RANK(IF(POOL, CLOSE, None)) > 2)
    assert wrap(MA(CS_PCT(AMOUNT), 5), POOL) == MA(CS_PCT(IF(POOL, AMOUNT, None)), 5)
    nested = CS_ZSCORE(CS_RANK(CLOSE) + OPEN)
    assert wrap(nested, POOL) == CS_ZSCORE(IF(POOL, CS_RANK(IF(POOL, CLOSE, None)) + OPEN, None))
    plain = MA(CLOSE, 5) > OPEN
    assert wrap(plain, POOL) is plain and wrap(CLOSE, POOL) is CLOSE


def test_expressions_wrap_conditions_factors_and_the_formula_but_never_the_pool():
    universe = SH_SZ & (CS_PCT(AMOUNT) > 0.1)
    strategy = Strategy(
        universe=universe,
        filters=(Condition("big", CS_RANK(CLOSE) > 2), Group("g", (Condition("up", CLOSE > OPEN),))),
        factors=(Factor("z", CS_ZSCORE(CLOSE)), Factor("off", CS_RANK(OPEN), enabled=False)),
        weighting="formula",
        formula=CS_PCT(OPEN),
        pool=Pool(exclude_st=True),
    )
    roots = expressions(strategy)
    pool = universe & ~Field("st")
    assert list(roots) == ["universe", "exclude_st", "pool", "big", "up", "z", "formula"]
    assert roots["universe"] is universe and roots["pool"] == pool
    assert (
        roots["big"] == (CS_RANK(IF(pool, CLOSE, None)) > 2) and roots["up"] is strategy.filters[1].conditions[0].expr
    )
    assert roots["z"] == CS_ZSCORE(IF(pool, CLOSE, None)) and roots["formula"] == CS_PCT(IF(pool, OPEN, None))


def test_a_strategy_whose_compiled_expression_is_too_large_names_that_expression():
    big = CLOSE
    for _ in range(10):
        big = big + big  # 2,047 nodes
    ranks = CS_RANK(CLOSE) + CS_RANK(OPEN) + CS_RANK(AMOUNT) + CS_RANK(Field("high")) + CS_RANK(Field("low"))
    Strategy(universe=big > 0, filters=(Condition("ok", ranks > 0, enabled=False),))
    with pytest.raises(SpecErrors) as raised:
        Strategy(universe=big > 0, filters=(Group("g", (Condition("a", CLOSE > 1), Condition("ok", ranks > 0))),))
    assert [(e.code, e.loc) for e in raised.value.errors] == [("too_large", ("filters", 0, "conditions", 1, "expr"))]
    with pytest.raises(SpecErrors) as raised:
        Strategy(universe=big > 0, factors=(Factor("f", CLOSE), Factor("r", ranks)))
    assert [(e.code, e.loc) for e in raised.value.errors] == [("too_large", ("factors", 1, "expr"))]
