"""Run warnings: ST-band fills, the ST state known, stale holdings, unchecked limits, and their fixed order."""

import datetime as dt
from dataclasses import replace

import polars as pl
import pytest

from tests.backtest.market import WEEK, A, B, build_store, business_days
from tests.frames import table_frame
from wequant.account import Execution
from wequant.backtest import WARNINGS, Backtest, run
from wequant.backtest import warnings as warnings_module
from wequant.backtest.warnings import st_band_fills
from wequant.expr import Field
from wequant.panel import load_panel
from wequant.strategy import Condition, Factor, Pool, Strategy

EX0 = Execution(slippage_bps=0.0)
CHINEXT = "300001.SZ"
ONE = Strategy(factors=(Factor("one", Field("close")),), top_n=1, period="day", every=1000)
NOT_ST = Strategy(filters=(Condition("not_st", ~Field("st")),))
JAN4, JAN5 = dt.date(2024, 1, 4), dt.date(2024, 1, 5)


def panel_of(home, days, bars, **market):
    store = build_store(home / "home", days, bars, **market)
    return load_panel(store=store, cache_dir=home / "cache")


def test_st_band_fills_count_fills_at_the_narrower_band_where_st_is_unknown(tmp_path):
    days = ["2024-03-04", "2024-03-05", "2026-07-06", "2026-07-07"]
    bars = [(10.0, 10.0), (10.5, 10.5, 10.0), (10.0, 10.0, 10.0), (10.5, 10.5, 10.0)]
    panel = panel_of(tmp_path, days, {A: bars, CHINEXT: bars}, boards={CHINEXT: "chinext"})
    fills = [
        ("2024-03-05", A, "buy", 100.0, 10.50),  # +5% of 10.00: counts
        ("2024-03-05", A, "buy", 100.0, 10.49),
        ("2024-03-05", A, "sell", 100.0, 9.50),  # -5%: counts
        ("2024-03-05", A, "sell", 0.0, 9.50),  # not a fill
        ("2024-03-05", CHINEXT, "buy", 100.0, 10.50),  # not the main board
        ("2026-07-07", A, "buy", 100.0, 10.50),  # the ST band equals the normal band from 2026-07-06
    ]
    schema = {"date": pl.String, "code": pl.String, "side": pl.String, "shares": pl.Float64, "price": pl.Float64}
    orders = pl.DataFrame(fills, schema=schema, orient="row").with_columns(pl.col("date").str.to_date())
    assert st_band_fills(panel, orders) == (1, 1)


