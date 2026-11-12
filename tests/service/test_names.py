"""data names ("补充股票名称"): stock_basic's names and official dates for the current version, written only on
commit. Synthetic homes only."""

import polars as pl
import pytest

from wequant import service
from wequant.data.store import Store, version_reason
from wequant.service import Context, NamesRequest, ServiceError, data_names


def test_a_dry_run_counts_and_writes_nothing(named_home):
    report = data_names(NamesRequest(), Context(named_home))
    counts = (report.committed, report.codes, report.listed, report.named_before, report.named_after, report.delisted)
    assert counts == (None, 4, 2, 0, 2, 0) and Store(named_home).versions() == [1]


def test_commit_writes_them_in_one_new_version_whose_reason_is_names(named_home):
    report = data_names(NamesRequest(commit=True), Context(named_home))
    store = Store(named_home)
    names = dict(store.read_table("securities").select("code", "name").rows())
    assert (report.committed, version_reason(store.manifest())["code"]) == (2, "names")
    assert names == {"000001.SZ": None, "300001.SZ": "股票丙", "600000.SH": "股票甲", "830001.BJ": None}


def test_committing_again_makes_no_version(named_home):
    data_names(NamesRequest(commit=True), Context(named_home))
    assert data_names(NamesRequest(commit=True), Context(named_home)).committed is None
    assert Store(named_home).versions() == [1, 2]


def test_results_name_each_stock_as_the_newest_version_does(market_home, cache):
    """Screen, explain, a run's day and orders and data bars, read on version 1, name stocks as version 2 does."""
    store = Store(market_home)
    renamed = store.read_table("securities").with_columns(name=pl.lit("名") + pl.col("code").str.slice(0, 6))
    store.commit({}, replace={"securities": renamed})
    expected, context = dict(renamed.select("code", "name").rows()), Context(market_home, cache)
    old = {"saved": {"id": "m"}, "data_version": 1}
    screened = service.screen(service.ScreenRequest(**old), context)
    explained = service.explain(service.ExplainRequest(**old, code="600004.SH", date="2024-01-11"), context)
    run = service.backtest(service.BacktestRequest(**old), context)
    day = service.runs_day(service.RunDayRequest(id=run.run_id, date=run.end), context)
    orders = service.runs_orders(service.OrdersRequest(id=run.run_id), context)
    bars = service.data_bars(service.BarsRequest(codes=["600004.SH"], data_version=1), context)
    rows = [*screened.rows, *screened.rejects, *day.positions, *day.decisions, *orders.rows, explained]
    named = [(row.code, row.name) for row in rows] + list(bars.names.items())
    assert len(named) > 10 and all(name == expected[code] for code, name in named)


def test_without_a_stock_basic_file_there_is_nothing_to_take(home):
    with pytest.raises(ServiceError) as caught:
        data_names(NamesRequest(), Context(home))
    assert [d.code for d in caught.value.detail] == ["no_stock_basic"]
