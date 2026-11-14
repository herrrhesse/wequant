"""A strategy whose scope reaches beyond the data's (S2.11): it runs, with a warning that says which stocks the data
holds and from which day; a saved run keeps the warning it had."""

import datetime as dt

import polars as pl

from tests.service.conftest import DAYS, SCREENED
from wequant import service
from wequant.data.scope import ALL, Layer, Scope, as_meta
from wequant.data.store import Store
from wequant.service import Context
from wequant.service.scopes import beyond_warnings
from wequant.strategy import Strategy

CSI300 = Scope(indexes=("000300.SH",))
EVERY, IN_CSI300 = Strategy.from_form(SCREENED), Strategy.from_form(SCREENED | {"pool": {"index": "000300.SH"}})
D1, D2 = dt.date(2023, 11, 15), dt.date(2025, 1, 2)
FIRST = dt.date.fromisoformat(DAYS[0])  # the market fixture's first day


def codes(warnings) -> list[str]:
    return [w.code for w in warnings]


def test_a_scope_the_data_does_not_hold_warns_and_one_it_holds_does_not():
    held = [Layer(CSI300, D1)]
    beyond = beyond_warnings(EVERY, held, D2)
    assert codes(beyond) == ["scope_beyond_data"] and beyond[0].params == {"scope": "沪深全部", "data": "沪深300"}
    assert beyond_warnings(IN_CSI300, held, D2) == []


def test_a_scope_held_only_from_a_later_day_warns_for_a_start_before_it():
    held = [Layer(ALL, D2), Layer(CSI300, D1)]
    before = beyond_warnings(EVERY, held, D1)
    assert codes(before) == ["scope_beyond_data_before"]
    assert before[0].params == {"scope": "沪深全部", "data": "沪深300", "since": "2025-01-02"}
    assert beyond_warnings(EVERY, held, D2) == []


def test_a_saved_run_keeps_its_data_scope_warning_once_the_data_covers_its_scope(market_home, cache):
    store, context = Store(market_home), Context(market_home, cache)
    store.commit(as_meta([Layer(CSI300, FIRST)]), reason="unspecified")
    run = service.backtest(service.BacktestRequest(strategy=SCREENED, benchmark=None), context)
    store.commit(as_meta([Layer(ALL, FIRST)]), reason="unspecified")
    shown = service.runs_show(service.RunRequest(id=run.run_id), context)
    assert "scope_beyond_data" in codes(run.warnings) and "scope_beyond_data" in codes(shown.warnings)


def test_an_addition_the_first_layer_holds_is_covered_and_one_held_later_counts_its_early_stocks(market_home, cache):
    """Another layer still reaching back does not make a covered index uncovered; every stock held only from a later
    day is a stock to fetch (S2.11, frontend's report on the layered demo)."""
    store, context, later = Store(market_home), Context(market_home, cache), dt.date.fromisoformat(DAYS[3])
    store.commit(as_meta([Layer(ALL, later), Layer(CSI300, FIRST)]), reason="unspecified")
    held = service.data_extend_estimate(service.ExtendEstimateRequest(scope={"indexes": ["000300.SH"]}), context)
    early = service.data_extend_estimate(service.ExtendEstimateRequest(scope={"all": True}), context)
    assert (held.covered, held.stocks, held.seconds) == (True, 0, 0)
    shsz = store.read_table("securities").filter(~pl.col("code").str.ends_with(".BJ")).height
    assert (early.covered, early.stocks) == (False, shsz) and early.seconds > 0
