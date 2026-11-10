"""Strategy descriptions: every validation rule names its field, values are normalised, steps come in funnel order."""

import datetime as dt
import math
from dataclasses import fields

import pytest

from wequant.expr import Field
from wequant.strategy import SH_SZ, Condition, Factor, Group, Pool, SpecError, Step, Strategy

CLOSE, ST = Field("close"), Field("st")
OK = Condition("ok", CLOSE > 1.0)
MOM = Factor("mom", CLOSE)


TOP2 = {"factors": (MOM,), "top_n": 2}


@pytest.mark.parametrize(
    "build, code, loc",
    [
        (lambda: Condition("", CLOSE > 1.0), "out_of_range", ("name",)),
        (lambda: Condition(5, CLOSE > 1.0), "wrong_type", ("name",)),
        (lambda: Condition("date", CLOSE > 1.0), "reserved_name", ("name",)),
        (lambda: Condition("rank", CLOSE > 1.0), "reserved_name", ("name",)),
        (lambda: Condition("held", CLOSE > 1.0), "reserved_name", ("name",)),
        (lambda: Condition("min_list_days", CLOSE > 1.0), "reserved_name", ("name",)),
        (lambda: Condition("c", CLOSE), "needs_condition", ("expr",)),
        (lambda: Condition("c", "close > 1"), "wrong_type", ("expr",)),
        (lambda: Condition("c", CLOSE > 1.0, enabled=1), "wrong_type", ("enabled",)),
        (lambda: Group("g", ()), "out_of_range", ("conditions",)),
        (lambda: Group("g", (MOM,)), "wrong_type", ("conditions",)),
        (lambda: Group("g", OK), "wrong_type", ("conditions",)),
        (lambda: Group("g", (OK,), mode="some"), "not_a_choice", ("mode",)),
        (lambda: Group("g", (OK,), enabled="yes"), "wrong_type", ("enabled",)),
        (lambda: Group("universe", (OK,)), "reserved_name", ("name",)),
        (lambda: Factor("f", CLOSE, prefer="up"), "not_a_choice", ("prefer",)),
        (lambda: Factor("f", CLOSE, weight=0), "out_of_range", ("weight",)),
        (lambda: Factor("f", CLOSE, weight=-1.0), "out_of_range", ("weight",)),
        (lambda: Factor("f", CLOSE, weight=math.inf), "out_of_range", ("weight",)),
        (lambda: Factor("f", CLOSE, weight=math.nan), "out_of_range", ("weight",)),
        (lambda: Factor("f", CLOSE, weight=10**400), "out_of_range", ("weight",)),
        (lambda: Factor("f", CLOSE, weight=True), "wrong_type", ("weight",)),
        (lambda: Factor("f", CLOSE, weight="1"), "wrong_type", ("weight",)),
        (lambda: Factor("f", 1.0), "wrong_type", ("expr",)),
        (lambda: Factor("f", CLOSE, enabled=None), "wrong_type", ("enabled",)),
        (lambda: Strategy(universe=CLOSE), "needs_condition", ("universe",)),
        (lambda: Strategy(filters=(MOM,)), "wrong_type", ("filters",)),
        (lambda: Strategy(factors=(OK,)), "wrong_type", ("factors",)),
        (lambda: Strategy(filters=(OK,), factors=(Factor("ok", CLOSE),)), "duplicate_name", ("factors", 0, "name")),
        (lambda: Strategy(filters=(Group("g", (OK,)), OK)), "duplicate_name", ("filters", 1, "name")),
        (
            lambda: Strategy(filters=(Group("g", (Condition("g", CLOSE > 1.0),)),)),
            "duplicate_name",
            ("filters", 0, "conditions", 0, "name"),
        ),
        (lambda: Strategy(factors=(MOM,), top_n=0), "out_of_range", ("top_n",)),
        (lambda: Strategy(factors=(MOM,), top_n=True), "wrong_type", ("top_n",)),
        (lambda: Strategy(factors=(MOM,), top_n=2.0), "wrong_type", ("top_n",)),
        (lambda: Strategy(top_n=2), "requires", ("top_n",)),
        (lambda: Strategy(factors=(Factor("mom", CLOSE, enabled=False),), top_n=2), "requires", ("top_n",)),
        (lambda: Strategy(**TOP2, backups=-1), "out_of_range", ("backups",)),
        (lambda: Strategy(**TOP2, backups=True), "wrong_type", ("backups",)),
        (lambda: Strategy(factors=(MOM,), backups=1), "requires", ("backups",)),
        (lambda: Strategy(period="year"), "not_a_choice", ("period",)),
        (lambda: Strategy(every=0), "out_of_range", ("every",)),
        (lambda: Strategy(every=True), "wrong_type", ("every",)),
        (lambda: Strategy(weighting="rank"), "not_a_choice", ("weighting",)),
        (lambda: Strategy(weighting="score"), "requires", ("weighting",)),
        (lambda: Strategy(formula=CLOSE), "requires", ("formula",)),
        (lambda: Strategy(weighting="formula"), "requires", ("formula",)),
        (lambda: Strategy(weighting="formula", formula="close"), "wrong_type", ("formula",)),
        (lambda: Strategy(band=-0.1), "out_of_range", ("band",)),
        (lambda: Strategy(band=math.nan), "out_of_range", ("band",)),
        (lambda: Strategy(band=True), "wrong_type", ("band",)),
        (lambda: Strategy(anchor="2024-01-02"), "wrong_type", ("anchor",)),
        (lambda: Strategy(anchor=dt.datetime(2024, 1, 2)), "wrong_type", ("anchor",)),
        (lambda: Strategy(factors=(MOM,), hold_rank=3), "requires", ("hold_rank",)),
        (lambda: Strategy(factors=(MOM,), hold_rank=1), "requires", ("hold_rank",)),
        (lambda: Strategy(**TOP2, hold_rank=0), "out_of_range", ("hold_rank",)),
        (lambda: Strategy(**TOP2, hold_rank=2), "out_of_range", ("hold_rank",)),
        (lambda: Strategy(**TOP2, hold_rank=True), "wrong_type", ("hold_rank",)),
        (lambda: Strategy(**TOP2, hold_rank=3.0), "wrong_type", ("hold_rank",)),
        *[(lambda x=x: Strategy(max_weight=x), "out_of_range", ("max_weight",)) for x in (0, 1, 1.5, -0.1, math.nan)],
        (lambda: Strategy(max_weight="0.1"), "wrong_type", ("max_weight",)),
        (lambda: Strategy(max_weight=True), "wrong_type", ("max_weight",)),
        (lambda: Strategy(pool={"exclude_st": True}), "wrong_type", ("pool",)),
        (lambda: Pool(index="000001.SH"), "not_a_choice", ("index",)),
        (lambda: Pool(index=300), "wrong_type", ("index",)),
        (lambda: Pool(boards=()), "out_of_range", ("boards",)),
        (lambda: Pool(boards="main"), "wrong_type", ("boards",)),
        (lambda: Pool(boards=("main", 1)), "wrong_type", ("boards",)),
        (lambda: Pool(boards=("main", "bse")), "not_a_choice", ("boards", 1)),
        (lambda: Pool(boards=("star", "main", "star")), "duplicate_value", ("boards", 2)),
        (lambda: Pool(exclude_st=1), "wrong_type", ("exclude_st",)),
        (lambda: Pool(min_list_days=1), "out_of_range", ("min_list_days",)),
        (lambda: Pool(min_list_days=0), "out_of_range", ("min_list_days",)),
        (lambda: Pool(min_list_days=True), "wrong_type", ("min_list_days",)),
        (lambda: Pool(min_list_days=2.0), "wrong_type", ("min_list_days",)),
    ],
)
def test_each_rule_raises_its_code_at_its_location(build, code, loc):
    with pytest.raises(SpecError) as raised:
        build()
    assert (raised.value.code, raised.value.loc, raised.value.path) == (code, loc, ())
    assert str(raised.value).startswith(".".join(map(str, loc)) + ": ")


def test_a_duplicate_name_is_named_in_the_message():
    with pytest.raises(SpecError, match="'ok' names more than one"):
        Strategy(filters=(OK,), factors=(Factor("ok", CLOSE),))


def test_the_new_fields_keep_their_values_and_order_the_boards():
    pool = Pool(index="000300.SH", boards=["star", "main"], exclude_st=True, min_list_days=250)
    strategy = Strategy(factors=(MOM,), top_n=2, hold_rank=3, max_weight=1 / 4, pool=pool)
    assert pool.boards == ("main", "star") and strategy.pool is pool
    assert (strategy.hold_rank, strategy.max_weight) == (3, 0.25) and type(
        Strategy(max_weight=1e-9).max_weight
    ) is float
    assert [f.name for f in fields(Strategy)][-4:] == ["anchor", "hold_rank", "max_weight", "pool"]


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
    assert (strategy.hold_rank, strategy.max_weight, strategy.pool) == (None, None, Pool())
    assert (Pool().index, Pool().boards, Pool().exclude_st, Pool().min_list_days) == (None, None, False, None)


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
