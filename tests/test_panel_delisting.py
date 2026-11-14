"""The delisting period on the panel (S2.10a): the last N bars of a stock whose final name carries 退, N by the rules
in force on its last bar; suspended days not counted."""

import datetime as dt

import numpy as np
import polars as pl

from tests.backtest.market import build_store, business_days
from wequant.panel import load_panel

A, B = "600000.SH", "600001.SH"


def panel(tmp_path, first, n, names, gap=None):
    """A and B trading n weekdays from first (A suspended on day index gap), both delisted after their last bar."""
    days = business_days(n, first)
    bars = {A: [None if t == gap else (10.0, 10.0) for t in range(n)], B: [(20.0, 20.0)] * n}
    store = build_store(tmp_path / "home", days, bars)
    delisted = dt.date.fromisoformat(days[-1]) + dt.timedelta(days=3)
    securities = store.read_table("securities").with_columns(
        name=pl.col("code").replace_strict(names, default=None), delist_date=pl.lit(delisted)
    )
    store.commit({}, replace={"securities": securities}, reason="unspecified")
    return load_panel(store=store, cache_dir=tmp_path / "cache")


def col(found, code):
    return list(found.codes).index(code)


def test_the_last_15_bars_of_a_stock_named_with_tui_since_2021_are_its_delisting_period(tmp_path):
    found = panel(tmp_path, "2024-01-02", 20, {A: "甲退", B: "乙股份"}, gap=17)
    days = found.delisting[:, col(found, A)].tolist()
    assert days == [0, 0, 0, 0, *range(1, 14), 0, 14, 15]  # day index 17 suspended: no bar, not counted
    assert (found.delisting[:, col(found, B)] == 0).all() and found.delisting_days[col(found, A)] == 15


def test_a_delisting_before_2021_has_30_days_and_one_before_july_2012_none(tmp_path):
    old = panel(tmp_path / "2019", "2019-03-01", 35, {A: "退市甲", B: "乙退"})
    assert old.delisting[:, col(old, A)].tolist() == [0] * 5 + list(range(1, 31))
    early = panel(tmp_path / "2011", "2011-03-01", 35, {A: "退市甲", B: "乙退"})
    assert not np.any(early.delisting)


def test_a_star_st_stock_without_tui_in_its_name_has_no_delisting_period(tmp_path):
    found = panel(tmp_path, "2024-01-02", 20, {A: "*ST甲", B: "乙股份"})
    assert not np.any(found.delisting)
