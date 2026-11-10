"""Canonical forms: golden keys, round trips compared as canonical text, and refusals that are never TypeErrors."""

import datetime as dt
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wequant.account import Execution
from wequant.analytics import MetricSpec
from wequant.backtest import Backtest
from wequant.canonical import canonical
from wequant.expr import INDEXES, REF, STD, Field
from wequant.strategy import (
    MODES,
    PERIODS,
    POOL_BOARDS,
    PREFERS,
    SH_SZ,
    Condition,
    Factor,
    Group,
    Pool,
    SpecError,
    SpecErrors,
    Strategy,
    parse_form,
    to_form,
)

ADJ, CLOSE, OPEN, ST, VOLUME = Field("adj_close"), Field("close"), Field("open"), Field("st"), Field("volume")
EX0 = Execution(slippage_bps=0.0)
MOM = Strategy(factors=(Factor("mom", ADJ / REF(ADJ, 1)),), top_n=2)
EXECUTION = (
    '{"commission_rate":0.0003,"delisting":"auto","failure_recovery":0.0,"min_commission":5.0,"price":"open",'
    '"slippage_bps":0.0}'
)
DATA = '{"anomalies":"flag","exclude_suspicious":false}'
METRICS = '{"periods_per_year":242,"risk_free_rate":0.0,"tail_fraction":0.05}'
MOM_KEY = '{"factors":[{"expr":["DIV","adj_close",["REF","adj_close",1]],"name":"mom"}],"top_n":2}'


def test_golden_strategy_keys():
    assert MOM.key == MOM_KEY
    strategy = Strategy(
        filters=(Group("g", (Condition("a", CLOSE > 5), Condition("b", ~ST, enabled=False)), mode="any"),),
        factors=(Factor("v", VOLUME, prefer="low", weight=2),),
        top_n=10,
        backups=2,
        band=0.2,
    )
    assert strategy.key == (
        '{"backups":2,"band":0.2,"factors":[{"expr":"volume","name":"v","prefer":"low","weight":2.0}],'
        '"filters":[{"conditions":[{"expr":["GT","close",5.0],"name":"a"},{"enabled":false,"expr":["NOT","st"],'
        '"name":"b"}],"mode":"any","name":"g"}],"top_n":10}'
    )


def test_the_new_fields_stay_out_of_the_form_at_their_defaults_and_have_golden_keys():
    assert Strategy(pool=Pool(), hold_rank=None, max_weight=None).key == Strategy().key == "{}"
    assert Strategy(factors=MOM.factors, top_n=2, pool=Pool(exclude_st=False)).key == MOM_KEY
    pool = Pool(index="000905.SH", boards=("star", "main"), exclude_st=True, min_list_days=250)
    strategy = Strategy(factors=MOM.factors, top_n=2, hold_rank=4, max_weight=0.3, pool=pool)
    assert strategy.key == (
        '{"factors":[{"expr":["DIV","adj_close",["REF","adj_close",1]],"name":"mom"}],"hold_rank":4,"max_weight":0.3,'
        '"pool":{"boards":["main","star"],"exclude_st":true,"index":"000905.SH","min_list_days":250},"top_n":2}'
    )
    assert Strategy(pool=Pool(boards=("chinext",))).key == '{"pool":{"boards":["chinext"]}}'


def test_golden_backtest_keys_write_execution_and_metrics_in_full():
    key = f'{{"data":{DATA},"execution":{EXECUTION},"metrics":{METRICS},"strategy":{MOM_KEY}}}'
    assert Backtest(MOM, execution=EX0).key == key
    assert Backtest(MOM, execution=Execution(slippage_bps=-0.0)).key == key
    ret1 = Strategy(factors=(Factor("ret1", ADJ / REF(ADJ, 1)),), top_n=1, period="day")
    assert Backtest(ret1, cash=100_000, execution=EX0).key == (
        f'{{"cash":100000.0,"data":{DATA},"execution":{EXECUTION},"metrics":{METRICS},"strategy":{{"factors":[{{"expr":["DIV",'
        '"adj_close",["REF","adj_close",1]],"name":"ret1"}],"period":"day","top_n":1}}'
    )


