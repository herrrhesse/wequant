"""Strategy descriptions: every validation rule names its field, values are normalised, steps come in funnel order."""

import datetime as dt
import math

import pytest

from wequant.expr import Field
from wequant.strategy import SH_SZ, Condition, Factor, Group, Step, Strategy

CLOSE, ST = Field("close"), Field("st")
OK = Condition("ok", CLOSE > 1.0)
MOM = Factor("mom", CLOSE)


@pytest.mark.parametrize(
    "build, field",
    [
        (lambda: Condition("", CLOSE > 1.0), "name"),
        (lambda: Condition(5, CLOSE > 1.0), "name"),
        (lambda: Condition("date", CLOSE > 1.0), "name"),
        (lambda: Condition("rank", CLOSE > 1.0), "name"),
        (lambda: Condition("c", CLOSE), "expr"),
        (lambda: Condition("c", "close > 1"), "expr"),
        (lambda: Condition("c", CLOSE > 1.0, enabled=1), "enabled"),
        (lambda: Group("g", ()), "conditions"),
        (lambda: Group("g", (MOM,)), "conditions"),
        (lambda: Group("g", OK), "conditions"),
        (lambda: Group("g", (OK,), mode="some"), "mode"),
        (lambda: Group("g", (OK,), enabled="yes"), "enabled"),
        (lambda: Group("universe", (OK,)), "name"),
        (lambda: Factor("f", CLOSE, prefer="up"), "prefer"),
        (lambda: Factor("f", CLOSE, weight=0), "weight"),
        (lambda: Factor("f", CLOSE, weight=-1.0), "weight"),
        (lambda: Factor("f", CLOSE, weight=math.inf), "weight"),
        (lambda: Factor("f", CLOSE, weight=math.nan), "weight"),
        (lambda: Factor("f", CLOSE, weight=10**400), "weight"),
        (lambda: Factor("f", CLOSE, weight=True), "weight"),
        (lambda: Factor("f", CLOSE, weight="1"), "weight"),
        (lambda: Factor("f", 1.0), "expr"),
        (lambda: Factor("f", CLOSE, enabled=None), "enabled"),
        (lambda: Strategy(universe=CLOSE), "universe"),
        (lambda: Strategy(filters=(MOM,)), "filters"),
        (lambda: Strategy(factors=(OK,)), "factors"),
        (lambda: Strategy(filters=(OK,), factors=(Factor("ok", CLOSE),)), "name"),
        (lambda: Strategy(filters=(Group("g", (OK,)), OK)), "name"),
        (lambda: Strategy(filters=(Group("g", (Condition("g", CLOSE > 1.0),)),)), "name"),
        (lambda: Strategy(factors=(MOM,), top_n=0), "top_n"),
        (lambda: Strategy(factors=(MOM,), top_n=True), "top_n"),
        (lambda: Strategy(factors=(MOM,), top_n=2.0), "top_n"),
        (lambda: Strategy(top_n=2), "top_n"),
        (lambda: Strategy(factors=(Factor("mom", CLOSE, enabled=False),), top_n=2), "top_n"),
        (lambda: Strategy(factors=(MOM,), top_n=2, backups=-1), "backups"),
        (lambda: Strategy(factors=(MOM,), top_n=2, backups=True), "backups"),
        (lambda: Strategy(factors=(MOM,), backups=1), "backups"),
        (lambda: Strategy(period="year"), "period"),
        (lambda: Strategy(every=0), "every"),
        (lambda: Strategy(every=True), "every"),
        (lambda: Strategy(weighting="rank"), "weighting"),
        (lambda: Strategy(weighting="score"), "weighting"),
        (lambda: Strategy(formula=CLOSE), "formula"),
        (lambda: Strategy(weighting="formula"), "formula"),
        (lambda: Strategy(weighting="formula", formula="close"), "formula"),
        (lambda: Strategy(band=-0.1), "band"),
        (lambda: Strategy(band=math.nan), "band"),
        (lambda: Strategy(band=True), "band"),
        (lambda: Strategy(anchor="2024-01-02"), "anchor"),
        (lambda: Strategy(anchor=dt.datetime(2024, 1, 2)), "anchor"),
    ],
)
def test_each_rule_raises_a_value_error_naming_its_field(build, field):
    with pytest.raises(ValueError, match=rf"^{field}: "):
        build()


def test_lists_become_tuples_and_numbers_floats():
    group = Group("g", [OK])
    strategy = Strategy(filters=[group], factors=[Factor("mom", CLOSE, weight=2)], band=1)
    assert group.conditions == (OK,) and strategy.filters == (group,) and isinstance(strategy.factors, tuple)
    assert type(strategy.factors[0].weight) is float and strategy.factors[0].weight == 2.0
    assert type(strategy.band) is float and strategy.band == 1.0
    assert math.copysign(1, Strategy(band=-0.0).band) == 1.0


def test_defaults():
    strategy = Strategy()
    assert strategy.universe == SH_SZ and strategy.universe.form == ["NOT", "exchange@BJ"]
    assert (strategy.top_n, strategy.backups, strategy.period, strategy.every) == (None, 0, "week", 1)
    assert (strategy.weighting, strategy.formula, strategy.band, strategy.anchor) == ("equal", None, None, None)


def test_steps_in_funnel_order_without_disabled_items_and_empty_groups():
    a, b = Condition("a", CLOSE > 1.0), Condition("b", ~ST)
    off = Condition("off", CLOSE > 2.0, enabled=False)
    strategy = Strategy(
        filters=(
            a,
            off,
            Group("g", (b, Condition("c", CLOSE > 3.0, enabled=False), Condition("d", CLOSE > 4.0)), mode="any"),
            Group("empty", (Condition("e", CLOSE > 5.0, enabled=False),)),
            Group("disabled", (Condition("f", CLOSE > 6.0),), enabled=False),
        )
    )
    assert strategy.steps() == (
        Step("a", a.expr, (), "all", True),
        Step("b", b.expr, (), "all", False),
        Step("d", CLOSE > 4.0, (), "all", False),
        Step("g", None, ("b", "d"), "any", True),
    )
