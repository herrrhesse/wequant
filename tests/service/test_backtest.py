"""backtest on the synthetic market (tests.service.conftest.MARKET), traded daily."""

import datetime as dt

import polars as pl
import pytest

from tests.backtest.market import build_store
from tests.service.conftest import DAYS, MARKET, SCREENED
from wequant.runs.archive import Archive
from wequant.service import Context, Exit, ServiceError
from wequant.service.backtest import BacktestRequest, backtest, order_counts
from wequant.service.screen import ScreenRequest, screen
from wequant.strategy import Strategy

DAILY = {**SCREENED, "period": "day"}
D = [dt.date.fromisoformat(day) for day in DAYS]


def run(home, **request):
    return backtest(BacktestRequest(**{"strategy": DAILY, **request}), Context(home))


def test_each_decision_is_what_screen_shows_for_that_day(market_home):
    """Without hold_rank the targets, weights and backups do not depend on the holdings (design 11.3)."""
    result = run(market_home)
    picks = Archive(market_home / "runs").load(result.run_id).picks
    assert picks.height > 0
    for day, rows in picks.group_by("date", maintain_order=True):
        shown = screen(ScreenRequest(strategy=DAILY, date=day[0]), Context(market_home)).rows
        expected = [(r.code, r.role, r.weight) for r in shown if r.role != "none"]
        assert rows.select("code", "role", "weight").rows() == expected


def test_a_run_is_archived_under_its_id_unless_asked_not_to(market_home, tmp_path):
    archive = Archive(market_home / "runs")
    kept = run(market_home)
    assert kept.saved and kept.run_id in archive
    other = run(market_home, cash=500_000.0, save=False)
    assert not other.saved and other.run_id not in archive and other.report is None


def test_the_delisted_holdings_a_run_handled_are_listed(tmp_path):
    """600004.SH ranks first from day 3, is bought on day 4, and its data ends there: written down at day 5's open."""
    market = {**MARKET, "600004.SH": MARKET["600004.SH"][:5] + [None] * 3}
    store = build_store(tmp_path / "m", DAYS, market, index=("000300.SH", [3000.0] * 8))
    ended = pl.when(pl.col("code") == "600004.SH").then(pl.lit(D[4])).otherwise(pl.col("last_date"))
    store.commit({}, replace={"securities": store.read_table("securities").with_columns(last_date=ended)})
    result = run(store.home, save=False)
    assert [(r.date, r.code, r.treatment) for r in result.delisted] == [(D[5], "600004.SH", "write_down")]
    assert result.orders.forced == {"delist_write_down": 1} and "delisting_approximate" in [
        w.code for w in result.warnings
    ]


def test_forced_rows_are_counted_apart_from_rebalance_orders():
    orders = pl.DataFrame(
        {
            "status": ["filled", "partial", "rejected", "rejected", "rejected", "filled"],
            "reason": [None, "cash", "limit_up", "limit_up", "no_bar", "delist_write_down"],
        }
    )
    counts = order_counts(orders)
    assert (counts.total, counts.filled, counts.partial, counts.rejected) == (5, 1, 1, 3)
    assert counts.rejected_by_reason == {"limit_up": 2, "no_bar": 1} and counts.forced == {"delist_write_down": 1}


FAILURES = [
    ({"start": "2023-12-29"}, "out_of_range", ["start"]),
    ({"cash": 1000.001}, "out_of_range", ["cash"]),
    ({"execution": {"delisting": "hold", "failure_recovery": 0.5}}, "requires", ["execution", "failure_recovery"]),
    ({"strategy": {**DAILY, "anchor": "2024-01-06"}}, "not_a_trading_day", ["strategy", "anchor"]),
    ({"strategy": None, "saved": {"id": "anchored"}}, "not_a_trading_day", ["saved", "anchor"]),
    ({"benchmark": "000905.SH"}, "no_data", ["benchmark"]),
]


@pytest.mark.parametrize("request_, code, loc", FAILURES, ids=[f"{case[1]} {case[2]}" for case in FAILURES])
def test_what_backtest_refuses_is_located_in_the_request(market_home, request_, code, loc):
    Context(market_home).library().save(Strategy.from_form({**DAILY, "anchor": "2024-01-06"}), "anchored")
    with pytest.raises(ServiceError) as raised:
        run(market_home, **request_)
    assert raised.value.exit == Exit.USAGE and [(d.code, d.loc) for d in raised.value.detail] == [(code, loc)]


def test_a_cancelled_run_is_interrupted_and_leaves_nothing(market_home):
    with pytest.raises(ServiceError) as raised:
        backtest(BacktestRequest(strategy=DAILY), Context(market_home, cancel=lambda: True))
    assert raised.value.exit == Exit.INTERRUPTED and not (market_home / "runs").exists()


def test_progress_reaches_the_last_day(market_home):
    seen = []
    backtest(BacktestRequest(strategy=DAILY, save=False), Context(market_home, progress=seen.append))
    assert (seen[-1].done, seen[-1].total, seen[-1].day) == (7, 7, D[-1])