def test_to_form_writes_dates_as_iso_text_and_none_when_it_is_not_the_default():
    backtest = Backtest(Strategy(anchor=dt.date(2016, 11, 7)), start=dt.date(2016, 11, 8), benchmark=None)
    form = backtest.form
    assert form["strategy"] == {"anchor": "2016-11-07"} and form["start"] == "2016-11-08"
    assert form["benchmark"] is None and "end" not in form and "cash" not in form


def test_round_trips_compare_canonical_text():
    backtest = Backtest(MOM, start=dt.date(2020, 1, 2), cash=123_456.78, execution=EX0, metrics=MetricSpec(250))
    assert Backtest.from_form(backtest.form) == backtest and Backtest.from_form(backtest.form).key == backtest.key
    assert Strategy.from_form(MOM.form) == MOM
    form = json.loads(backtest.key)
    assert canonical(to_form(parse_form(Backtest, form))) == canonical(form)


NUMBERS = st.one_of(st.integers(1, 9), st.floats(0.01, 9.0, allow_nan=False, allow_infinity=False))
BOOLS = [CLOSE > 5.0, ~ST, Field("limit_up"), (CLOSE > OPEN) & ~ST]
NUMS = [CLOSE, ADJ / REF(ADJ, 5) - 1, STD(ADJ, 20), VOLUME]
DAYS = st.dates(dt.date(2005, 1, 1), dt.date(2026, 12, 31))


@st.composite
def strategies(draw) -> Strategy:
    flag = st.booleans()
    conditions = [Condition(f"c{i}", draw(st.sampled_from(BOOLS)), draw(flag)) for i in range(draw(st.integers(0, 3)))]
    groups = [
        Group(
            f"g{i}",
            [Condition(f"g{i}c{k}", draw(st.sampled_from(BOOLS)), draw(flag)) for k in range(draw(st.integers(1, 2)))],
            draw(st.sampled_from(MODES)),
            draw(flag),
        )
        for i in range(draw(st.integers(0, 2)))
    ]
    factors = [
        Factor(f"f{i}", draw(st.sampled_from(NUMS + BOOLS)), draw(st.sampled_from(PREFERS)), draw(NUMBERS), draw(flag))
        for i in range(draw(st.integers(0, 3)))
    ]
    ranked = any(f.enabled for f in factors)
    top_n = draw(st.none() | st.integers(1, 60)) if ranked else None
    weighting = draw(st.sampled_from(["equal", "formula", *(["score"] if ranked else [])]))
    boards = st.lists(st.sampled_from(POOL_BOARDS), min_size=1, max_size=3, unique=True)
    pool = Pool(
        index=draw(st.none() | st.sampled_from(INDEXES)),
        boards=draw(st.none() | boards),
        exclude_st=draw(flag),
        min_list_days=draw(st.none() | st.integers(2, 500)),
    )
    return Strategy(
        universe=draw(st.sampled_from([SH_SZ, SH_SZ & (CLOSE > 1.0)])),
        filters=draw(st.permutations(conditions + groups)),
        factors=factors,
        top_n=top_n,
        backups=draw(st.integers(0, 5)) if top_n else 0,
        period=draw(st.sampled_from(PERIODS)),
        every=draw(st.integers(1, 4)),
        weighting=weighting,
        formula=draw(st.sampled_from(NUMS)) if weighting == "formula" else None,
        band=draw(st.none() | NUMBERS | st.just(0)),
        anchor=draw(st.none() | DAYS),
        hold_rank=draw(st.none() | st.integers(top_n + 1, top_n + 30)) if top_n else None,
        max_weight=draw(st.none() | st.floats(1e-6, 1 - 1e-6) | st.just(0.5)),
        pool=pool,
    )