def test_st_unknown_reports_the_share_of_universe_cells_with_a_known_state(tmp_path):
    days = ["2024-03-01", "2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]
    days += ["2024-03-11", "2024-03-12"]
    status = [{"code": A, "date": day, "tradestatus": 1, "is_st": False} for day in ("2024-03-01", "2024-03-08")]
    panel = panel_of(tmp_path, days, {A: [(10.0, 10.0)] * 8, "600004.SH": [(8.0, 8.0)] * 8}, status=status)
    result = run(Backtest(NOT_ST, execution=EX0, benchmark=None), panel)
    assert result.funnel["date"].unique().to_list() == [dt.date(2024, 3, 1), dt.date(2024, 3, 8)]
    assert result.warnings["st_unknown"] == (
        "ST state known on 50.0% of the universe cells on decision days; unknown reads as not ST",
        {"known_share": 0.5},
    )
    assert "st_unknown" not in run(Backtest(ONE, execution=EX0, benchmark=None), panel).warnings


def stale(tmp_path, days):
    bars = {A: [(10.0, 10.0), (10.3, 10.0), (10.0, 10.0)] + [None] * (days - 3)}
    panel = panel_of(tmp_path, business_days(days), bars)
    return run(Backtest(ONE, cash=100_000, execution=EX0, benchmark=None), panel)


def test_a_holding_without_a_bar_for_more_than_20_days_is_reported(tmp_path):
    result = stale(tmp_path, 25)
    assert result.ledger.orders.select("date", "shares", "price").rows() == [(dt.date(2024, 1, 3), 9700.0, 10.3)]
    assert result.ledger.daily["equity"][-1] == pytest.approx(97_059.03)
    text, params = result.warnings["stale_holdings"]
    assert text == (
        "1 codes were held more than 20 trading days after their last bar, valued at their last close: at most 99.9% "
        "of equity on 2024-02-02, 99.9% on the last day 2024-02-05"
    )  # stale from row 23, the first row with no bar in the 21 rows up to it; the first worst day is named
    share = 97_000 / 97_059.03
    assert params == {
        "codes": 1,
        "stale_days": 20,
        "share": pytest.approx(share),
        "day": "2024-02-05",
        "worst_share": pytest.approx(share),
        "worst_day": "2024-02-02",
    }


def test_a_holding_stale_in_the_middle_of_the_run_is_reported_though_it_trades_again(tmp_path):
    bars = {A: [(10.0, 10.0), (10.3, 10.0), (10.0, 10.0)] + [None] * 23 + [(10.0, 10.0)] * 5}
    panel = panel_of(tmp_path, business_days(31), bars)
    result = run(Backtest(ONE, cash=100_000, execution=EX0, benchmark=None), panel)
    text, params = result.warnings["stale_holdings"]
    assert (params["codes"], params["worst_day"], params["share"]) == (1, "2024-02-02", 0.0)  # rows 23 to 25
    assert params["worst_share"] == pytest.approx(97_000 / 97_059.03) and params["day"] == "2024-02-13"
    assert text.endswith("at most 99.9% of equity on 2024-02-02, 0.0% on the last day 2024-02-13")


def test_twenty_days_without_a_bar_are_not_stale(tmp_path):
    result = stale(tmp_path, 23)
    assert result.ledger.daily["date"][-1] == dt.date(2024, 2, 1) and "stale_holdings" not in result.warnings


def test_limits_unchecked_goes_once_the_limits_are_checked(tmp_path, monkeypatch):
    result = stale(tmp_path / "before", 23)
    assert list(result.warnings) == ["limits_unchecked"]
    monkeypatch.setattr(warnings_module, "limit_check_current", lambda manifest: True)
    assert stale(tmp_path / "after", 23).warnings == {}


def test_warnings_keep_the_order_of_warnings(tmp_path):
    never = Strategy(filters=(Condition("never", Field("close") < 0.0), Condition("not_st", ~Field("st"))))
    result = run(
        Backtest(never, execution=EX0, benchmark=None), panel_of(tmp_path, business_days(6), {A: [(1.0, 1.0)] * 6})
    )
    assert list(result.warnings) == ["limits_unchecked", "st_unknown", "no_picks"]
    assert list(result.warnings) == [code for code in WARNINGS if code in result.warnings]


def test_index_uncovered_counts_the_decision_days_outside_each_index_span(tmp_path):
    store = build_store(tmp_path / "home", WEEK, {A: [(10.0, 10.0)] * 5, B: [(20.0, 20.0)] * 5})
    members = [{"index_code": "000905.SH", "code": code, "start_date": WEEK[2], "end_date": WEEK[4]} for code in (A, B)]
    store.commit({"source": "test"}, replace={"index_members": table_frame("index_members", members)})
    panel = load_panel(store=store, cache_dir=tmp_path / "cache")
    csi500 = Strategy(pool=Pool(index="000905.SH"), period="day")
    result = run(Backtest(csi500, execution=EX0, benchmark=None), panel)
    assert result.picks.filter(pl.col("role") == "target")["date"].unique().sort().to_list() == [JAN4, JAN5]
    assert result.warnings["index_uncovered"] == (
        "member@000905.SH: 2 decision days outside its index_members span from 2024-01-04",
        {"index": "000905.SH", "days": 2, "first": "2024-01-04"},
    )
    both = replace(csi500, universe=~Field("exchange@BJ") & Field("member@000852.SH"))
    assert run(Backtest(both, execution=EX0, benchmark=None), panel).warnings["index_uncovered"] == (
        "member@000852.SH: 4 decision days outside its index_members span, which is empty; "
        "member@000905.SH: 2 decision days outside its index_members span from 2024-01-04",
        {"index": "000852.SH", "days": 4, "first": None, "index_2": "000905.SH", "days_2": 2, "first_2": "2024-01-04"},
    )
    assert (
        "index_uncovered"
        not in run(Backtest(replace(csi500, pool=Pool()), execution=EX0, benchmark=None), panel).warnings
    )


def test_list_days_approximate_counts_the_codes_starting_with_the_calendar_and_their_cells_left_out(tmp_path):
    store = build_store(tmp_path / "home", WEEK, {A: [(10.0, 10.0)] * 5, B: [(20.0, 20.0)] * 5})
    seasoned = Strategy(pool=Pool(min_list_days=3), period="day")
    result = run(Backtest(seasoned, execution=EX0, benchmark=None), load_panel(store=store, cache_dir=tmp_path / "a"))
    text = (
        "list_days counts trading days from securities.first_date, the first day of the source's data, not the listing "
        "date: a lower bound for the 2 codes whose data starts with the calendar, which min_list_days left out of {} "
        "decision-day cells"
    )
    assert result.warnings["list_days_approximate"] == (text.format(4), {"codes_at_calendar_start": 2, "cells": 4})
    later = replace(seasoned, anchor=dt.date(2024, 1, 3))
    panel = load_panel(WEEK[1], store=store, cache_dir=tmp_path / "b")
    assert run(Backtest(later, execution=EX0, benchmark=None), panel).warnings["list_days_approximate"][0] == (
        text.format(2)
    )
    seasoned_expr = Strategy(filters=(Condition("seasoned", Field("list_days") > 1),), period="day")
    assert run(Backtest(seasoned_expr, execution=EX0, benchmark=None), panel).warnings["list_days_approximate"][0] == (
        text.format(0)
    )
