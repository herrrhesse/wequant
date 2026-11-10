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
from wequant.expr import REF, STD, ExprError, Field, LookAheadError
from wequant.strategy import MODES, PERIODS, PREFERS, SH_SZ, Condition, Factor, Group, Strategy, parse_form, to_form

ADJ, CLOSE, OPEN, ST, VOLUME = Field("adj_close"), Field("close"), Field("open"), Field("st"), Field("volume")
EX0 = Execution(slippage_bps=0.0)
MOM = Strategy(factors=(Factor("mom", ADJ / REF(ADJ, 1)),), top_n=2)
EXECUTION = '{"commission_rate":0.0003,"min_commission":5.0,"price":"open","slippage_bps":0.0}'
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


def test_golden_backtest_keys_write_execution_and_metrics_in_full():
    key = f'{{"execution":{EXECUTION},"metrics":{METRICS},"strategy":{MOM_KEY}}}'
    assert Backtest(MOM, execution=EX0).key == key
    assert Backtest(MOM, execution=Execution(slippage_bps=-0.0)).key == key
    ret1 = Strategy(factors=(Factor("ret1", ADJ / REF(ADJ, 1)),), top_n=1, period="day")
    assert Backtest(ret1, cash=100_000, execution=EX0).key == (
        f'{{"cash":100000.0,"execution":{EXECUTION},"metrics":{METRICS},"strategy":{{"factors":[{{"expr":["DIV",'
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


def test_expression_errors_pass_through():
    with pytest.raises(LookAheadError):
        Backtest.from_form(mom_form(strategy__factors__0__expr=["REF", "adj_close", -1]))
    with pytest.raises(ExprError, match="unknown operator"):
        Backtest.from_form(mom_form(strategy__factors__0__expr=["ZAP", "adj_close"]))