@st.composite
def backtests(draw) -> Backtest:
    strategy = draw(strategies())
    start, end = sorted(draw(st.lists(DAYS.filter(lambda d: d >= (strategy.anchor or d)), min_size=2, max_size=2)))
    return Backtest(
        strategy,
        start=draw(st.none() | st.just(start)),
        end=draw(st.none() | st.just(end)),
        cash=draw(st.integers(1, 10**9) | st.integers(1, 10**9).map(lambda fen: fen / 100)),
        execution=Execution(slippage_bps=draw(st.sampled_from([0.0, 10.0, 2.5]))),
        benchmark=draw(st.sampled_from([None, "000300.SH", "000905.SH"])),
        metrics=MetricSpec(draw(st.integers(1, 365))),
    )


def as_ints(form: object, floats: bool = False) -> object:
    """form with each whole float of a float field (weight, band, cash) written as an int."""
    if isinstance(form, dict):
        return {k: as_ints(v, k in ("weight", "band", "cash")) for k, v in form.items()}
    if isinstance(form, list):
        return [as_ints(v) for v in form]
    return int(form) if floats and isinstance(form, float) and form.is_integer() else form


@given(backtests())
def test_every_canonical_form_round_trips_and_ints_read_as_floats(backtest):
    form = json.loads(backtest.key)
    assert parse_form(Backtest, to_form(backtest)) == backtest
    assert canonical(to_form(parse_form(Backtest, form))) == canonical(form) == backtest.key
    assert canonical(to_form(parse_form(Backtest, as_ints(form)))) == backtest.key


def mom_form(**changes) -> dict:
    form = json.loads(Backtest(MOM, execution=EX0).key)
    for path, value in changes.items():
        *parents, leaf = path.split("__")
        target = form
        for name in parents:
            target = target[int(name)] if isinstance(target, list) else target[name]
        target[leaf] = value
    return form


GROUPED = json.loads(Strategy(filters=(Group("g", (Condition("a", CLOSE > 5.0),)),)).key)


@pytest.mark.parametrize(
    "form",
    [
        mom_form(extra=1),
        mom_form(strategy__extra=1),
        mom_form(strategy__factors__0__extra=1),
        mom_form(execution__foo=1),
        mom_form(metrics__foo=1),
        {"strategy": GROUPED | {"filters": [{**GROUPED["filters"][0], "extra": 1}]}},
        {"strategy": {"filters": [{"name": "a", "expr": ["GT", "close", 5.0], "extra": 1}]}},
        {"strategy": {"filters": [{"name": "a", "expr": ["GT", "close", 5.0], "conditions": []}]}},
        {"strategy": {"filters": [{"name": "a"}]}},
        mom_form(execution=[1]),
        mom_form(metrics="default"),
        mom_form(strategy=None),
        {},
        [],
        mom_form(strategy__factors={"name": "mom"}),
        mom_form(strategy__universe=["ADD", "close", 1.0]),
        mom_form(strategy__factors__0__weight=True),
        mom_form(strategy__factors__0__weight="1"),
        mom_form(strategy__factors__0__weight=10**400),
        mom_form(strategy__top_n=2.0),
        mom_form(strategy__anchor="2024-13-01"),
        mom_form(strategy__anchor=20240102),
        mom_form(cash=None),
        mom_form(execution__price=1),
        mom_form(metrics__periods_per_year=True),
        mom_form(start="2024-01-02", end="2023-01-02"),
    ],
)
def test_bad_forms_raise_value_error_never_type_error(form):
    with pytest.raises(ValueError):
        Backtest.from_form(form)


