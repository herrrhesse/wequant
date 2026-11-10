"""Each funnel level's rejected stocks on a hand-made day: every listed stock once, counts that match the funnel, the
verdict and the value side of a simple comparison."""

import numpy as np
import polars as pl
import pytest

from tests.frames import table_frame
from wequant.data.store import Store
from wequant.expr import BETWEEN, REF, Field
from wequant.panel import load_panel
from wequant.strategy import Comparison, Condition, Factor, Group, Pool, Strategy, comparison, levels, screen, select

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]
CLOSE, OPEN = Field("close"), Field("open")
STOCKS = {  # code: (first day, last day, bars as (open, close) or None, ST), the decision on day 1
    "600000.SH": (0, 2, [(10.1, 10.0)] * 3, False),  # scored
    "600004.SH": (0, 2, [(20.1, 20.0)] * 3, False),  # scored
    "600005.SH": (2, 2, [None, None, (9.1, 9.0)], False),  # not listed yet
    "600006.SH": (0, 2, [(9.1, 9.0), None, (9.1, 9.0)], False),  # no bar: suspended
    "600007.SH": (0, 2, [(3.1, 3.0)] * 3, False),  # out of the universe
    "600008.SH": (0, 2, [(8.1, 8.0)] * 3, True),  # ST
    "600009.SH": (0, 2, [(60.1, 60.0)] * 3, False),  # fails cheap at 60
    "600010.SH": (0, 2, [(7.0, 7.0)] * 3, False),  # fails both members of the group
    "600011.SH": (1, 2, [None, (6.1, 6.0), (6.1, 6.0)], False),  # first bar: no momentum
}
STRATEGY = Strategy(
    universe=~Field("exchange@BJ") & (CLOSE > 5.0),
    filters=(
        Condition("cheap", CLOSE < 50.0),
        Group("g", (Condition("drop", OPEN > CLOSE), Condition("busy", Field("volume") > 2e6)), mode="any"),
    ),
    factors=(Factor("mom", Field("adj_close") / REF(Field("adj_close"), 1)),),
    top_n=1,
    pool=Pool(exclude_st=True),
)


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("rejects")
    store = Store(home / "home")
    store.commit({"source": "test"}, replace=tables())
    return load_panel(store=store, cache_dir=home / "cache")


@pytest.fixture(scope="module")
def screened(panel):
    return screen(STRATEGY, panel, DAYS[1])


def tables() -> dict[str, pl.DataFrame]:
    securities = [
        {"code": code, "exchange": "SH", "board": "main", "first_date": DAYS[first], "last_date": DAYS[last]}
        for code, (first, last, _, _) in STOCKS.items()
    ]
    bars = [
        {"code": code, "date": day, **_prices(*bar)}
        for code, (_, _, seq, _) in STOCKS.items()
        for day, bar in zip(DAYS, seq)
        if bar is not None
    ]
    status = [
        {"code": code, "date": DAYS[t], "tradestatus": int(seq[t] is not None), "is_st": st}
        for code, (first, last, seq, st) in STOCKS.items()
        for t in range(first, last + 1)
    ]
    return {
        "calendar": table_frame("calendar", [{"date": day} for day in DAYS]),
        "securities": table_frame("securities", securities, source="test"),
        "bars": table_frame("bars", bars, volume=1_000_000, amount=1e7, preclose_derived=True, source="test"),
        "adj_factor": table_frame("adj_factor", [{"code": c, "ex_date": DAYS[0], "factor": 1.0} for c in STOCKS]),
        "daily_status": table_frame("daily_status", status, source="test"),
    }


def _prices(open_: float, close: float) -> dict[str, int]:
    prices = {"open": open_, "high": max(open_, close), "low": min(open_, close), "close": close, "preclose": close}
    return {name: round(value * 1000) for name, value in prices.items()}


def test_every_level_rejects_its_stock_once_with_its_verdict(screened):
    assert screened.rejects.rows() == [
        ("600006.SH", "bar", "bar", False, None, None),
        ("600007.SH", "universe", "universe", False, None, None),
        ("600008.SH", "exclude_st", "pool", False, None, None),
        ("600009.SH", "cheap", "condition", False, 60.0, None),
        ("600010.SH", "g", "group", False, None, None),
        ("600011.SH", "scored", "scored", None, None, "mom"),
    ]
    scored = screened.stocks.filter(pl.col("scored"))["code"].to_list()
    assert scored == ["600000.SH", "600004.SH"]
    listed = sorted(set(STOCKS) - {"600005.SH"})
    assert sorted(scored + screened.rejects["code"].to_list()) == listed


def test_the_rejects_of_each_level_are_what_its_left_count_drops(screened):
    left = dict(screened.funnel.select("step", "left").iter_rows())
    chain = [level.step for level in levels(STRATEGY)]
    assert chain == ["listed", "bar", "universe", "exclude_st", "cheap", "g", "scored"]
    assert [left[step] for step in chain] == [8, 7, 6, 5, 4, 3, 2]
    counts = dict(screened.rejects["step"].value_counts().iter_rows())
    assert all(left[before] - left[step] == counts.get(step, 0) for before, step in zip(chain, chain[1:]))
    assert left["drop"] is None and left["busy"] is None


@pytest.mark.parametrize(
    "expr, want",
    [
        (CLOSE > 5.0, Comparison(CLOSE, "gt", 5.0, None)),
        (5.0 > CLOSE, Comparison(CLOSE, "lt", 5.0, None)),
        (5.0 <= CLOSE, Comparison(CLOSE, "ge", 5.0, None)),
        (Field("amount") <= 1e8, Comparison(Field("amount"), "le", 1e8, None)),
        (BETWEEN(CLOSE, 9.0, 3.0), Comparison(CLOSE, "between", 3.0, 9.0)),
        (CLOSE > OPEN, None),
        (BETWEEN(CLOSE, OPEN, 3.0), None),
        (~Field("st"), None),
    ],
)
def test_a_simple_comparison_puts_its_constant_on_the_right(expr, want):
    assert comparison(expr) == want


def test_select_keeps_the_value_side_of_each_simple_comparison_when_asked(panel):
    kept = select(STRATEGY, panel, [1], values=True)
    assert list(kept.values) == ["cheap", "busy"]  # drop compares two expressions
    assert np.array_equal(kept.values["cheap"], panel.raw("close")[[1]], equal_nan=True)
    assert np.array_equal(kept.values["busy"], panel.raw("volume")[[1]], equal_nan=True)
    assert select(STRATEGY, panel, [1]).values == {}
