"""Hand-computed runs of the hold buffer and the weight cap: which week a holding is sold, and the cash a cap keeps."""

import datetime as dt
from dataclasses import replace

import polars as pl
import pytest

from tests.backtest.market import WEEK, build_store, business_days
from wequant.account import Execution
from wequant.backtest import Backtest, run
from wequant.expr import Field
from wequant.panel import load_panel
from wequant.strategy import Factor, Strategy

EX0 = Execution(slippage_bps=0.0)
A, B, C = "600000.SH", "000001.SZ", "600004.SH"
DAYS = business_days(21, "2024-01-01")  # Monday to the Monday four weeks later; Fridays are rows 4, 9, 14 and 19
WEEKS = [  # A, B and C's close (and open) from each row on: A ranks 1, 2, 1, 2, then 3 at the decision rows
    (0, (10.2, 10.1, 10.0)),
    (4, (10.1, 10.2, 10.0)),
    (9, (10.2, 10.1, 10.0)),
    (14, (10.1, 10.2, 10.0)),
    (19, (10.0, 10.1, 10.2)),
]
LEVEL = Strategy(factors=(Factor("level", Field("close")),), top_n=1, hold_rank=2)
JAN = {n: dt.date(2024, 1, n) for n in range(1, 32)}


def levels(row: int) -> tuple[float, float, float]:
    return [prices for start, prices in WEEKS if start <= row][-1]


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    home = tmp_path_factory.mktemp("hold_rank")
    bars = {code: [(levels(t)[k],) * 2 for t in range(len(DAYS))] for k, code in enumerate((A, B, C))}
    return load_panel(store=build_store(home / "home", DAYS, bars), cache_dir=home / "cache")


def trades(result):
    return result.ledger.orders.select("date", "code", "side").rows()


def decisions(result, code):
    columns = ("date", "rank", "held", "action", "sell_kind", "sell_rank", "sell_limit")
    return result.detail.filter(pl.col("code") == code).select(columns).rows()


def test_a_holding_ranked_within_hold_rank_is_kept_until_it_drops_below(panel):
    result = run(Backtest(LEVEL, cash=100_000, execution=EX0, benchmark=None), panel)
    assert trades(result) == [(JAN[2], A, "buy"), (JAN[29], A, "sell"), (JAN[29], C, "buy")]
    first = result.ledger.orders.row(0, named=True)
    assert (first["shares"], first["price"], first["commission"], first["transfer_fee"]) == (9800, 10.2, 29.99, 1.0)
    held = result.ledger.daily.filter(pl.col("date") < JAN[29])
    assert held["equity"].to_list()[1:] == pytest.approx([9.01 + 9800 * levels(t)[0] for t in range(1, 20)])
    assert decisions(result, A) == [
        (JAN[1], 1, False, "buy", None, None, None),
        (JAN[5], 2, True, "keep", None, None, None),
        (JAN[12], 1, True, "keep", None, None, None),
        (JAN[19], 2, True, "keep", None, None, None),
        (JAN[26], 3, True, "sell", "rank", 3, 2),
    ]
    assert result.picks.filter(pl.col("date") == JAN[19]).select("code", "rank").rows() == [(A, 2)]


def test_without_hold_rank_the_holding_is_sold_the_week_it_ranks_second(panel):
    result = run(Backtest(replace(LEVEL, hold_rank=None), cash=100_000, execution=EX0, benchmark=None), panel)
    assert trades(result) == [
        (JAN[2], A, "buy"),
        (JAN[8], A, "sell"),
        (JAN[8], B, "buy"),
        (JAN[15], B, "sell"),
        (JAN[15], A, "buy"),
        (JAN[22], A, "sell"),
        (JAN[22], B, "buy"),
        (JAN[29], B, "sell"),
        (JAN[29], C, "buy"),
    ]
    assert decisions(result, A)[1] == (JAN[5], 2, True, "sell", "rank", 2, 1)


def test_a_cap_no_target_can_take_keeps_cash_and_says_so(tmp_path):
    bars = {A: [(10.0, 10.0), (10.0, 10.2), (10.2, 10.1), (10.1, 10.3)], B: [(20.0, 20.0)] * 4}
    panel = load_panel(store=build_store(tmp_path / "home", WEEK[:4], bars), cache_dir=tmp_path / "cache")
    capped = Strategy(factors=(Factor("level", Field("close")),), top_n=2, weighting="score", period="day")
    result = run(Backtest(replace(capped, max_weight=0.4), cash=100_000, execution=EX0, benchmark=None), panel)
    picks = result.picks.select("date", "code", "rank", "score", "weight", "raw_weight", "capped").rows()
    assert picks == [
        (day, code, rank, score, 0.4, raw, True)
        for day in (JAN[2], JAN[3], JAN[4])
        for code, rank, score, raw in ((B, 1, 1.0, 2 / 3), (A, 2, 0.5, 1 / 3))
    ]
    assert result.warnings["max_weight_cash"] == (
        "3 decision days kept 20.0% of equity in cash on average: max_weight capped every target"
    )
    bought = result.ledger.orders.filter(pl.col("date") == JAN[3]).select("code", "shares").rows()
    assert bought == [(B, 2000.0), (A, 4000.0)]  # 0.4 of 100,000 each: 40,000 / 20.0 and 40,000 / 10.0
    assert "max_weight_cash" not in run(Backtest(capped, cash=100_000, execution=EX0, benchmark=None), panel).warnings