def test_an_expression_error_keeps_its_code_and_path_at_its_location():
    with pytest.raises(SpecErrors) as raised:
        Backtest.from_form(mom_form(strategy__factors__0__expr=["GT", ["MA", "adj_close", 0], 10]))
    [error] = raised.value.errors
    assert (error.code, error.loc, error.path) == ("out_of_range", ("strategy", "factors", 0, "expr"), (1, 2))
    with pytest.raises(SpecErrors, match="unknown operator") as raised:
        Strategy.from_form({"universe": ["ZAP", "close"]})
    assert [(e.code, e.loc, e.path) for e in raised.value.errors] == [("unknown_operator", ("universe",), ())]
    with pytest.raises(SpecErrors) as raised:
        Strategy.from_form({"filters": [{"name": "a", "expr": ["REF", "close", -1]}]})
    assert raised.value.errors[0].code == "negative_window"


def test_every_field_error_of_a_form_is_raised_at_once_in_the_forms_order():
    form = {
        "top_n": "5",
        "pool": {"exclud_st": True, "boards": ["main", "bse", "main"]},
        "filters": [{"name": "a"}, {"name": "", "expr": ["GT", "close", 1]}, {"name": "g", "conditions": []}],
        "periodd": "week",
        "max_weight": 1,
    }
    with pytest.raises(SpecErrors) as raised:
        Strategy.from_form(form)
    assert [(e.code, e.loc) for e in raised.value.errors] == [
        ("wrong_type", ("top_n",)),
        ("unknown_key", ("pool", "exclud_st")),
        ("not_a_choice", ("pool", "boards", 1)),
        ("missing_key", ("filters", 0, "expr")),
        ("out_of_range", ("filters", 1, "name")),
        ("out_of_range", ("filters", 2, "conditions")),
        ("unknown_key", ("periodd",)),
        ("out_of_range", ("max_weight",)),
    ]
    assert str(raised.value).startswith("top_n: '5' is not of type int; pool.exclud_st: Pool has no field")


def test_rules_across_fields_follow_once_every_field_reads_and_stop_at_the_first():
    with pytest.raises(SpecErrors) as raised:
        Strategy.from_form({"top_n": 2, "backups": 1, "hold_rank": 2})
    assert [(e.code, e.loc) for e in raised.value.errors] == [("requires", ("top_n",))]
    form = {"factors": [{"name": "a", "expr": "close"}, {"name": "a", "expr": "open"}], "top_n": 2, "hold_rank": 2}
    with pytest.raises(SpecErrors) as raised:
        Strategy.from_form(form)
    assert [(e.code, e.loc) for e in raised.value.errors] == [("duplicate_name", ("factors", 1, "name"))]


@pytest.mark.parametrize(
    "form, code, loc",
    [
        ({"filters": [5]}, "wrong_type", ("filters", 0)),
        ({"filters": {"name": "a"}}, "wrong_type", ("filters",)),
        ({"anchor": "2024-13-01"}, "wrong_type", ("anchor",)),
        ({"band": 10**400}, "out_of_range", ("band",)),
        ({"pool": []}, "wrong_type", ("pool",)),
        ({"pool": {"index": "000300.SZ"}}, "not_a_choice", ("pool", "index")),
        ({"pool": {"boards": []}}, "out_of_range", ("pool", "boards")),
        ({"pool": {"boards": ["main", "main"]}}, "duplicate_value", ("pool", "boards", 1)),
        ({"pool": {"min_list_days": 1}}, "out_of_range", ("pool", "min_list_days")),
        ({"filters": [{"name": "a", "expr": "close"}]}, "needs_condition", ("filters", 0, "expr")),
        (
            {"filters": [{"name": "g", "conditions": [{"name": "c"}]}]},
            "missing_key",
            ("filters", 0, "conditions", 0, "expr"),
        ),
        ({"filters": [{"name": "a", "expr": "st", "mode": "all"}]}, "unknown_key", ("filters", 0, "mode")),
        (5, "wrong_type", ()),
    ],
)
def test_a_form_error_has_its_code_and_location(form, code, loc):
    with pytest.raises((SpecError, SpecErrors)) as raised:
        Strategy.from_form(form)
    assert [(e.code, e.loc) for e in raised.value.errors] == [(code, loc)]
